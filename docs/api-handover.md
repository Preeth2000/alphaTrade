# Backtest API — UI Handover

New endpoints for manually triggering backtests and managing the cron schedule.

**Base URL:** `http://localhost:8081/api/v1`  
**Auth:** `X-API-Key: <alphaTrade_api_key>` on every request.

---

## New Endpoints

### `POST /backtest/trigger` → 202

Kicks off a backtest immediately. Fire-and-forget — returns a `run_id` instantly, backtest runs in the background. Poll `GET /backtest/runs/{run_id}` to track status.

**Request body** (all fields optional):
```json
{
  "start": "2026-04-01",
  "end": "2026-05-01",
  "model_id": "AAPL_v1"
}
```

- `start` / `end`: ISO date strings (`YYYY-MM-DD`). If omitted, defaults to a rolling window ending today using the configured `lookback_days`.
- `model_id`: run only this model. Omit to run all models.

**Response:**
```json
{ "run_id": 42, "status": "queued" }
```

503 if the backend was started without backtest scheduler support.

---

### `GET /backtest/runs/{run_id}`

Single run by ID. 404 if not found.

```json
{
  "id": 42,
  "ts": "2026-05-16T09:15:00",
  "start_date": "2026-04-16",
  "end_date": "2026-05-16",
  "config_json": "{...}",
  "status": "done"
}
```

`status` values: `"queued"` → `"running"` → `"done"` | `"failed"`

Poll this after triggering. Suggested interval: 2s while `status` is `queued` or `running`.

---

### `GET /backtest/schedule`

Global schedule status.

```json
{
  "schedule_enabled": true,
  "cron": "0 2 * * *",
  "lookback_days": 30,
  "jobs": [
    { "id": "backtest_AAPL_v1", "next_run_time": "2026-05-17T02:00:00+00:00" },
    { "id": "backtest_TSLA_v2", "next_run_time": "2026-05-17T02:00:00+00:00" }
  ]
}
```

`jobs` lists active APScheduler jobs — one per model that has scheduled runs enabled.

---

### `PATCH /backtest/schedule`

Update global schedule settings. Send only fields to change.

```json
{
  "schedule_enabled": false,
  "cron": "0 3 * * 1-5",
  "lookback_days": 60
}
```

- `schedule_enabled`: `false` cancels all scheduled jobs immediately.
- `cron`: standard 5-field cron expression. 422 on invalid syntax.
- `lookback_days`: how many days of history each scheduled run covers.

Returns same shape as `GET /backtest/schedule`. Changes persist across restarts (`overrides.yaml`).

---

### `GET /backtest/schedule/{model_id}`

Per-model schedule status.

```json
{
  "model_id": "AAPL_v1",
  "disabled": false,
  "effective_cron": "0 2 * * *",
  "effective_lookback_days": 30,
  "next_run_time": "2026-05-17T02:00:00+00:00"
}
```

`effective_*` = model-level override if set, otherwise falls back to global value.  
`next_run_time` is `null` if the model is disabled or scheduling is globally off.

---

### `PATCH /backtest/schedule/{model_id}`

Override schedule for one model. Send only fields to change.

```json
{
  "disabled": true,
  "cron": "0 4 * * *",
  "lookback_days": 90
}
```

- `disabled: true` removes this model's job without affecting others.
- `cron` / `lookback_days` override the global values for this model only. 422 on invalid cron.

Returns same shape as `GET /backtest/schedule/{model_id}`. Persists to `overrides.yaml`.

---

## Existing Endpoints (unchanged)

These existed before — no changes, listed for context:

- `GET /backtest/runs` — list all runs
- `GET /backtest/runs/{run_id}/trades` — trades from a specific run

---

## Error Shapes

| Status | Meaning |
|--------|---------|
| 422 | Validation error — e.g. invalid cron |
| 503 | Scheduler not available |

422 example (bad cron):
```json
{
  "detail": [{ "loc": ["body", "cron"], "msg": "Invalid cron expression: '99 99 * * *'", "type": "value_error" }]
}
```

---

## Suggested UI Flow

**Manual trigger:**
1. User clicks "Run Backtest" → optional date pickers + optional model filter
2. `POST /backtest/trigger` → get `run_id`
3. Poll `GET /backtest/runs/{run_id}` every 2s → show status badge
4. On `done`, show link to trades: `GET /backtest/runs/{run_id}/trades`

**Schedule management:**
1. Load `GET /backtest/schedule` → show toggle + cron input + lookback slider
2. `PATCH /backtest/schedule` on change
3. Per model: `GET /backtest/schedule/{model_id}` → show per-model overrides panel
4. `PATCH /backtest/schedule/{model_id}` to disable/override individual models
