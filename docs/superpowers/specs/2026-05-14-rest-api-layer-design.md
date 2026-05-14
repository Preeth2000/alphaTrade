# REST API Layer for UI Consumption

**Date:** 2026-05-14  
**Issue:** alphaLink-bmw  
**Status:** Approved

## Overview

FastAPI app at `alphalink/api/` serving SQLite repos over HTTP on `:8081`. Runs in the same asyncio event loop as the bot via `asyncio.create_task`. Also exposes a read/write settings endpoint backed by a new `BotSettings` SQLite table, enabling the UI to configure the bot without touching `.env` or `overrides.yaml`.

## Architecture

### New module: `alphalink/api/`

```
alphalink/api/
├── __init__.py
├── app.py          # FastAPI factory + start_api_server() coroutine
├── auth.py         # X-API-Key header dependency
├── deps.py         # DB session dependency (yields Session per request)
└── routers/
    ├── positions.py
    ├── orders.py
    ├── signals.py
    ├── pnl.py
    ├── models.py
    ├── backtest.py
    ├── health.py
    └── settings.py
```

### Startup integration (`main.py`)

`start_api_server(engine, health_state, settings)` is called after `start_health_server`. It starts uvicorn with `loop="none"` (externally managed asyncio loop) as an `asyncio.create_task`. The API server shuts down on the same stop event as the bot.

### Hot-reload

At the top of each `tick()`, call `BotSettingsRepo.get()`. If a `BotSettings` row exists, overlay its values onto the runtime `Settings` object. If `t212_api_key` or `t212_env` differs from the current `T212Client`, reinitialise the client in-place.

### New dependencies (`pyproject.toml`)

```
fastapi>=0.111
uvicorn[standard]>=0.29
```

### New config field (`Settings`)

```
ALPHALINK_API_PORT=8081   # default 8081
```

---

## Endpoints

All routes prefixed `/api/v1`.

### Authentication

`X-API-Key: <key>` header checked against `ALPHALINK_API_KEY` env var (or the `BotSettings.alphalink_api_key` DB value if set). If neither is set, auth is skipped (dev mode). Returns `403` on mismatch.

### Data endpoints (read-only)

| Method | Path | Query params | Source |
|--------|------|-------------|--------|
| GET | `/api/v1/positions` | — | `PositionRepo.all()` |
| GET | `/api/v1/orders` | `since` (ISO datetime, default -24h), `limit` (int, default 100) | `OrderRepo` |
| GET | `/api/v1/signals` | `since`, `limit` | `SignalRepo` |
| GET | `/api/v1/pnl` | `since` (YYYY-MM-DD), `limit` | `PnlSnapshotRepo` |
| GET | `/api/v1/models` | — | `ModelPerformanceRepo.all()` + registry manifest data |
| GET | `/api/v1/backtest/runs` | — | `BacktestRun` table |
| GET | `/api/v1/backtest/runs/{id}/trades` | — | `BacktestRepo.trades_for_run(id)` |
| GET | `/api/v1/health` | — | `HealthState` fields as JSON |

`orders` and `signals` without `since` default to last 24 hours.

### Settings endpoints (read/write)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/settings` | Return all settings; sensitive fields returned as `"***"` |
| PUT | `/api/v1/settings` | Partial update — only provided fields are written |

---

## BotSettings Table

New `BotSettings` SQLModel table. Singleton pattern: always id=1, upserted on PUT.

### Fields

| Group | Field | Type | Default |
|-------|-------|------|---------|
| **T212** | `t212_api_key` | str | `""` |
| | `t212_env` | str (`demo\|live`) | `"demo"` |
| | `t212_account_type` | str (`invest\|isa\|cfd`) | `"invest"` |
| **Data** | `data_provider` | str (`yfinance\|polygon`) | `"yfinance"` |
| | `polygon_api_key` | str | `""` |
| **Slack** | `slack_enabled` | bool | `false` |
| | `slack_webhook_url` | str | `""` |
| | `slack_min_level` | str | `"WARNING"` |
| **Email** | `email_enabled` | bool | `false` |
| | `email_smtp_host` | str | `""` |
| | `email_smtp_port` | int | `587` |
| | `email_smtp_user` | str | `""` |
| | `email_smtp_password` | str | `""` |
| | `email_from_addr` | str | `""` |
| | `email_to_addrs` | str | `""` (comma-separated) |
| | `email_min_level` | str | `"WARNING"` |
| **Trading defaults** | `size_pct` | float | `0.10` |
| | `stop_loss_pct` | float | `0.02` |
| | `take_profit_pct` | float | `0.05` |
| | `cooldown_bars` | int | `3` |
| | `extended_hours` | bool | `false` |
| **Risk** | `max_positions` | int | `5` |
| | `daily_loss_halt_pct` | float | `0.05` |
| **API** | `alphalink_api_key` | str | `""` |

Masked on GET read: `t212_api_key`, `polygon_api_key`, `email_smtp_password`, `slack_webhook_url`, `alphalink_api_key`.

New Alembic migration: `0003_bot_settings`.

New repo: `BotSettingsRepo` in `store/repos.py` with `get() -> BotSettings | None` and `upsert(BotSettings)`.

---

## Error Handling

- Route handlers return `404` if a resource (e.g. backtest run id) does not exist.
- DB errors return `500` with a generic message (no internal detail leaked).
- FastAPI handles request validation errors automatically (`422`).
- API server crash does not affect the bot loop — `asyncio.create_task` isolates it.

## Security

- `t212_api_key`, `polygon_api_key`, `email_smtp_password`, `slack_webhook_url`, `alphalink_api_key` stored plaintext in SQLite. Restrict file permissions: `chmod 600 state.db`.
- API key auth disabled only when both env var and DB value are empty — never silently bypassed.
- No rate limiting in scope (single-user local deployment).

## Testing

- Unit tests for each router using FastAPI `TestClient` with an in-memory SQLite DB.
- Auth middleware tested: valid key passes, wrong key returns 403, no key with no config passes.
- Settings hot-reload tested: mock `BotSettingsRepo.get()` returning changed T212 key, verify `T212Client` reinitialised.
