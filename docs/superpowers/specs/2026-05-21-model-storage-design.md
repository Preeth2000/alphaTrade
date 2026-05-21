# Model Storage Design

**Date:** 2026-05-21  
**Status:** Approved  

## Overview

Shared model storage between a producer project (training) and alphaTrade (inference). MinIO provides S3-compatible object storage accessible by both projects, locally via Docker and in production via real S3 or a remote MinIO instance. A `ModelSyncDaemon` inside the bot polls MinIO, validates new model versions against configurable backtest thresholds, and promotes passing models into `models_dir` where the existing `ModelRegistry` hot-reloads them.

## Architecture

```
┌─────────────────────────┐         MinIO          ┌─────────────────────┐
│   Producer project      │                        │   alphaTrade (bot)  │
│                         │  PUT models/{user}/    │                     │
│  train →                │    {account}/          │  ModelSyncDaemon    │
│  backtest (historic) →  │    {run_name}/v{n}/    │  (polls MinIO)      │
│  write backtest.json →  │ ──────────────────────▶│       │             │
│  upload all artifacts   │   manifest.json        │       ▼             │
│                         │   model.onnx           │  ValidationGate     │
└─────────────────────────┘   backtest.json        │  reads backtest.json│
                               metrics.json        │  checks thresholds  │
                               config.snapshot.yaml│  (configurable)     │
                               latest              │       │  pass       │
                                                   │       ▼             │
                                                   │  models_dir/        │
                                                   │  (local cache)      │
                                                   │       │             │
                                                   │       ▼             │
                                                   │  ModelRegistry      │
                                                   │  (hot-reload tick)  │
                                                   └─────────────────────┘
```

**Key boundaries:**
- Producer owns training, backtesting, and proof of quality.
- Consumer owns promotion decision and live serving.
- MinIO is the only coupling point between projects.
- `backtest.json` is the contract between projects.
- `models_dir` unchanged — existing hot-reload works as-is.
- MLflow gets its own `mlflow/` bucket on same MinIO when added later.

## Storage Layout & Versioning

```
models/
└── {user}/
    └── {account}/
        └── {run_name}/
            ├── latest              ← JSON object, updated last after all files uploaded
            ├── v1/
            │   ├── manifest.json
            │   ├── model.onnx
            │   ├── backtest.json
            │   ├── metrics.json
            │   ├── config.snapshot.yaml
            │   └── checkpoints/
            ├── v2/
            └── v3/
```

**Run name convention:** `{ticker}_{arch}` for standard models, `{ticker}_{arch}_{variant}` only when strategy differs meaningfully (e.g. `AAPL_Transformer_highvol`). Hyperparameters are recorded in `config.snapshot.yaml`, not encoded in the name.

**`latest` object contents:**
```json
{
  "version": "v3",
  "uploaded_at": "2026-05-21T10:00:00Z",
  "run_name": "AAPL_Transformer"
}
```

**Versioning rules:**
- Producer lists `{user}/{account}/{run_name}/` in MinIO, finds max `v{n}`, uploads `v{n+1}`. First upload → `v1`.
- `latest` written last — partial uploads never promoted.
- `max_versions` controls retention. `-1` = unbounded. Default `5`.
- After successful promotion, daemon prunes oldest versions beyond limit.

**User/account scoping:**
- `user` = trainer identity. `account` = trading account the model targets.
- Current default: `user=default`, `account=default`.
- When auth lands, real IDs drop in without restructuring storage.

## Producer Upload Contract

Producer must upload all files before writing `latest`. Required files:

| File | Required | Purpose |
|------|----------|---------|
| `manifest.json` | yes | model identity, hash, feature spec |
| `model.onnx` | yes | inference artifact |
| `backtest.json` | yes | validation contract |
| `metrics.json` | yes | training metrics |
| `config.snapshot.yaml` | yes | hyperparams record |
| `checkpoints/` | no | training checkpoints |

**`backtest.json` minimum contract** (consumer reads these keys):
```json
{
  "sharpe_ratio": 1.2,
  "max_drawdown": 0.08,
  "win_rate": 0.52,
  "total_trades": 143,
  "backtest_period": {
    "start": "2024-01-01",
    "end": "2026-01-01"
  }
}
```
Consumer ignores unknown keys — producer can add richer metrics without breaking consumer.

**Producer version flow:**
```
train → backtest (historic playback) →
list MinIO {user}/{account}/{run_name}/ →
next_version = max(existing) + 1, or v1 if empty →
upload all artifacts to v{next_version}/ →
write latest object
```

## Sync Daemon & Validation Gate

`ModelSyncDaemon` runs as an async task inside the bot, same process as the existing tick loop.

