# Model Retirement Config Design

**Date:** 2026-05-18  
**Status:** Approved

## Overview

Extends model retirement with time-based evaluation gating, per-model config overrides, and full runtime mutability via API. Replaces the trade-count-only gate with an OR gate (time elapsed OR trade count reached). Adds per-model enable/disable and threshold overrides. Global config is runtime-mutable without restart.

---

## Section 1: Config

### Global (`ModelRetirementConfig`)

Two new fields added to existing config:

```python
class ModelRetirementConfig(BaseSettings):
    enabled: bool = False
    lookback_trades: int = 20           # rolling window size (global only, not per-model)
    min_win_rate: float = 0.4
    min_rolling_pnl: float = -500.0
    min_evaluation_period: str = "30d"  # NEW — time-based gate
    min_trades_before_evaluation: int = 5  # NEW — trade count gate
```

Set via `overrides.yaml` at startup. Runtime-mutable via `PATCH /retirement/config` (writes to `BotSettings` DB + mutates in-memory immediately).

### Per-model (`ModelRetirementOverride`)

New class, nested in `ModelOverride` under `retirement:` key:

```python
class ModelRetirementOverride(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    enabled: Optional[bool] = None
    min_win_rate: Optional[float] = None
    min_rolling_pnl: Optional[float] = None
    min_trades_before_evaluation: Optional[int] = None
    min_evaluation_period: Optional[str] = None
```

`ModelOverride` gains:
```python
retirement: ModelRetirementOverride = ModelRetirementOverride()
```

All fields optional — `None` means fall back to global. `lookback_trades` is global-only (per-model override is a footgun; see design rationale below).

`overrides.yaml` example:
```yaml
models:
  my_daily_model:
    retirement:
      enabled: false
      min_win_rate: 0.3
      min_rolling_pnl: -200.0
      min_trades_before_evaluation: 3
      min_evaluation_period: "60d"
```

Runtime-mutable via `PATCH /models/{run_name}/retirement` (mutates in-memory + persists to YAML). Follows identical pattern to `PATCH /backtest/schedule/{model_id}`.

### Merge semantics

```python
def _effective_config(
    global_cfg: ModelRetirementConfig,
    per_model: ModelRetirementOverride,
) -> ModelRetirementConfig:
    """Per-model non-None values win over global."""
    return ModelRetirementConfig(
        enabled=per_model.enabled if per_model.enabled is not None else global_cfg.enabled,
        min_win_rate=per_model.min_win_rate if per_model.min_win_rate is not None else global_cfg.min_win_rate,
        min_rolling_pnl=per_model.min_rolling_pnl if per_model.min_rolling_pnl is not None else global_cfg.min_rolling_pnl,
        min_trades_before_evaluation=per_model.min_trades_before_evaluation if per_model.min_trades_before_evaluation is not None else global_cfg.min_trades_before_evaluation,
        min_evaluation_period=per_model.min_evaluation_period if per_model.min_evaluation_period is not None else global_cfg.min_evaluation_period,
        lookback_trades=global_cfg.lookback_trades,
    )
```

### Why `lookback_trades` is global-only

Per-model `lookback_trades` can be set so high the model never accumulates enough trades to be evaluated — silently bypassing retirement. The correct solution for interval-aware windows is the time-based gate (`min_evaluation_period`).

---

## Section 2: DB Schema

New migration: `0005_retirement_config.py`

### `ModelPerformance` — add `first_trade_at`

```python
first_trade_at: Optional[datetime] = Field(default=None)
```

Set once on first `record_trade` call (when `trade_count == 0`). Never updated after that. Used by time-based gate in `check_retirement`.

### `BotSettings` — add global retirement runtime fields

```python
retirement_enabled: Optional[bool] = Field(default=None)
retirement_min_win_rate: Optional[float] = Field(default=None)
retirement_min_rolling_pnl: Optional[float] = Field(default=None)
retirement_min_trades_before_evaluation: Optional[int] = Field(default=None)
retirement_min_evaluation_period: Optional[str] = Field(default=None)
```

All nullable. `None` = use YAML value. Applied over YAML defaults in `apply_bot_settings` and immediately on `PATCH /retirement/config`.

Per-model overrides have no DB storage — live in `settings.model_overrides` (in-memory + YAML), identical to backtest overrides.

---

## Section 3: Logic

### `record_trade` change

```python
if perf.trade_count == 0:
    perf.first_trade_at = datetime.utcnow()
# then increment trade_count as before
```

### Period parser

```python
def _parse_period(s: str) -> timedelta:
    if s.endswith("d"):
        return timedelta(days=int(s[:-1]))
    raise ValueError(f"Invalid period format: {s!r}. Use e.g. '30d'")
```

Supports `"Nd"` only. No weeks/months to avoid calendar ambiguity.

### `check_retirement` — OR gate replaces trade count gate

