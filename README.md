# alphaLink

Trading bot that consumes ML model artifacts from [alphaGen](../alphaGen), fetches live OHLCV, and executes orders on Trading212.

## Prerequisites

- Docker + Docker Compose
- A Trading212 API key (Settings → API)
- Model artifacts from alphaGen (`model.onnx` + `manifest.json`)
- Optional: Polygon.io API key (for `DATA_PROVIDER=polygon`)

For local dev without Docker:
```bash
sudo apt-get install -y libta-lib-dev
pip install -e ".[dev]"
```

## Quick start

```bash
cp .env.example .env
# Edit .env with your credentials
mkdir -p models/my_run
cp /path/to/alphaGen/artifacts/my_run/model.onnx  models/my_run/
cp /path/to/alphaGen/artifacts/my_run/manifest.json models/my_run/

docker compose up
```

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `T212_API_KEY` | yes | — | Trading212 API key |
| `T212_ENV` | no | `demo` | `demo` or `live` |
| `DATA_PROVIDER` | no | `yfinance` | `yfinance` or `polygon` |
| `POLYGON_API_KEY` | if polygon | — | Polygon.io API key |
| `MODELS_DIR` | no | `./models` | Directory scanned for artifacts |
| `STATE_DB_PATH` | no | `./state.db` | SQLite audit log path |

## Adding models

Drop any alphaGen artifact directory into `./models/`:

```
models/
└── aapl_daily_mlp_example/
    ├── manifest.json
    └── model.onnx
```

Bot auto-discovers all valid `(manifest.json, model.onnx)` pairs on startup.

Multiple models for the same stock are fused via softmax-averaged logits before the risk gate.

## overrides.yaml

Configure per-model settings and global risk limits:

```yaml
defaults:
  size_pct: 0.10          # 10% equity per BUY
  stop_loss_pct: 0.02
  take_profit_pct: 0.05
  cooldown_bars: 3

risk:
  max_positions: 5
  daily_loss_halt_pct: 0.05  # halt if daily PnL drops >= 5%

models:
  aapl_daily_mlp_example:
    enabled: true
    t212_ticker: AAPL_US_EQ   # omit to auto-resolve via T212 API
    size_pct: 0.05            # override default
```

`t212_ticker` maps the yfinance ticker in `manifest.json` to a T212 instrument ticker. If omitted, bot queries the T212 instruments API and caches the result in `state.db`.

## Verify a model before deploying

```bash
alphalink verify ./models/aapl_daily_mlp_example/
```

Runs: hash check → ONNX smoke test → live OHLCV fetch → inference → prints signal + confidence.

## CLI

```bash
alphalink run               # start daemon
alphalink verify <dir>      # dry-run a model artifact
alphalink status            # open positions + day-open equity
```

## Data providers

**yfinance** (default): free, no API key, daily reliable, intraday delayed.

**Polygon.io**: set `DATA_PROVIDER=polygon` and `POLYGON_API_KEY`. Same US equity ticker format as yfinance (`AAPL`, `MSFT`). T212 instrument resolution is provider-agnostic.

## Architecture

```
Scheduler (bar-close per model interval)
  → DataProvider (yfinance | polygon) → OHLCV
  → Adapter (features → normalize → window → ONNX) → logits
  → Consensus (softmax avg, group by ticker) → signal
  → RiskGate (drawdown halt, cooldown, max positions) → approved?
  → T212Client (MARKET order + SL/TP)
  → SQLite (signals, orders, positions, equity_curve)
```

## Running tests

```bash
pytest tests/unit -v
pytest tests/integration -v   # e2e test requires alphaGen artifact at ../alphaGen/artifacts/
```