**Startup:**
```
scan MinIO for all {user}/{account}/{run_name}/latest
compare against .sync/ version records
download + validate any missing or newer versions
```

**Poll loop** (every `MODEL_SYNC_POLL_INTERVAL` seconds):
```
for each run_name in MinIO:
    read MinIO latest → version string
    read .sync/{run_name} → local version string
    if equal → skip
    if differs → download v{n} to tmp → validate → promote or reject
```

**Local version records** prevent re-download on restart:
```
models_dir/
├── AAPL_Transformer/        ← promoted model
│   ├── manifest.json
│   └── model.onnx
└── .sync/
    └── AAPL_Transformer     ← contains "v3"
```

**Promotion flow:**
```
download v{n} to tmp dir
→ hash check (manifest.model_hash)
→ backtest.json threshold check
→ pass: move to models_dir/{run_name}, write version to .sync/
→ fail: log reason + rejected version, delete tmp, do not retry same version
```

**Validation thresholds** — configurable per-model via existing DB override system, with env var global fallback:
```yaml
validation:
  default:
    min_sharpe: 0.5
    max_drawdown: 0.20
    min_win_rate: 0.45
  AAPL_Transformer:        # per-model override via DB
    min_sharpe: 0.8
```

**Failure modes:**

| Scenario | Behaviour |
|----------|-----------|
| MinIO unreachable | keep serving current models, log warning, retry next poll |
| Model fails validation | log reason, skip, keep current, don't retry same version |
| Download corrupted | hash mismatch → treat as validation fail |
| No models at startup | existing behaviour — bot exits with error |

## Infrastructure

**docker-compose.yml additions:**
```yaml
minio:
  image: minio/minio:latest
  networks:
    - appnet
  command: server /data --console-address ":9001"
  environment:
    MINIO_ROOT_USER: ${MINIO_ROOT_USER:-minioadmin}
    MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD:-minioadmin}
  volumes:
    - minio_data:/data
  ports:
    - "9000:9000"
    - "9001:9001"
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
    interval: 10s
    timeout: 5s
    retries: 5

minio-init:
  image: minio/mc:latest
  networks:
    - appnet
  depends_on:
    minio:
      condition: service_healthy
  entrypoint: >
    /bin/sh -c "
    mc alias set local http://minio:9000 $$MINIO_ROOT_USER $$MINIO_ROOT_PASSWORD;
    mc mb --ignore-existing local/models;
    mc mb --ignore-existing local/mlflow;
    "
  environment:
    MINIO_ROOT_USER: ${MINIO_ROOT_USER:-minioadmin}
    MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD:-minioadmin}

volumes:
  pgdata:
  minio_data:
```

**Bot service env additions (via `.env`):**
```
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=minioadmin
MINIO_BUCKET=models
MODEL_USER=default
MODEL_ACCOUNT=default
MODEL_SYNC_POLL_INTERVAL=60
MODEL_MAX_VERSIONS=5
MODEL_VALIDATION_MIN_SHARPE=0.5
MODEL_VALIDATION_MAX_DRAWDOWN=0.20
MODEL_VALIDATION_MIN_WIN_RATE=0.45
```

`docker-compose.yml` references them:
```yaml
environment:
  - MINIO_ENDPOINT=minio:9000
  - MINIO_ACCESS_KEY=${MINIO_ROOT_USER}
  - MINIO_SECRET_KEY=${MINIO_ROOT_PASSWORD}
  - MINIO_BUCKET=${MINIO_BUCKET}
  - MODEL_USER=${MODEL_USER}
  - MODEL_ACCOUNT=${MODEL_ACCOUNT}
  - MODEL_SYNC_POLL_INTERVAL=${MODEL_SYNC_POLL_INTERVAL}
  - MODEL_MAX_VERSIONS=${MODEL_MAX_VERSIONS}
  - MODEL_VALIDATION_MIN_SHARPE=${MODEL_VALIDATION_MIN_SHARPE}
  - MODEL_VALIDATION_MAX_DRAWDOWN=${MODEL_VALIDATION_MAX_DRAWDOWN}
  - MODEL_VALIDATION_MIN_WIN_RATE=${MODEL_VALIDATION_MIN_WIN_RATE}
```

**Multi-machine prod:** set `MINIO_ENDPOINT` to remote MinIO/S3 URL on both producer and consumer. Everything else unchanged.

## Future: MLflow Integration

MLflow uses the `mlflow/` bucket on the same MinIO instance. No storage infra changes — add one MLflow tracking server service to `docker-compose.yml`. Producer logs experiment runs to MLflow; consumer remains unchanged.
