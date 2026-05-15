# alphaLink — UI Handover Document

> For the agent building the frontend UI. Read this top-to-bottom before writing any code.

---

## 1. What alphaLink Is

alphaLink is a production ML-driven trading bot. It loads ONNX model artifacts, fetches live OHLCV market data, runs inference + multi-model consensus, applies risk gates, and executes orders on Trading212. It persists signals, orders, positions, equity curve, and trade journals in SQLite.

The UI's job is a **read-mostly monitoring dashboard**: show live bot state, stream live events, display performance history, and expose a settings panel. The API is almost entirely read-only — order placement and position management happen inside the bot, not from the UI.

```
┌─────────────────────────────────────────────────────┐
│                   UI (separate repo)                │
│  Dashboard · Positions · Signals · Equity · Trades  │
│             Settings · Backtest Viewer              │
└───────────────┬─────────────────┬───────────────────┘
                │  REST (polling) │  SSE (streaming)
                ▼                 ▼
┌─────────────────────────────────────────────────────┐
│         alphaLink FastAPI  :8081/api/v1             │
├─────────────────────────────────────────────────────┤
│  Scheduler → Inference → Consensus → Risk → T212    │
│  SQLite state ← orders, positions, equity, trades   │
└─────────────────────────────────────────────────────┘
         │ health probe
         ▼
   aiohttp :8080  /healthz  /readyz   (no auth)
   Prometheus :9090  /metrics         (no auth)
```

---

## 2. Connection Setup

### Base URL

```
http://localhost:8081/api/v1
```

Store in an env var (e.g. `VITE_ALPHALINK_API_URL`). Docker Compose maps `8081:8081`.

### CORS

`allow_origins=["*"]` — no proxy needed. Direct fetch from browser works.

### Authentication

All `/api/v1/*` endpoints check for auth if `ALPHALINK_API_KEY` env var or the `alphalink_api_key` DB setting is configured. If neither is set, auth is disabled (useful for local dev).

| Transport | How to pass key |
|---|---|
| REST (GET/PUT) | `X-Api-Key: <key>` header |
| SSE stream | `?key=<key>` query param |

Wrong key → `403 {"detail":"Invalid API key"}`.

### Health Endpoints (no auth, separate server)

```
GET http://localhost:8080/healthz   → 200 ok  |  503 stale tick
GET http://localhost:8080/readyz    → 200 ok  |  503 not ready
```

Use for startup gating in UI (show "connecting…" until readyz passes).

---

## 3. REST Endpoints

All return JSON. Times are **naive UTC ISO-8601** strings — UI must localize for display. Ordering: `ts` descending for most collections; `ts` ascending for equity curve. Pagination via `since` (datetime/date) + `limit` (int, capped per endpoint). No cursors or page tokens.

Error envelope: `{"detail": "<message>"}` with HTTP 403 / 404 / 422.

---

### GET `/positions` → `Position[]`

Open positions. No query params.

| Field | Type | Notes |
|---|---|---|
| `id` | int | |
| `t212_ticker` | str | Trading212 instrument ticker |
| `quantity` | float | Positive = long |
| `avg_entry` | float | Average fill price |
| `opened_at` | datetime | |
| `last_signal_ts` | datetime\|null | |
| `cooldown_until_ts` | datetime\|null | Non-null = blocked from re-entry |
| `stop_order_id` | str\|null | OCO stop leg |
| `limit_order_id` | str\|null | OCO take-profit leg |

---

### GET `/orders` → `Order[]`

| Query | Default | Max |
|---|---|---|
| `since` | now − 24h | — |
| `limit` | 100 | 1000 |

| Field | Type | Notes |
|---|---|---|
| `id` | int | |
| `ts` | datetime | |
| `signal_id` | int\|null | FK to signal |
| `t212_ticker` | str | |
| `side` | str | `BUY` or `SELL` |
| `quantity` | float | |
| `status` | str | `pending` · `filled` · `rejected` · `error` |
| `t212_order_id` | str | Trading212 order reference |
| `fill_price` | float\|null | Null until filled |
| `error_msg` | str | Empty string if no error |
| `client_order_id` | str | Idempotency key |

---

### GET `/signals` → `Signal[]`

| Query | Default | Max |
|---|---|---|
| `since` | now − 24h | — |
| `limit` | 100 | 1000 |

| Field | Type | Notes |
|---|---|---|
| `id` | int | |
| `ts` | datetime | |
| `run_name` | str | Model identifier |
| `ticker` | str | yfinance-style ticker |
| `signal` | str | `BUY` · `SELL` · `HOLD` |
| `model_count` | int | Models that voted |
| `raw_json` | str | JSON-encoded logits / probabilities |

