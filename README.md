# alphaTrade

> Live trading bot — loads ONNX models from [alphaGen](../alphaGen), runs bar-close inference, fuses signals across models via softmax consensus, enforces 8 risk gates, and submits Market + OCO bracket orders to Trading 212.

**Stack:** Python 3.11, FastAPI, ONNX Runtime, APScheduler, TA-Lib, SQLModel, Alembic, PostgreSQL, Redis, MLflow · **Ports:** `8081` (API), `8080` (health), `9090` (metrics) · **Repo:** `projectAlpha/alphaTrade/`

---

## What it is

alphaTrade is the execution layer of the platform. It consumes model artifacts (`model.onnx` + `manifest.json`) produced by [alphaGen](../alphaGen), runs live inference at NYSE bar close, and submits orders to Trading 212 via their REST API. [alphaLink](../alphaLink) provides the UI for monitoring positions, managing models, and toggling the kill switch. [alphaKey](../alphaKey) provides JWT authentication.

alphaTrade does **not** use PyTorch — ONNX Runtime only, keeping the runtime image lightweight.

---

## How it works

```
APScheduler (NYSE bar-close per model interval)
  → DataProvider (yfinance | polygon) → OHLCV
  → Adapter (feature build → normalize → window → ONNX Runtime) → logits per model
  → Consensus (softmax-averaged logits grouped by ticker) → signal + confidence
  → RiskGate × 8 (daily loss halt, retirement, cooldown, sector limit,
                   max positions, pyramiding, short-sell prevention, confidence) → approved?
  → T212Client → MARKET order → poll fill (≤30s) → OCO bracket (SL + TP)
  → OCO monitor task (polls SL/TP every 10s via asyncio.to_thread)
  → PostgreSQL (signal, order, position, equity_curve, trade_journal, ...)
```

**Key design rules:**
- **Fail-closed auth** — if alphaKey is unreachable, requests are rejected (not passed through).
- **Kill switch** — creating a `HALT` file at the repo root blocks all order submission. Inference and API continue. Resume via `alphaTrade resume` or `DELETE /api/v1/kill-switch`.
- **Consensus** — multiple models for the same ticker are fused before the risk gate. No individual model can trigger an order; the consensus signal must clear.
- **Config override chain** — `per-model ModelOverrideRecord` DB > `BotSettings` DB (hot-reload via `PUT /api/v1/settings`) > `overrides.yaml` (startup seed, read-only at runtime) > Pydantic defaults.
- **Model sync** — dual-path: Redis `model.ready` pub/sub (primary) + MLflow poll every `MODEL_SYNC_POLL_INTERVAL` seconds (fallback).

---

## Prerequisites

- Docker + Docker Compose (recommended)
- Trading 212 API key (Settings → API in the T212 app)
- Model artifacts from alphaGen (`model.onnx` + `manifest.json`)
- Optional: Polygon.io API key for `DATA_PROVIDER=polygon`

For local dev without Docker:

```bash
sudo apt-get install -y libta-lib-dev
pip install -e ".[dev]"
```

Requires Python ≥ 3.11.

---

## Usage

### Quick start

```bash
cp .env.example .env
# Edit .env with T212 credentials and platform URLs

# Drop alphaGen artifacts into models/
mkdir -p models/my_run
cp /path/to/alphaGen/artifacts/my_run/model.onnx   models/my_run/
cp /path/to/alphaGen/artifacts/my_run/manifest.json models/my_run/

docker compose up
```

Bot auto-discovers all valid `(manifest.json, model.onnx)` pairs in `MODELS_DIR` on startup.

### CLI

```bash
alphaTrade run                          # start daemon
alphaTrade verify ./models/my_run/      # dry-run: hash + ONNX smoke + live OHLCV + inference
alphaTrade status                       # print open positions + day-open equity
```

`verify` runs: hash check → ONNX smoke test → live OHLCV fetch → inference → prints signal + confidence. Use before deploying any new model.

---

## Configuration

Copy `.env.example` → `.env`.

**Override chain:** per-model DB override → `BotSettings` DB (PUT `/api/v1/settings`) → `overrides.yaml` → Pydantic defaults. Only `overrides.yaml` changes require restart; DB settings are hot-reloaded.

**Required:**

| Variable | Purpose |
|---|---|
| `T212_ACTIVE_ACCOUNT` | `demo` \| `invest` \| `isa` |
| `T212_API_KEY` | Trading 212 API key for the active account |
| `DATABASE_URL` | PostgreSQL DSN — `postgresql+asyncpg://platform:<pw>@postgres:5432/alphatrade` |
| `ALPHAKEY_URL` | alphaKey base URL (for JWKS + introspection) |

**Optional:**

| Variable | Default | Purpose |
|---|---|---|
| `DATA_PROVIDER` | `yfinance` | `yfinance` \| `polygon` |
| `POLYGON_API_KEY` | — | Polygon.io key (if `DATA_PROVIDER=polygon`) |
| `MODELS_DIR` | `./models` | Directory scanned for artifact dirs |
| `STATE_DB_PATH` | `./state.db` | SQLite fallback (overridden by `DATABASE_URL`) |
| `API_PORT` | `8081` | FastAPI listen port |
| `AUTH_MODE` | `jwt` | `jwt` (alphaKey-verified) \| `legacy` |
| `ALPHATRADE_API_KEY` | — | Static API key (legacy auth only) |
| `SECRETS_SOURCE` | `db` | `db` (alphaKey vault) \| `env` |
| `DB_SECRETS_KEY` | — | Fernet key for DB-stored secrets |
| `JWT_ISSUER` / `JWT_AUDIENCE` | `alphakey` | Must match alphaKey config |
| `MODEL_SYNC_POLL_INTERVAL` | `60` | MLflow poll interval (seconds) |
| `ALERTS__SLACK__WEBHOOK_URL` | — | Slack alert webhook |
| `ALERTS__EMAIL__*` | — | SMTP alert config |

