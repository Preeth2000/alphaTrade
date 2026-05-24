# Validation Gate Design

**Date:** 2026-05-24  
**Branch:** feat/db-first-overrides  
**Services:** alphaTrade (executor), producer service (separate Python service)

---

## Problem

Models trained in producer service are uploaded to MLflow and promoted to Production. Bad models (poor metrics, bad config) currently enter MLflow and waste disk space. No signal back to producer when executor rejects a model. No retrain mechanism.

## Decision Summary

- **Producer owns all metric validation.** Bad models never reach MLflow.
- **Executor does artifact integrity checks only.** No metric re-checking.
- **One set of thresholds**, owned by producer `.env`.
- **`model_deployments` DB table** is the signal channel from executor → producer.
- **Retrain is optional, not automatic.** Producer decides based on failure category + budget.

---

## Architecture

```
Producer Service                         alphaTrade (Executor)
────────────────                         ─────────────────────
train model
  │
run backtest → metrics available:
  sharpe, max_drawdown, hit_rate,
  n_trades, total_return, best_val_loss,
  accuracy, f1 (per class), confusion
  │
ProducerValidationGate
  all thresholds from producer .env
  │
  ├─ FAIL → log reason, never register
  │          decide retrain (see below)
  │
  └─ PASS → register to MLflow
             set "production" alias
                                         ModelSyncDaemon polls MLflow
                                           artifact integrity checks:
                                             run_id present?
                                             download artifacts (retry ×N)
                                             backtest.json present?
                                           PASS → promote to models_dir
                                           FAIL → model_deployments(failed)
                                                   ← producer polls this table
```

---

## alphaTrade Changes

### 1. Remove metric validation from executor

**File:** `alphaTrade/store/model_sync.py`

- Remove `ValidationGate` class entirely (or gut to no-op).
- Remove metric checks from `_sync_once`. Keep structural checks:
  1. `pv.run_id` present → else `permanently_fail`
  2. Download artifacts (retry up to `max_download_attempts`) → else `permanently_fail`
  3. `backtest.json` present → else `permanently_fail`
- On `permanently_fail`, write `model_deployments` row with `failure_msg`. This is the signal to producer.

### 2. Remove ValidationThresholds from config

**File:** `alphaTrade/config.py`

- Delete `ValidationThresholds` class.
- Remove `validation: ValidationThresholds` field from `ModelSyncConfig`.
- Existing defaults (`min_sharpe=0.5`, `max_drawdown=0.20`, `min_hit_rate=0.45`) move to producer config.

### 3. model_deployments table — no schema change needed

Table already has: `run_name`, `promoted_at`, `activated_at`, `failed_at`, `failure_msg`, `status`.  
Producer queries: `SELECT failure_msg, failed_at FROM model_deployments WHERE run_name = ? ORDER BY promoted_at DESC LIMIT 1`

---

## Producer Service Spec

> **Handoff note for next agent:** This section is self-contained. The producer is a separate Python service that trains models, runs backtests, and registers to MLflow. It does NOT currently have a validation gate. Implement everything in this section.

### Overview

Producer trains → runs backtest → validates → registers to MLflow (if passes). Polls `model_deployments` in alphaTrade DB for executor structural failures. Decides whether to retrain based on failure category and retrain budget.

### Shared DB Access

Producer needs read access to alphaTrade's PostgreSQL `model_deployments` table.

```
Connection string: same DATABASE_URL as alphaTrade (from shared .env or env var)
Table: model_deployments
Columns used: run_name, failed_at, failure_msg, status
Access: read-only SELECT
```

### ProducerValidationGate

New module: `producer/validation/gate.py`

Runs after backtest completes, before `mlflow.register_model()`.

**Thresholds (all in producer `.env`):**

```env
# Backtest metrics
VALIDATION_MIN_SHARPE=0.5
VALIDATION_MAX_DRAWDOWN=0.20
VALIDATION_MIN_HIT_RATE=0.45
VALIDATION_MIN_N_TRADES=10

# Training metrics
VALIDATION_MAX_VAL_LOSS=0.5
VALIDATION_MIN_VAL_ACCURACY=0.50
VALIDATION_MIN_F1_CLASS1=0.40
```

**Interface:**