---

### GET `/pnl` → `PnlSnapshot[]`

Daily P&L snapshots.

| Query | Default | Max |
|---|---|---|
| `since` | — | — |
| `limit` | 100 | 1000 |

`since` format: `YYYY-MM-DD` (date, not datetime).

| Field | Type |
|---|---|
| `id` | int |
| `date` | str (YYYY-MM-DD) |
| `total_equity` | float |
| `day_pnl` | float |
| `day_pnl_pct` | float |
| `realized_pnl` | float |
| `unrealized_pnl` | float |
| `positions_json` | str (JSON) |
| `open_positions` | int |
| `trade_count` | int |

---

### GET `/models` → `ModelPerformance[]`

Per-model rolling performance stats.

| Field | Type | Notes |
|---|---|---|
| `id` | int | |
| `model_id` | str | Matches manifest model identifier |
| `trade_count` | int | |
| `win_count` | int | |
| `rolling_pnl` | float | |
| `rolling_trades_json` | str | JSON-encoded recent trades |
| `retired` | bool | |
| `retired_at` | datetime\|null | |
| `last_updated` | datetime | |

---

### GET `/backtest/runs` → `BacktestRun[]`

List completed backtest runs.

### GET `/backtest/runs/{run_id}` → `BacktestRun`

404 if not found.

| Field | Type |
|---|---|
| `id` | int |
| `ts` | datetime |
| `start_date` | str (YYYY-MM-DD) |
| `end_date` | str (YYYY-MM-DD) |
| `config_json` | str (JSON) |

### GET `/backtest/runs/{run_id}/trades` → `BacktestTrade[]`

404 if run not found.

| Field | Type | Notes |
|---|---|---|
| `id` | int | |
| `run_id` | int | |
| `model_id` | str | |
| `side` | str | `BUY` · `SELL` |
| `entry_bar` | int | Bar index |
| `exit_bar` | int | |
| `entry_time` | datetime | |
| `exit_time` | datetime | |
| `entry_price` | float | |
| `exit_price` | float | |
| `quantity` | float | |
| `exit_reason` | str | |
| `realized_pnl` | float | |
| `sl_price` | float\|null | |
| `tp_price` | float\|null | |

---

### GET `/equity-curve` → `EquityCurve[]`

Equity timeseries. Order: `ts` **ascending**.

| Query | Default | Max |
|---|---|---|
| `since` | now − 30d | — |
| `limit` | 500 | 5000 |

| Field | Type |
|---|---|
| `id` | int |
| `ts` | datetime |
| `equity` | float |

---

### GET `/trades` → `TradeJournal[]`

Closed trade journal.

| Query | Default | Notes |
|---|---|---|
| `since` | now − 30d | Ignored when `model_id` set |
| `limit` | 100 | Max 1000 |
| `model_id` | — | Filter by model; overrides `since` |

| Field | Type | Notes |
|---|---|---|
| `id` | int | |
| `ts` | datetime | |
| `model_id` | str | |
| `ticker` | str | |
| `entry_price` | float | |
| `exit_price` | float | |
| `quantity` | float | |
| `entry_time` | datetime | |
| `exit_time` | datetime | |
| `hold_bars` | int | |
| `exit_reason` | str | `OCO_SL` · `OCO_TP` · `SIGNAL_SELL` · `HALT` · `MANUAL` |
| `sl_price` | float\|null | |
| `tp_price` | float\|null | |
| `realized_pnl` | float | |
| `pnl_pct` | float | |

---

### GET `/health` → object

In-API health state. Distinct from aiohttp `/healthz`.

```json
{
  "last_tick_at": "2026-05-15T14:32:00" | null,
  "t212_ok": true,
  "models_loaded": true,
  "longest_interval_seconds": 86400
}
```

---

### GET `/settings` → BotSettings (masked)

Returns current config. Sensitive fields replaced with `"***"`:
`t212_api_key`, `polygon_api_key`, `email_smtp_password`, `slack_webhook_url`, `alphalink_api_key`.

### PUT `/settings` → BotSettings (masked)

Body: JSON, all fields optional. Only provided fields are updated. Hot-reload applied on next tick (T212Client reinits automatically).

Mutable fields:

