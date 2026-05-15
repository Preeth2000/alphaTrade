# alphaTrade API: Models & Overrides — UI Handover

**Date**: 2026-05-15  
**Base URL**: `http://localhost:8081/api/v1`  
**Auth**: all endpoints require `X-API-Key: <key>` header

---

## GET /models

Returns all known models — both currently loaded (active) and historical (in DB but no longer on disk).

**No request body.**

**Response**: `list[ModelSummary]`

```json
[
  {
    "run_name": "aapl_v1",
    "ticker": "AAPL",
    "interval": "1d",
    "model_arch": "mlp",
    "n_features": 14,
    "active": true,
    "trade_count": 42,
    "win_count": 24,
    "rolling_pnl": 312.50,
    "retired": false,
    "retired_at": null,
    "last_updated": "2026-05-15T10:00:00"
  },
  {
    "run_name": "tsla_old_v1",
    "ticker": null,
    "interval": null,
    "model_arch": null,
    "n_features": null,
    "active": false,
    "trade_count": 10,
    "win_count": 3,
    "rolling_pnl": -80.0,
    "retired": true,
    "retired_at": "2026-05-10T09:00:00",
    "last_updated": "2026-05-10T09:00:00"
  }
]
```

**Key fields:**

| Field | Meaning |
|---|---|
| `run_name` | Unique model ID. Use this as the key for all override calls. |
| `ticker` | yfinance ticker from the manifest (e.g. `AAPL`). Null if model no longer on disk. |
| `interval` | Bar interval: `1m`, `5m`, `15m`, `1h`, `1d`, `1wk` |
| `active` | `true` = model files on disk and loaded in registry right now |
| `retired` | `true` = bot auto-retired this model due to poor performance |
| `rolling_pnl` | Realized PnL across last N trades (configured by `min_rolling_pnl` in risk config) |

**UI note**: use `active` + `retired` together. A model can be `active: true, retired: true` if it was flagged for retirement but files not yet removed.

---

## GET /models/{run_name}/overrides

Returns the current per-model overrides for a specific model. All fields are nullable — `null` means "use global default from `GET /settings`".

**Response**: `ModelOverrideResponse`

```json
{
  "run_name": "aapl_v1",
  "enabled": null,
  "broker_ticker": null,
  "size_pct": 0.05,
  "stop_loss_pct": null,
  "take_profit_pct": null,
  "cooldown_bars": null,
  "updated_at": "2026-05-15T10:30:00",
  "resolved_ticker": "AAPL_US_EQ"
}
```

**Key fields:**

| Field | Meaning | Null means |
|---|---|---|
| `enabled` | Whether bot trades this model | Use default (`true`) |
| `broker_ticker` | Explicit broker-side ticker to use | Auto-resolve at tick time |
| `size_pct` | Fraction of account equity per order (e.g. `0.05` = 5%) | Use global `size_pct` from `/settings` |
| `stop_loss_pct` | Stop-loss distance from fill price (e.g. `0.02` = 2%) | Use global `stop_loss_pct` from `/settings` |
| `take_profit_pct` | Take-profit distance from fill price (e.g. `0.05` = 5%) | Use global `take_profit_pct` from `/settings` |
| `cooldown_bars` | Bars to wait after close before re-entry | Use global `cooldown_bars` from `/settings` |
| `resolved_ticker` | **Read-only.** Effective broker ticker the bot will actually use this tick. See below. |

### resolved_ticker explained

This is a read-only computed field showing what broker ticker the bot will use when executing an order for this model.

Resolution priority (same as the tick loop):
1. `broker_ticker` override in DB (what you set via PUT) → returned directly
2. Not set in DB → looks up the most recent signal for this `run_name` → checks the instrument cache (previously auto-resolved T212 ticker)
3. No cached value yet → returns `null`

**`null` does not mean broken** — it means the ticker hasn't been auto-resolved yet (model hasn't ticked) or no override is set. The bot will resolve it at tick time via the T212 instruments API and cache the result. After the first tick, `resolved_ticker` will be populated.

**When to set `broker_ticker` explicitly**: if the manifest `ticker` (e.g. `AAPL`) doesn't auto-resolve correctly to the broker's instrument name, set `broker_ticker` to the exact broker string (e.g. `AAPL_US_EQ`).

**Model not yet traded**: if `GET /models` shows `trade_count: 0`, `resolved_ticker` will be `null` until the first tick fires.

---

## PUT /models/{run_name}/overrides

Upserts overrides for a model. Partial update — only send fields you want to change. Fields not included in the request body are left unchanged.

To explicitly clear a field back to "use default", send it as `null`.

**Request body** (all fields optional):

```json
{
  "enabled": true,
  "broker_ticker": "AAPL_US_EQ",
  "size_pct": 0.05,
  "stop_loss_pct": 0.02,
  "take_profit_pct": 0.05,
  "cooldown_bars": 3
}
```

**Examples:**

Disable a model without removing it:
```json
{ "enabled": false }
```

Set broker ticker only:
```json
{ "broker_ticker": "AAPL_US_EQ" }
```

Clear broker_ticker override (revert to auto-resolve):
```json
{ "broker_ticker": null }
```

**Response**: same `ModelOverrideResponse` shape as GET, with `resolved_ticker` reflecting the new state.

---

## DELETE /models/{run_name}/overrides

Removes all overrides for a model. All fields revert to global defaults. Bot resumes auto-resolving the broker ticker.

**Response**:
```json
{ "deleted": true, "run_name": "aapl_v1" }
```

Returns `404` if no overrides exist for this `run_name`.

---

## Suggested UI flows

**Model list page:**
1. `GET /models` — render table with active/inactive badge, win rate (`win_count / trade_count`), rolling PnL, retired flag
2. For each active model, link to override editor

**Override editor (per model):**
1. `GET /models/{run_name}/overrides` — pre-fill form with current values; show `resolved_ticker` as read-only info field
2. `GET /settings` — fetch global defaults to show as placeholder text in null fields (so user knows what value applies)
3. On save: `PUT /models/{run_name}/overrides` with only changed fields
4. "Reset to defaults" button: `DELETE /models/{run_name}/overrides`

**Disable/enable toggle:**
```
PUT /models/{run_name}/overrides  { "enabled": false }
PUT /models/{run_name}/overrides  { "enabled": true }
```

---

## Global defaults reference

Fetch with `GET /settings`. Relevant fields that per-model overrides fall back to:

```json
{
  "size_pct": 0.10,
  "stop_loss_pct": 0.02,
  "take_profit_pct": 0.05,
  "cooldown_bars": 3
}
```