```python
@dataclass
class ProducerValidationResult:
    passed: bool
    reason: str | None = None
    category: str | None = None  # "metric" | "training" | "structural"

class ProducerValidationGate:
    def check_backtest(self, metrics: dict) -> ProducerValidationResult: ...
    def check_training(self, fold_results: list[FoldResult]) -> ProducerValidationResult: ...
```

**Check order:**

1. Backtest metrics: sharpe ≥ MIN_SHARPE, abs(max_drawdown) ≤ MAX_DRAWDOWN, hit_rate ≥ MIN_HIT_RATE, n_trades ≥ MIN_N_TRADES
2. Training metrics: mean(best_val_loss across folds) ≤ MAX_VAL_LOSS, mean(val_accuracy) ≥ MIN_VAL_ACCURACY, mean(f1[class1]) ≥ MIN_F1_CLASS1

First failure short-circuits. Returns category so retrain logic can act on it.

### Registration Flow

```python
# In producer run/train pipeline, after backtest:

gate = ProducerValidationGate(config)
result = gate.check_backtest(backtest_metrics)
if result.passed:
    result = gate.check_training(fold_results)

if not result.passed:
    log.warning("validation failed [%s]: %s", result.category, result.reason)
    retrain_manager.handle_failure(model_name, result)
    return  # do NOT call mlflow.register_model

# Register to MLflow and set production alias
mlflow.register_model(...)
client.set_registered_model_alias(model_name, "production", version)
```

### Retrain Manager

New module: `producer/validation/retrain.py`

**Failure categories and actions:**

| Category | Condition | Action |
|---|---|---|
| `metric` | backtest metrics below threshold | retrain if budget remaining, else flag human |
| `training` | val_loss / accuracy / f1 bad | retrain if budget remaining, else flag human |
| `structural` | executor: no run_id, missing artifact | flag human — code/config bug, no retrain |
| `infra` | executor: download failed | retry later, do not count against retrain budget |

**Retrain budget:**

```env
RETRAIN_MAX_ATTEMPTS=3   # per model_name, resets on successful registration
```

Stored in producer DB or local JSON file (producer's choice). Key: `model_name`. Value: attempt count + last_attempt_at.

**Executor failure polling:**

On startup and after each registration attempt:

```python
def check_executor_failures(model_name: str, db_session) -> ExecutorFailure | None:
    # model_name == run_name in alphaTrade's model_deployments table
    row = db_session.execute(
        text(
            "SELECT failure_msg, failed_at FROM model_deployments "
            "WHERE run_name = :run_name AND status = 'failed' "
            "ORDER BY promoted_at DESC LIMIT 1"
        ),
        {"run_name": model_name},
    ).fetchone()
    if row:
        return categorize_failure(row.failure_msg)
    return None

def categorize_failure(msg: str) -> str:
    if "no run_id" in msg or "backtest.json missing" in msg:
        return "structural"
    if "download failed" in msg:
        return "infra"
    return "unknown"
```

**Human flag:**

When retrain budget exhausted or structural/unknown failure: write to producer log at ERROR level + emit notification (Slack/email/webhook — producer's existing notify mechanism). Do not register. Do not retrain.

### Config Summary (producer `.env`)

```env
# Shared DB (alphaTrade)
ALPHATRADE_DATABASE_URL=postgresql://...

# Validation thresholds
VALIDATION_MIN_SHARPE=0.5
VALIDATION_MAX_DRAWDOWN=0.20
VALIDATION_MIN_HIT_RATE=0.45
VALIDATION_MIN_N_TRADES=10
VALIDATION_MAX_VAL_LOSS=0.5
VALIDATION_MIN_VAL_ACCURACY=0.50
VALIDATION_MIN_F1_CLASS1=0.40

# Retrain
RETRAIN_MAX_ATTEMPTS=3
```

### Files to Create (producer service)

```
producer/
  validation/
    __init__.py
    gate.py          # ProducerValidationGate, ProducerValidationResult
    retrain.py       # RetrainManager, failure categorization, executor poll
    config.py        # ValidationConfig, RetrainConfig loaded from .env
```

### Integration Point

Caller (producer's existing train/run pipeline) calls gate after backtest, before MLflow registration. No other changes to producer pipeline needed.

---

## Out of Scope

- Executor does not delete MLflow model versions. Producer owns MLflow lifecycle.
- No webhook/REST between services — DB polling only.
- No per-class retrain strategy — retrain decision is binary (yes/no).
- Executor `ValidationThresholds` config removed, not migrated.