| Field | Type |
|---|---|
| `t212_api_key` | str |
| `t212_env` | str |
| `t212_account_type` | str |
| `data_provider` | str |
| `polygon_api_key` | str |
| `slack_enabled` | bool |
| `slack_webhook_url` | str |
| `slack_min_level` | str |
| `email_enabled` | bool |
| `email_smtp_host` | str |
| `email_smtp_port` | int |
| `email_smtp_user` | str |
| `email_smtp_password` | str |
| `email_from_addr` | str |
| `email_to_addrs` | str (comma-separated) |
| `email_min_level` | str |
| `size_pct` | float |
| `stop_loss_pct` | float |
| `take_profit_pct` | float |
| `cooldown_bars` | int |
| `extended_hours` | bool |
| `max_positions` | int |
| `daily_loss_halt_pct` | float |
| `alphalink_api_key` | str |

**UI note:** Mask sensitive fields client-side too — don't render `***` in an editable input. Use placeholder text instead and only PUT when user edits them.

---

### GET `/kill-switch` → object

Current halt state.

```json
{ "halted": false, "sentinel_file": "HALT" }
```

### POST `/halt` → object

Engages kill switch — bot stays alive, inference continues, orders stop submitting. Idempotent.

```json
{ "halted": true, "sentinel_file": "HALT" }
```

### POST `/resume` → object

Disengages kill switch — order submission resumes. Idempotent.

```json
{ "halted": false, "sentinel_file": "HALT" }
```

---

## 4. SSE Stream

```
GET /api/v1/stream?key=<api_key>
Content-Type: text/event-stream
```

Each event: `data: <json>\n\n`. First chunk is `: ping\n\n` (flush).

No heartbeat after initial ping. Use `EventSource` (browser native) — it auto-reconnects on disconnect.

### Event Types

**`signal_fired`** — emitted when inference produces a signal
```json
{
  "type": "signal_fired",
  "ticker": "AAPL",
  "signal": "BUY",
  "run_name": "model_aapl_v1",
  "ts": "2026-05-15T14:30:00Z"
}
```

**`order_filled`** — emitted when T212 reports fill
```json
{
  "type": "order_filled",
  "ticker": "AAPL",
  "side": "BUY",
  "qty": 10.0,
  "fill_price": 183.42,
  "ts": "2026-05-15T14:30:01Z"
}
```

**`tick_complete`** — emitted at end of each scheduler tick
```json
{
  "type": "tick_complete",
  "interval": "1h",
  "ts": "2026-05-15T14:30:02Z"
}
```

**Recommended pattern:** subscribe to SSE on app mount → on `order_filled` or `tick_complete`, invalidate relevant REST query caches (positions, orders, signals).

---

## 5. Suggested UI Feature Map

| View | Primary endpoints | Live update |
|---|---|---|
| **Dashboard** | `/health`, `/pnl` (latest 1), `/positions` | SSE `tick_complete` → refetch |
| **Positions** | `/positions` | SSE `order_filled` → refetch |
| **Orders log** | `/orders?since=&limit=` | SSE `order_filled` → prepend or refetch |
| **Signals feed** | `/signals?since=&limit=` | SSE `signal_fired` → prepend |
| **Equity chart** | `/equity-curve?since=` | Poll every N minutes |
| **P&L history** | `/pnl?since=` | Poll daily or on `tick_complete` |
| **Trade journal** | `/trades?since=&model_id=` | Poll |
| **Models** | `/models` | Poll |
| **Backtest viewer** | `/backtest/runs` + `/backtest/runs/{id}/trades` | Static |
| **Settings panel** | GET + PUT `/settings` | User-initiated |
| **Kill switch** | GET `/kill-switch`, POST `/halt`, POST `/resume` | Poll `/kill-switch` to reflect current state |

---

## 6. Known Gaps — Tell the User Before Building

These features do **not** have API endpoints. Build UI affordances only if user confirms backend changes first.

| Gap | Current state | Workaround |
|---|---|---|
| **Manual position close** | No DELETE or close endpoint | Bot-managed only |
| **Order placement** | API is read-only | By design — bot places all orders |
| **Model upload** | No endpoint | Drop files into `models_dir` on server |

---

## 7. Local Dev Bring-up

```bash
# Start bot (pick one)
alphalink run
docker compose up

# Verify REST API
curl http://localhost:8081/api/v1/health

# Test SSE (streams indefinitely)
curl -N "http://localhost:8081/api/v1/stream"

# With auth key
curl -H "X-Api-Key: yourkey" http://localhost:8081/api/v1/positions
curl -N "http://localhost:8081/api/v1/stream?key=yourkey"

# Health probes (no auth)
curl http://localhost:8080/readyz
```

---

## 8. Useful Background Docs

| File | Content |
|---|---|
| `FEATURES.md` | Full feature inventory — all capabilities of the bot |
| `docs/RUNBOOK.md` | Operational runbook |
| `docker-compose.yml` | Port mappings, env var names |
| `overrides.yaml` | Per-model and global config reference |
