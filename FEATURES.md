# alphaTrade — Feature Reference

ML-driven automated trading bot. Consumes ONNX model artifacts from alphaGen, fetches live OHLCV data, executes orders on Trading212, and monitors risk/performance via REST API.

---

## CLI

| Command | Description |
|---|---|
| `alphaTrade run` | Start trading bot daemon with async event loop and schedulers |
| `alphaTrade verify <dir>` | Dry-run a model: hash check, ONNX smoke test, live data fetch, inference |
| `alphaTrade status` | Show loaded models, open positions, and day-open equity from state.db |
| `alphaTrade halt` | Engage kill switch — pause order submission without stopping bot |
| `alphaTrade resume` | Disengage kill switch — resume order submission |
| `alphaTrade backtest <start> <end>` | Run historical backtest across all models with slippage/commission |
| `alphaTrade report [--since DATE]` | Generate P&L report: daily snapshots and closed trade summary |

---

## Model Management

- **Auto-discovery** — Scans `models_dir` for valid `(manifest.json, model.onnx)` pairs on startup
- **Hot-reload registry** — Diffs models_dir each tick; adds/removes models without downtime
- **Manifest validation** — Enforces manifest_version, feature names, input shape, output format
- **ONNX integrity** — SHA256 hash verification and smoke test on every model load
- **Multi-model fusion** — Softmax-averaged consensus when multiple models target same ticker
- **Model retirement** — Auto-retires underperforming models by rolling win rate and P&L threshold

---

## Data Providers

- **yfinance** — Free, no API key, daily reliable, intraday delayed (default)
- **Polygon.io** — Premium data with configurable multiplier/timespan; requires `POLYGON_API_KEY`
- **OHLCV validation** — Column presence, bar count, and NaN checks per interval

---

## Feature Engineering

- **TA-Lib indicators** — RSI, MACD, BBANDS, ATR, EMA, SMA, ADX, Stochastic, and more
- **Normalization** — Z-score or min-max, mirroring alphaGen training exactly
- **Dynamic windowing** — MLP (flattened) and RNN/CNN (3D tensor) architecture support
- **Shape/dtype/NaN validation** — Applied after compute and normalize

---

## Inference

- **ONNX InferenceSession** — CPU executor with opset compatibility check
- **Smoke test** — Validates output shape `(1, 3)` and dtype `float32` on load
- **Per-bar prediction** — Single-sample inference returns logits tuple each tick

---

## Consensus

- **Softmax averaging** — Multiple logit sets → averaged softmax probs → BUY/SELL/HOLD signal
- **Ticker grouping** — Groups by stock ticker when multiple models trade same asset
- **Confidence scoring** — Returns max softmax probability per signal

---

## Risk Management

- **Daily loss halt** — Pauses non-HOLD signals if daily P&L loss exceeds configurable threshold
- **Cooldown enforcement** — Blocks re-entry for N bars after exit (configurable per model)
- **Max positions cap** — Hard limit on concurrent open positions
- **Pyramiding block** — Prevents multiple BUY signals on same asset
- **Short prevention** — SELL only allowed if position already open
- **Model retirement check** — Skips signals from retired models
- **Sector limits (balanced)** — Caps sector exposure as % of portfolio equity
- **Sector limits (unbalanced)** — Caps max positions per sector with per-sector overrides

---

## Position Sizing

- **Fixed** — Percent of equity (default 10%)
- **ATR-based** — Risk-per-trade in dollars, stop distance from ATR × multiplier
- **VIX-based** — Inverse-proportional to VIX; lower VIX = larger size, capped at max
- **Fallback** — Reverts to fixed if ATR is zero or VIX unavailable

---

## Order Execution

- **T212 HTTP client** — REST wrapper with auth, exponential backoff, 429 rate-limit handling
- **Market orders** — Signed quantities (positive BUY, negative SELL)
- **OCO orders** — Paired stop-loss and take-profit with async monitoring
- **Idempotent submission** — Client order ID deduplication prevents accidental re-submission
- **Fill tracking** — Records fill price and status transitions (pending → filled/rejected/error)

---

## OCO Monitoring

- **Parallel leg polling** — Async poll of stop and limit order status every N seconds
- **Auto-close on fill** — Logs trade to TradeJournal with exit reason and P&L
- **Orphan cleanup** — Cancels surviving leg when other reaches terminal state
- **Post-exit cooldown** — Sets model cooldown after SL/TP fill

---

## Scheduler

