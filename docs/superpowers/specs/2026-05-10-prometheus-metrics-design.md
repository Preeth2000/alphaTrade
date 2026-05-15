# Prometheus Metrics Export — Design Spec

**Date:** 2026-05-10  
**Issue:** alphaTrade-c9e  
**Priority:** P1

## Goal

Expose bot telemetry on `:9090/metrics` in Prometheus format so bot behavior is observable without reading logs.

## Metrics

### Counters

| Name | Labels | Incremented when |
|------|--------|-----------------|
| `signals_total` | `ticker`, `signal` | Signal saved to DB in tick |
| `orders_total` | `side`, `status` | Order fill attempt resolves (`filled`, `error`, `skipped_duplicate`) |
| `inference_errors_total` | `run_name` | Exception caught in inference block |
| `t212_requests_total` | `endpoint`, `status` | Any T212Client HTTP call completes |

### Gauges

| Name | Set when |
|------|---------|
| `equity_total` | After successful `get_total_equity` each tick |
| `open_positions` | After position upsert/remove in tick |
| `daily_pnl_pct` | After `daily_loss_pct` computed each tick |

### Histograms

| Name | Labels | Measured |
|------|--------|---------|
| `inference_latency_seconds` | `run_name` | Time for `compute_features` → `model.run(x)` per model |
| `t212_request_latency_seconds` | `endpoint` | Wall time of each T212Client HTTP call |

## Architecture

### `alphaTrade/metrics.py`

Module-level singletons using default `prometheus_client` registry. No config, no injection — import and call.

```python
from prometheus_client import Counter, Gauge, Histogram

signals_total = Counter("signals_total", "...", ["ticker", "signal"])
orders_total = Counter("orders_total", "...", ["side", "status"])
inference_errors_total = Counter("inference_errors_total", "...", ["run_name"])
t212_requests_total = Counter("t212_requests_total", "...", ["endpoint", "status"])

equity_total = Gauge("equity_total", "...")
open_positions = Gauge("open_positions", "...")
daily_pnl_pct = Gauge("daily_pnl_pct", "...")

inference_latency_seconds = Histogram("inference_latency_seconds", "...", ["run_name"])
t212_request_latency_seconds = Histogram("t212_request_latency_seconds", "...", ["endpoint"])
```

### Instrumentation touch-points

**`alphaTrade/broker/t212_client.py`** — `_get`, `_post`, `_delete`:
- Wrap each HTTP call with `time.perf_counter()` delta
- Record `t212_requests_total.labels(endpoint=path, status=str(r.status_code)).inc()`
- Record `t212_request_latency_seconds.labels(endpoint=path).observe(elapsed)`
- On exception: record `status="error"`

**`alphaTrade/main.py` tick():**
- `signals_total` — after `signal_repo.save(sig_rec)`
- `orders_total` — after fill resolve (status: `filled`, `error`, `skipped_duplicate`)
- `inference_errors_total` — in the `except` block of the inference loop
- `inference_latency_seconds` — wrap `compute_features` → `model.run(x)` block
- `equity_total`, `open_positions`, `daily_pnl_pct` — after equity/position state known

### Server startup

In `alphaTrade/main.py` `run()`, before scheduler tasks:

```python
from prometheus_client import start_http_server
start_http_server(9090)
log.info("Metrics server listening on :9090")
```

`start_http_server` spawns a daemon thread — no async integration needed.

### Dependencies

Add to `pyproject.toml`:
```
prometheus-client>=0.20
```

## Testing

### Strategy: mock call-site assertions + smoke test

**Why not registry isolation:** metrics are module-level singletons. Injecting a test registry requires restructuring the module. Not worth it for the coverage gained.

**Smoke test** (`tests/unit/test_metrics.py`):
- Import `alphaTrade.metrics`
- Call `prometheus_client.generate_latest()` on the default registry
- Assert output is non-empty and parses without error
- Confirms: no typos in metric definitions, valid Prometheus text format

**Mock tests** — patch `alphaTrade.metrics.*` at call sites in existing tick/t212 tests:
- `test_tick_*`: assert `signals_total.labels(...).inc()` called with correct ticker/signal
- `test_t212_client`: assert `t212_requests_total.labels(endpoint=..., status="200").inc()` called

These catch wiring bugs: wrong label name, missing call, wrong status code.

## Out of Scope

- Grafana dashboard configuration
- Alertmanager rules
- Authentication on `/metrics` endpoint
- Custom histogram buckets (use defaults)