**`overrides.yaml`** (startup seed — read-only at runtime):

```yaml
defaults:
  size_pct: 0.10           # 10% equity per BUY signal
  stop_loss_pct: 0.02
  take_profit_pct: 0.05
  cooldown_bars: 3
  position_sizing: fixed   # fixed | atr | vix

risk:
  max_positions: 5
  daily_loss_halt_pct: 0.05   # halt if daily PnL drops ≥ 5%

models:
  aapl_daily_mlp_example:
    enabled: true
    t212_ticker: AAPL_US_EQ   # omit to auto-resolve via T212 instruments API
    size_pct: 0.05             # per-model override
```

`t212_ticker` maps the manifest ticker to a T212 instrument. If omitted, the bot queries the T212 instruments API and caches the result.

---

## API

> `Authorization: Bearer <jwt>` required on all routes unless noted. Base path: `/api/v1`.

**Trading state:**

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/positions` | Open positions |
| `GET` | `/orders` | Recent orders |
| `GET` | `/signals` | Recent signals |
| `GET` | `/pnl` | PnL summary |
| `GET` | `/trades` | Trade journal |
| `GET` | `/equity-curve` | Historical equity |
| `GET` | `/stream` | SSE — live trading events |

**Model management:**

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/models` | Registered model list + status |
| `POST` | `/models/{name}/promote` | Promote model to active |
| `POST` | `/models/{name}/demote` | Demote model |
| `GET/PUT` | `/models/{name}/overrides` | Per-model risk overrides |
| `POST` | `/models/{name}/retry-deploy` | Retry failed deployment |
| `POST` | `/models/{name}/fork` | Fork a model config |

**Kill switch:**

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/kill-switch` | Engage kill switch (writes `HALT` file) |
| `DELETE` | `/kill-switch` | Resume trading |
| `GET` | `/kill-switch` | Kill switch status |

**Settings:**

| Method | Path | Purpose |
|---|---|---|
| `GET/PUT` | `/settings` | Bot-wide settings (hot-reload, no restart) |
| `GET/PUT` | `/settings/retirement` | Model retirement thresholds |

**Backtest:**

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/backtest` | Trigger backtest run |
| `GET` | `/backtest/runs` | List backtest runs |

**Health (no auth):**

| Method | Path | Port | Purpose |
|---|---|---|---|
| `GET` | `/health` | `8080` | Kubernetes liveness |
| `GET` | `/healthz` | `8080` | Liveness |
| `GET` | `/readyz` | `8080` | Readiness (DB + Redis) |
| `GET` | `/metrics` | `9090` | Prometheus metrics |

---

## Architecture & Data

**Database:** `alphatrade` (PostgreSQL, 27 Alembic migrations)

| Table group | Key tables |
|---|---|
| Signals & orders | `signal`, `order`, `position`, `tradejournal` |
| Performance | `equitycurve`, `pnlsnapshot`, `model_performance` |
| Model lifecycle | `model_deployments`, `model_adoptions`, `model_override` |
| Configuration | `bot_settings`, `instrumentcache`, `sectorcache` |
| Backtesting | `backtest_run`, `backtest_trade`, `backtest_model_run` |

**Redis (db0):** pub/sub cache, `model.ready` subscription.

**MinIO:** reads from `models` bucket (artifacts published by alphaGen); writes to `trades` bucket.

**Model lifecycle states:** `launching` → `active` → `retired` / `disabled` / `deleted` / `failed`

---

## Tests

```bash
pytest tests/unit -v

# Integration (imports sibling code directly)
pytest tests/integration -v
```

### Contract tests (Pact)

alphaTrade is a **consumer** of [alphaKey](../alphaKey) (JWT verification) and a **provider** for [alphaLink](../alphaLink) (kill-switch, model promote/demote).

```bash
# Consumer — verify alphaTrade's calls to alphaKey match the expected shape
pytest tests/contract/test_alphakey_pact.py -v

# Provider — verify alphaTrade satisfies alphaLink's recorded contract
PACT_VERIFICATION_MODE=true pytest tests/integration/test_pact_provider_verification.py -v -s
```

The provider test starts a live uvicorn server with `AUTH_MODE=jwt` (local EC keypair — no live alphaKey needed), `REDIS__ENABLED=false`, and `MlflowClient` patched to a fake registry. **`PACT_VERIFICATION_MODE` must be `false` outside of Pact verification runs** — the gated endpoint can flip the kill switch and manipulate MLflow state.

---

## Data providers

**yfinance (default):** Free, no API key, daily data reliable, intraday delayed.

**Polygon.io:** Set `DATA_PROVIDER=polygon` and `POLYGON_API_KEY`. Same US equity ticker format as yfinance (`AAPL`, `MSFT`). T212 instrument resolution is provider-agnostic.

---

## Related

| Service | Relationship |
|---|---|
| [alphaFrame](../alphaFrame) | Provides Postgres (`alphatrade` DB), Redis, MinIO, Nginx |
| [alphaKey](../alphaKey) | Auth provider — JWKS, introspection, credential vault |
| [alphaGen](../alphaGen) | Produces ONNX models; alphaTrade subscribes to `model.ready` |
| [alphaLink](../alphaLink) | UI — positions, signals, kill-switch, model management |
| [alphaTest](../alphaTest) | Cross-service regression; `mock_t212` in-process broker |

Full documentation: [`alphaDocs`](../alphaDocs)