- **Bar-close timing** — Market-calendar-aware via `exchange_calendars` (NYSE)
- **Intraday intervals** — 1m, 5m, 15m, 1h with epoch modular arithmetic
- **Daily intervals** — 1d with NYSE session-close detection
- **Weekly intervals** — 1wk with Friday market-close detection
- **Market hours gating** — Skips intraday ticks outside market hours unless `extended_hours=True`
- **Safety margins** — Configurable second delay before bar close to account for data lag

---

## Database & State

- **SQLite backend** — Persists signals, orders, positions, equity curve, instruments, sectors
- **Repository pattern** — SignalRepo, OrderRepo, PositionRepo, EquityRepo, etc.
- **Alembic migrations** — Versioned schema (0001–0004+)
- **Trade journal** — Closed trades with entry/exit price, reason, P&L, SL/TP levels
- **Performance tracking** — Rolling window of trades per model for retirement evaluation
- **Instrument cache** — Maps yfinance ticker → T212 instrument_ticker with TTL

---

## Instrument Resolution

- **Priority chain** — `overrides.yaml` → SQLite cache → T212 instruments API
- **Best-match search** — Searches T212 API by ticker prefix and short name
- **Sector seeding** — Populates sector cache on first resolution
- **Fail-loud** — Raises `RuntimeError` if unresolvable; requires manual override

---

## Backtesting

- **Bar-by-bar simulation** — Same inference pipeline as live trading
- **Slippage modeling** — Configurable basis points (default 5 bps)
- **Commission simulation** — Fixed per-trade cost
- **SL/TP triggering** — Simulates intraday high/low against levels
- **Cooldown enforcement** — Identical cooldown logic as live
- **Trade export** — All trades with entry/exit, quantity, P&L, exit reason

---

## Backtest Reporter

- **Summary stats** — Total return, Sharpe ratio, max drawdown, win rate, profit factor
- **Trade analysis** — Avg win/loss, largest win/loss, consecutive wins/losses
- **Equity curve** — Daily equity progression through backtest period
- **Output formats** — Text table, JSON, CSV

---

## REST API (port 8081)

| Endpoint | Description |
|---|---|
| `GET /api/v1/positions` | Open positions with quantity, entry price, cooldown status |
| `GET /api/v1/orders` | Paginated order history with fill prices and status |
| `GET /api/v1/signals` | Recent signals with raw logits and model count |
| `GET /api/v1/pnl` | Daily P&L snapshots with equity and trade counts |
| `GET /api/v1/models` | Loaded models with manifest metadata |
| `GET /api/v1/backtest/runs` | Completed backtest runs with summary stats |
| `GET /api/v1/backtest/runs/<id>` | Single backtest result with all trades |
| `GET /api/v1/health` | 200 if tick recent and T212 reachable |
| `GET /api/v1/equity` | Equity curve timeseries for charting |
| `GET /api/v1/trades` | Closed trade journal with exit reasons and P&L |
| `GET /api/v1/settings` | Current bot config (API keys masked) |
| `PUT /api/v1/settings` | Update live settings without restart |
| `GET /api/v1/stream` | SSE stream of tick events, order fills (API key required) |

---

## Health Monitoring (port 8080)

- **Healthz probe** — 503 if tick stale (> 2× longest interval)
- **Readyz probe** — 503 until models loaded and T212 connectivity confirmed
- **State flags** — `last_tick_at`, `t212_ok`, `models_loaded`

---

## Alerting

- **Webhook delivery** — Non-blocking async queue to custom webhook URL
- **Slack alerts** — Slack-formatted payload with level prefixes
- **Email alerts** — SMTP with auth, configurable from/to and min level
- **Alert levels** — INFO / WARNING / ERROR / CRITICAL with per-channel thresholds
- **Rate limiting** — 30-second per-category window prevents spam
- **Bounded queue** — 128-item queue with daemon worker; drops oldest if full

---

## Kill Switch

- **Sentinel file** — Create/remove `./HALT` file to toggle order submission
- **Env override** — `alphaTrade_HALT=1` pauses submission
- **Graceful pause** — Bot stays alive; inference continues; orders don't submit

---

## Configuration

- **Environment variables** — `T212_API_KEY`, `T212_ENV`, `DATA_PROVIDER`, `POLYGON_API_KEY`, `MODELS_DIR`, `STATE_DB_PATH`
- **overrides.yaml** — Per-model config, global defaults, risk limits, alerts, backtest params
- **Hot-reload** — API settings updates apply immediately (T212Client reinits automatically)
- **Pydantic validation** — Type-safe config with field validators

---

## Metrics & Logging

- **Prometheus** — Counters/histograms: signals, orders, inference latency, positions, daily P&L, equity, T212 request latency/status (port 9090)
- **Structured logging** — Per-module named loggers, JSON-compatible, rotatable log file