```python
def check_retirement(session, model_id, cfg) -> bool:
    if not cfg.enabled:
        return False

    repo = ModelPerformanceRepo(session)
    perf = repo.get_or_create(model_id)
    if perf.retired:
        return True

    period = _parse_period(cfg.min_evaluation_period)
    age_ok = perf.first_trade_at is not None and (datetime.utcnow() - perf.first_trade_at) >= period
    trades_ok = perf.trade_count >= cfg.min_trades_before_evaluation

    if not (age_ok or trades_ok):  # neither gate met
        return False

    # evaluate thresholds
    trades = json.loads(perf.rolling_trades_json)
    win_rate = sum(1 for t in trades if t > 0) / len(trades) if trades else 0.0
    rolling_pnl = sum(trades)

    should_retire = win_rate < cfg.min_win_rate or rolling_pnl < cfg.min_rolling_pnl
    if should_retire:
        perf.retired = True
        perf.retired_at = datetime.utcnow()
        repo.update(perf)
    return should_retire
```

### Callers (tick loop + OCO monitor)

Both resolve per-model override and pass merged config:

```python
per_model = settings.model_overrides.get(manifest.run_name, ModelOverride()).retirement
cfg = _effective_config(settings.risk.model_retirement, per_model)
record_trade(session, model_id=manifest.run_name, realized_pnl=pnl, cfg=cfg)
if check_retirement(session, model_id=manifest.run_name, cfg=cfg):
    wh.notify("WARNING", f"Model {manifest.run_name} auto-retired: performance below threshold",
              category="model-retirement")
```

---

## Section 4: API

### Global retirement config

```
GET   /api/v1/retirement/config   → current effective global config (YAML + DB merged)
PATCH /api/v1/retirement/config   → update global (writes BotSettings DB + immediate in-memory)
```

`PATCH` body — all optional:
```json
{
  "enabled": true,
  "min_win_rate": 0.35,
  "min_rolling_pnl": -300.0,
  "min_trades_before_evaluation": 10,
  "min_evaluation_period": "45d"
}
```

Implemented in new `retirement.py` router. Router receives `settings` reference via `make_router(session_dep, api_key_dep, settings)`.

### Per-model retirement config

```
GET    /api/v1/models/{run_name}/retirement   → overrides + effective values
PATCH  /api/v1/models/{run_name}/retirement   → set overrides, persist to YAML
DELETE /api/v1/models/{run_name}/retirement   → clear overrides, revert to global
```

`GET` response:
```json
{
  "run_name": "my_model",
  "enabled": null,
  "min_win_rate": 0.3,
  "min_rolling_pnl": null,
  "min_trades_before_evaluation": null,
  "min_evaluation_period": null,
  "effective_enabled": true,
  "effective_min_win_rate": 0.3,
  "effective_min_rolling_pnl": -500.0,
  "effective_min_trades_before_evaluation": 5,
  "effective_min_evaluation_period": "30d"
}
```

`null` override = not set, uses global. Effective fields show resolved values after merge — frontend gets complete picture in one request.

### Model un-retire

```
POST /api/v1/models/{run_name}/unretire
```

Clears `retired`, `retired_at`, `first_trade_at`, `rolling_trades_json`, `trade_count`, `win_count`, `rolling_pnl` on `ModelPerformance`. Full reset — prevents immediate re-retirement from stale history. Returns updated `ModelSummary`.

### App wiring

```python
def create_app(engine, health_state, registry=None, backtest_scheduler=None, settings=None):
    ...
    app.include_router(retirement.make_router(session_dep, api_key_dep, settings), prefix="/api/v1")
```

`settings` passed by reference — mutations in the router are immediately visible to the tick loop.

---

## Section 5: Testing

### `test_retirement_config.py`
- `None` per-model fields fall back to global
- Non-`None` per-model fields override global
- `enabled=false` per-model overrides global `enabled=true`
- `_parse_period` handles `"30d"`, rejects bad format

### `test_model_performance.py` (extend existing)
- `first_trade_at` set on first trade, unchanged on subsequent trades
- OR gate: retires after `min_trades_before_evaluation` met even if period not elapsed
- OR gate: retires after `min_evaluation_period` met even if trade count not reached
- Neither gate met → no evaluation → no retirement
- Already-retired model returns `True` immediately

### `test_retirement_api.py` (new)
- `GET /retirement/config` returns YAML defaults when no DB override
- `PATCH /retirement/config` updates in-memory immediately + persists to DB
- `GET /models/{run_name}/retirement` returns null overrides + correct effective values
- `PATCH /models/{run_name}/retirement` mutates in-memory + YAML
- `DELETE /models/{run_name}/retirement` clears overrides, effective values revert to global
- `POST /models/{run_name}/unretire` clears retired flag + resets performance counters

### `test_oco_monitor.py` (extend existing)
- Retirement fires with correct merged config when OCO closes trade

---

## Runtime Mutability Summary

| Change | Mechanism | Takes effect |
|---|---|---|
| Global retirement config | `PATCH /retirement/config` → DB write + immediate in-memory mutation | Instant |
| Per-model retirement override | `PATCH /models/{run_name}/retirement` → in-memory mutation + YAML persist | Instant |
| Un-retire model | `POST /models/{run_name}/unretire` → DB write | Next registry refresh (≤ 1 tick) |
