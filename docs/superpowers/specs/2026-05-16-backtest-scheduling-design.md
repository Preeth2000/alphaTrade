# Backtest Scheduling Design

**Date:** 2026-05-16  
**Status:** Approved

## Overview

Add manual API trigger for backtests, nightly scheduled backtests via APScheduler, and runtime schedule management endpoints. Per-model cron overrides and disable flags live in `overrides.yaml`.

---

## 1. Config Changes

### `BacktestConfig` (alphaTrade/config.py)

Add three fields:

```python
class BacktestConfig(BaseSettings):
    # existing fields unchanged
    slippage_bps: int = 5
    commission_per_trade: float = 0.0
    initial_equity: float = 10000.0
    default_size_pct: float = 0.10
    sl_pct: Optional[float] = None
    tp_pct: Optional[float] = None
    # new
    schedule_enabled: bool = True
    cron: str = "0 2 * * *"      # 2am UTC nightly
    lookback_days: int = 30
```

### `BacktestScheduleOverride` + `ModelOverride` (alphaTrade/config.py)

New nested model; added as optional field on `ModelOverride`:

```python
class BacktestScheduleOverride(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    disabled: bool = False
    cron: Optional[str] = None          # None = inherit global
    lookback_days: Optional[int] = None # None = inherit global

class ModelOverride(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    enabled: bool = True
    t212_ticker: Optional[str] = None
    size_pct: Optional[float] = None
    backtest: BacktestScheduleOverride = BacktestScheduleOverride()
```

### overrides.yaml example

```yaml
backtest:
  schedule_enabled: true
  cron: "0 2 * * *"
  lookback_days: 30

models:
  AAPL_v1:
    backtest:
      cron: "0 6 * * *"   # different time for this model
  MSFT_v1:
    backtest:
      disabled: true       # excluded from scheduled runs
```

---

## 2. Database Migration

New Alembic migration adds `status` column to `backtest_run` table:

- Column: `status VARCHAR` default `"done"` (backward compat with existing rows)
- Values: `queued | running | done | failed`
- `BacktestRun` SQLModel updated to include `status: str = "done"`

---

## 3. Scheduler Module

**New file:** `alphaTrade/scheduler/backtest_scheduler.py`

### Architecture

- Wraps `apscheduler.schedulers.asyncio.AsyncIOScheduler`
- One APScheduler job per model (not one global job) — enables per-model cron
- Job ID convention: `f"backtest_{manifest.run_name}"`
- Started in `main.py` alongside `schedule_bar_close`

### Startup flow

```
scan_models(models_dir)
  → for each (manifest, model):
      override = settings.model_overrides.get(run_name).backtest
      if override.disabled → skip
      effective_cron = override.cron or settings.backtest.cron
      scheduler.add_job(
          _run_and_record,
          CronTrigger.from_crontab(effective_cron),
          id=f"backtest_{run_name}",
          args=[manifest, model, settings],
      )
scheduler.start()
```

`schedule_enabled=False` → scheduler not started at all.

### Shared execution function

```python
async def _run_and_record(manifest, model, settings, start=None, end=None):
    # compute date range: start/end or today-lookback_days
    # update BacktestRun.status = "running"
    # run run_backtest(...)
    # update status = "done" | "failed"
```

Both scheduled jobs and the API trigger call this function.

### Runtime update methods

```python
class BacktestScheduler:
    def update_global(self, schedule_enabled, cron, lookback_days) -> None
        # reschedule all non-disabled jobs; persist overrides.yaml

    def update_model(self, model_id, disabled, cron, lookback_days) -> None
        # remove + re-add job (or remove if disabled); persist overrides.yaml

    def get_status(self) -> dict
        # return all jobs with next_run_time, enabled status

    def get_model_status(self, model_id) -> dict
        # effective config + next_run_time for one model
```

---

## 4. API Endpoints

### Trigger

```
POST /backtest/trigger
```

Request body (all optional):
```json
{
  "start": "2025-04-01",
  "end":   "2025-05-16",
  "model_id": "AAPL_v1"
}
```

- `start`/`end` omitted → `end=today`, `start=today - lookback_days`
- `model_id` omitted → all models
- Launches `asyncio.create_task(_run_and_record(...))`
- Returns immediately: `{"run_id": int, "status": "queued"}`

### Schedule management

```
GET  /backtest/schedule                  → global config + all job statuses
PATCH /backtest/schedule                 → update global config
GET  /backtest/schedule/{model_id}       → effective config + next_run for model
PATCH /backtest/schedule/{model_id}      → update per-model override
```

**PATCH /backtest/schedule body** (all optional):
```json
{ "schedule_enabled": false, "cron": "0 6 * * *", "lookback_days": 60 }
```

**PATCH /backtest/schedule/{model_id} body** (all optional):
```json
{ "disabled": true, "cron": "0 4 * * *", "lookback_days": 14 }
```

Both PATCH endpoints:
1. Validate cron string via `CronTrigger.from_crontab()`
2. Update APScheduler jobs in memory
3. Persist changes to `overrides.yaml`
4. Return effective config after update

### Polling

Existing `GET /backtest/runs/{id}` returns `BacktestRun` including new `status` field.

---

## 5. Dependencies

Add to requirements: `apscheduler>=3.10`

---

## 6. Files Changed

| File | Change |
|------|--------|
| `alphaTrade/config.py` | Add `BacktestScheduleOverride`, extend `ModelOverride` and `BacktestConfig` |
| `alphaTrade/scheduler/backtest_scheduler.py` | New — `BacktestScheduler` class |
| `alphaTrade/api/routers/backtest.py` | Add trigger + schedule endpoints |
| `alphaTrade/api/app.py` | Wire `BacktestScheduler` into app startup |
| `alphaTrade/main.py` | Start `BacktestScheduler` alongside bar_close |
| `alphaTrade/store/repos.py` | Add `status` field to `BacktestRun` |
| `alphaTrade/store/migrations/versions/XXXX_backtest_status.py` | New Alembic migration |
| `requirements.txt` / `pyproject.toml` | Add `apscheduler>=3.10` |
