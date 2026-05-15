# Prometheus Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose bot telemetry (signals, orders, inference, T212 HTTP calls, equity) on `:9090/metrics` in Prometheus format.

**Architecture:** Module-level singletons in `alphaTrade/metrics.py` using the default prometheus_client registry. T212Client records request counts and latency inside `_get/_post/_delete`. `tick()` in `main.py` records business metrics. `start_http_server(9090)` starts a daemon thread in `run()` before the scheduler.

**Tech Stack:** `prometheus-client>=0.20`, existing `aiohttp` health server pattern for reference.

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `pyproject.toml` | Modify | Add `prometheus-client>=0.20` dependency |
| `alphaTrade/metrics.py` | Create | All metric definitions (counters, gauges, histograms) |
| `alphaTrade/broker/t212_client.py` | Modify | Record `t212_requests_total` + `t212_request_latency_seconds` in `_get/_post/_delete` |
| `alphaTrade/main.py` | Modify | Record business metrics in `tick()`; call `start_http_server(9090)` in `run()` |
| `tests/unit/test_metrics.py` | Create | Smoke test for metrics module + mock tests for instrumentation |

---

### Task 1: Add prometheus-client dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add dependency**

In `pyproject.toml`, add `"prometheus-client>=0.20",` to the `dependencies` list (after `"pyyaml>=6.0",`):

```toml
dependencies = [
    "onnxruntime>=1.17.0",
    "numpy>=1.26",
    "pandas>=2.2",
    "ta-lib>=0.4.28",
    "yfinance>=0.2.40",
    "polygon-api-client>=1.13",
    "httpx>=0.27",
    "aiohttp>=3.9",
    "tenacity>=8.2",
    "alembic>=1.13",
    "pydantic>=2.7",
    "pydantic-settings>=2.3",
    "sqlmodel>=0.0.19",
    "apscheduler>=3.10",
    "exchange-calendars>=4.5",
    "pyyaml>=6.0",
    "prometheus-client>=0.20",
    "typer>=0.12",
    "rich>=13.0",
    "python-json-logger>=3.2",
]
```

- [ ] **Step 2: Install**

```bash
pip install -e .
```

Expected: installs `prometheus_client` package with no errors.

- [ ] **Step 3: Verify import**

```bash
python -c "import prometheus_client; print(prometheus_client.__version__)"
```

Expected: prints a version string like `0.20.0`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "build: add prometheus-client dependency"
```

---

### Task 2: Create alphaTrade/metrics.py (TDD: smoke test first)

**Files:**
- Create: `tests/unit/test_metrics.py`
- Create: `alphaTrade/metrics.py`

- [ ] **Step 1: Write failing smoke test**

Create `tests/unit/test_metrics.py`:

```python
"""Smoke tests for alphaTrade.metrics — validates metric definitions parse correctly."""
from __future__ import annotations

import pytest
import prometheus_client


def test_metrics_module_exports_all_expected_names():
    import alphaTrade.metrics as m

    assert hasattr(m, "signals_total")
    assert hasattr(m, "orders_total")
    assert hasattr(m, "inference_errors_total")
    assert hasattr(m, "t212_requests_total")
    assert hasattr(m, "equity_total")
    assert hasattr(m, "open_positions")
    assert hasattr(m, "daily_pnl_pct")
    assert hasattr(m, "inference_latency_seconds")
    assert hasattr(m, "t212_request_latency_seconds")


def test_metrics_generate_valid_prometheus_text():
    import alphaTrade.metrics  # noqa: F401 — ensure metrics registered

    output = prometheus_client.generate_latest(prometheus_client.REGISTRY)
    assert len(output) > 0
    # Spot-check that our metric names appear in the output
    assert b"signals_total" in output
    assert b"orders_total" in output
    assert b"equity_total" in output
    assert b"inference_latency_seconds" in output
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_metrics.py -v
```

Expected: `FAILED` with `ModuleNotFoundError: No module named 'alphaTrade.metrics'`

- [ ] **Step 3: Implement alphaTrade/metrics.py**

Create `alphaTrade/metrics.py`:

```python
"""Prometheus metrics definitions. Import this module to register all metrics."""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

signals_total = Counter(
    "signals_total",
    "Consensus signals generated per ticker and direction",
    ["ticker", "signal"],
)

orders_total = Counter(
    "orders_total",
    "Order outcomes after submission attempt",
    ["side", "status"],
)

inference_errors_total = Counter(
    "inference_errors_total",
    "Exceptions caught during model inference",
    ["run_name"],
)

t212_requests_total = Counter(
    "t212_requests_total",
    "Trading212 HTTP requests by endpoint and HTTP status code",
    ["endpoint", "status"],
)

equity_total = Gauge(
    "equity_total",
    "Total portfolio equity in account currency",
)

open_positions = Gauge(
    "open_positions",
    "Number of positions currently held",
)

daily_pnl_pct = Gauge(
    "daily_pnl_pct",
    "Daily P&L as a fraction of today opening equity (negative = loss)",
)

inference_latency_seconds = Histogram(
    "inference_latency_seconds",
    "Wall time for feature computation + model inference per run",
    ["run_name"],
)

t212_request_latency_seconds = Histogram(
    "t212_request_latency_seconds",
    "Wall time for each Trading212 HTTP request",
    ["endpoint"],
)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_metrics.py -v
```

Expected: both tests `PASSED`.

- [ ] **Step 5: Commit**

```bash
git add alphaTrade/metrics.py tests/unit/test_metrics.py
git commit -m "feat: add prometheus metrics definitions (alphaTrade-c9e)"
```

---

### Task 3: Instrument T212Client (TDD)

**Files:**
- Modify: `tests/unit/test_metrics.py` (append new test class)
- Modify: `alphaTrade/broker/t212_client.py`

- [ ] **Step 1: Write failing test for T212Client instrumentation**

Append to `tests/unit/test_metrics.py`:

```python
import respx
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from alphaTrade.broker.t212_client import T212Client

DEMO_BASE = "https://demo.trading212.com/api/v0"


class TestT212ClientMetrics:
    @respx.mock
    def test_successful_get_increments_counter(self):
        respx.get(f"{DEMO_BASE}/equity/account/summary").mock(
            return_value=httpx.Response(200, json={"totalValue": 1000.0, "cash": {}})
        )
        client = T212Client(api_key="test-key", env="demo")
        mock_counter = MagicMock()
        mock_histogram = MagicMock()

        with (
            patch("alphaTrade.broker.t212_client.t212_requests_total", mock_counter),
            patch("alphaTrade.broker.t212_client.t212_request_latency_seconds", mock_histogram),
        ):
            client.get_account_summary()

        mock_counter.labels.assert_called_once_with(
            endpoint="/equity/account/summary", status="200"
        )
        mock_counter.labels.return_value.inc.assert_called_once()
        mock_histogram.labels.assert_called_once_with(endpoint="/equity/account/summary")
        mock_histogram.labels.return_value.observe.assert_called_once()

    @respx.mock
    def test_http_error_records_error_status(self):
        respx.get(f"{DEMO_BASE}/equity/account/summary").mock(
            return_value=httpx.Response(500, json={"error": "server error"})
        )
        client = T212Client(api_key="test-key", env="demo")
        mock_counter = MagicMock()
        mock_histogram = MagicMock()

        with (
            patch("alphaTrade.broker.t212_client.t212_requests_total", mock_counter),
            patch("alphaTrade.broker.t212_client.t212_request_latency_seconds", mock_histogram),
            patch("time.sleep"),
        ):
            try:
                client.get_account_summary()
            except Exception:
                pass

        # Counter must be called for each attempt (3 retries → 3 increments)
        assert mock_counter.labels.call_count >= 1
        # All status labels should be "500"
        for call in mock_counter.labels.call_args_list:
            assert call.kwargs["status"] == "500"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_metrics.py::TestT212ClientMetrics -v
```

Expected: `FAILED` with `AssertionError` — counter not called because instrumentation doesn't exist yet.

- [ ] **Step 3: Instrument T212Client._get, _post, _delete**

In `alphaTrade/broker/t212_client.py`, add import at the top (after existing imports):

```python
import time

from alphaTrade.metrics import t212_request_latency_seconds, t212_requests_total
```

Note: `import time` is already present — only add the metrics import line.

Replace `_get` (lines 36–53) with:

```python
def _get(self, path: str, **params: Any) -> Any:
    url = f"{self._base}{path}"
    for attempt in range(1, _MAX_RETRIES + 1):
        _t0 = time.perf_counter()
        try:
            r = httpx.get(url, headers=self._headers, params=params, timeout=30)
            if r.status_code == 429:
                _handle_429(r, attempt)
                continue
            r.raise_for_status()
            t212_requests_total.labels(endpoint=path, status=str(r.status_code)).inc()
            t212_request_latency_seconds.labels(endpoint=path).observe(time.perf_counter() - _t0)
            return r.json()
        except httpx.HTTPStatusError as exc:
            t212_requests_total.labels(endpoint=path, status=str(exc.response.status_code)).inc()
            t212_request_latency_seconds.labels(endpoint=path).observe(time.perf_counter() - _t0)
            if exc.response.status_code in _NO_RETRY_CODES or attempt == _MAX_RETRIES:
                raise
            time.sleep(min(2 ** attempt, 10))
        except (httpx.TransportError, httpx.ConnectError, httpx.NetworkError):
            t212_requests_total.labels(endpoint=path, status="error").inc()
            t212_request_latency_seconds.labels(endpoint=path).observe(time.perf_counter() - _t0)
            if attempt == _MAX_RETRIES:
                raise
            time.sleep(min(2 ** attempt, 10))
```

Replace `_post` (lines 55–72) with:

```python
def _post(self, path: str, body: dict[str, Any]) -> Any:
    url = f"{self._base}{path}"
    for attempt in range(1, _MAX_RETRIES + 1):
        _t0 = time.perf_counter()
        try:
            r = httpx.post(url, headers=self._headers, json=body, timeout=30)
            if r.status_code == 429:
                _handle_429(r, attempt)
                continue
            r.raise_for_status()
            t212_requests_total.labels(endpoint=path, status=str(r.status_code)).inc()
            t212_request_latency_seconds.labels(endpoint=path).observe(time.perf_counter() - _t0)
            return r.json()
        except httpx.HTTPStatusError as exc:
            t212_requests_total.labels(endpoint=path, status=str(exc.response.status_code)).inc()
            t212_request_latency_seconds.labels(endpoint=path).observe(time.perf_counter() - _t0)
            if exc.response.status_code in _NO_RETRY_CODES or attempt == _MAX_RETRIES:
                raise
            time.sleep(min(2 ** attempt, 10))
        except (httpx.TransportError, httpx.ConnectError, httpx.NetworkError):
            t212_requests_total.labels(endpoint=path, status="error").inc()
            t212_request_latency_seconds.labels(endpoint=path).observe(time.perf_counter() - _t0)
            if attempt == _MAX_RETRIES:
                raise
            time.sleep(min(2 ** attempt, 10))
```

Replace `_delete` (lines 74–91) with:

```python
def _delete(self, path: str) -> None:
    url = f"{self._base}{path}"
    for attempt in range(1, _MAX_RETRIES + 1):
        _t0 = time.perf_counter()
        try:
            r = httpx.delete(url, headers=self._headers, timeout=30)
            if r.status_code == 429:
                _handle_429(r, attempt)
                continue
            r.raise_for_status()
            t212_requests_total.labels(endpoint=path, status=str(r.status_code)).inc()
            t212_request_latency_seconds.labels(endpoint=path).observe(time.perf_counter() - _t0)
            return
        except httpx.HTTPStatusError as exc:
            t212_requests_total.labels(endpoint=path, status=str(exc.response.status_code)).inc()
            t212_request_latency_seconds.labels(endpoint=path).observe(time.perf_counter() - _t0)
            if exc.response.status_code in _NO_RETRY_CODES or attempt == _MAX_RETRIES:
                raise
            time.sleep(min(2 ** attempt, 10))
        except (httpx.TransportError, httpx.ConnectError, httpx.NetworkError):
            t212_requests_total.labels(endpoint=path, status="error").inc()
            t212_request_latency_seconds.labels(endpoint=path).observe(time.perf_counter() - _t0)
            if attempt == _MAX_RETRIES:
                raise
            time.sleep(min(2 ** attempt, 10))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_metrics.py -v
```

Expected: all tests `PASSED`.

- [ ] **Step 5: Run existing T212 tests to check for regressions**

```bash
pytest tests/unit/test_t212_resilience.py tests/unit/test_t212_client_oco.py -v
```

Expected: all `PASSED`.

- [ ] **Step 6: Commit**

```bash
git add alphaTrade/broker/t212_client.py tests/unit/test_metrics.py
git commit -m "feat: instrument T212Client with prometheus metrics (alphaTrade-c9e)"
```

---

### Task 4: Instrument tick() in main.py (TDD)

**Files:**
- Modify: `tests/unit/test_metrics.py` (append new test class)
- Modify: `alphaTrade/main.py`

- [ ] **Step 1: Write failing tests for tick() instrumentation**

Append to `tests/unit/test_metrics.py`:

```python
import asyncio
from pathlib import Path
import numpy as np
import pandas as pd
from sqlmodel import SQLModel, create_engine
from alphaTrade.config import Settings
from alphaTrade.health import HealthState
from alphaTrade.main import make_tick
from alphaTrade.store.repos import Position

_BUY_DF = pd.DataFrame({
    "Open": [150.0], "High": [155.0], "Low": [148.0],
    "Close": [152.0], "Volume": [1_000_000],
})


def _engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    return eng


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        t212_api_key="test-key",
        state_db_path=tmp_path / "state.db",
        models_dir=tmp_path / "models",
        overrides_path=tmp_path / "overrides.yaml",
    )


def _manifest(ticker="AAPL", run_name="run_a", interval="1d"):
    m = MagicMock()
    m.ticker = ticker
    m.run_name = run_name
    m.interval = interval
    m.window = 20
    m.feature_names = ["Close"]
    m.normalize_mean = {}
    m.normalize_std = {}
    return m


def _registry_with_signal(manifest, signal_output):
    model = MagicMock()
    # signal_output: np.array where argmax determines signal
    model.run.return_value = signal_output
    reg = MagicMock()
    reg.refresh = AsyncMock()
    reg.snapshot_by_interval.return_value = {"1d": [(manifest, model)]}
    reg.by_run_name = {manifest.run_name: (manifest, model)}
    return reg


class TestTickMetrics:
    @pytest.mark.asyncio
    async def test_signals_total_incremented_on_signal(self, tmp_path):
        manifest = _manifest()
        # HOLD signal (argmax=2 → "HOLD") — still records signal
        registry = _registry_with_signal(manifest, np.array([-1.0, -1.0, 2.0]))
        t212 = MagicMock()
        t212.get_total_equity.return_value = 10_000.0
        provider = MagicMock()
        provider.fetch_ohlcv.return_value = _BUY_DF

        mock_signals_total = MagicMock()
        mock_equity_total = MagicMock()
        mock_open_positions = MagicMock()
        mock_daily_pnl_pct = MagicMock()

        tick = make_tick(
            "1d",
            registry=registry,
            settings=_settings(tmp_path),
            engine=_engine(),
            t212=t212,
            provider=provider,
            health_state=HealthState(),
            oco_tasks=set(),
            static_map={"AAPL": "AAPL_US_EQ"},
        )

        with (
            patch("alphaTrade.main.compute_features", return_value=_BUY_DF),
            patch("alphaTrade.main.normalize", return_value=_BUY_DF),
            patch("alphaTrade.main.build_input", return_value=np.zeros((1, 1))),
            patch("alphaTrade.main.consensus_by_ticker", return_value={"AAPL": "HOLD"}),
            patch("alphaTrade.main.signals_total", mock_signals_total),
            patch("alphaTrade.main.equity_total", mock_equity_total),
            patch("alphaTrade.main.open_positions", mock_open_positions),
            patch("alphaTrade.main.daily_pnl_pct", mock_daily_pnl_pct),
        ):
            await tick()

        mock_signals_total.labels.assert_called_once_with(ticker="AAPL", signal="HOLD")
        mock_signals_total.labels.return_value.inc.assert_called_once()
        mock_equity_total.set.assert_called_once_with(10_000.0)
        mock_daily_pnl_pct.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_inference_errors_total_on_exception(self, tmp_path):
        manifest = _manifest()
        registry = _registry_with_signal(manifest, np.array([-1.0, -1.0, 2.0]))
        t212 = MagicMock()
        t212.get_total_equity.return_value = 10_000.0
        provider = MagicMock()
        provider.fetch_ohlcv.return_value = _BUY_DF

        mock_inference_errors = MagicMock()

        tick = make_tick(
            "1d",
            registry=registry,
            settings=_settings(tmp_path),
            engine=_engine(),
            t212=t212,
            provider=provider,
            health_state=HealthState(),
            oco_tasks=set(),
            static_map={"AAPL": "AAPL_US_EQ"},
        )

        with (
            patch("alphaTrade.main.compute_features", side_effect=ValueError("bad features")),
            patch("alphaTrade.main.inference_errors_total", mock_inference_errors),
        ):
            await tick()

        mock_inference_errors.labels.assert_called_once_with(run_name="run_a")
        mock_inference_errors.labels.return_value.inc.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_metrics.py::TestTickMetrics -v
```

Expected: `FAILED` — `AttributeError` on `alphaTrade.main.signals_total` (not imported yet).

- [ ] **Step 3: Add metric imports to main.py**

At the top of `alphaTrade/main.py`, after the existing first-party imports block, add:

```python
from alphaTrade.metrics import (
    daily_pnl_pct as metric_daily_pnl_pct,
    equity_total as metric_equity_total,
    inference_errors_total,
    inference_latency_seconds,
    open_positions as metric_open_positions,
    orders_total,
    signals_total,
)
```

Note: `daily_pnl_pct`, `equity_total`, and `open_positions` are aliased to avoid shadowing local variables of the same name already used in `tick()`.

- [ ] **Step 4: Add metric calls in tick() — signals and inference**

In `tick()`, inside the `for manifest, model in interval_models:` loop, replace the existing try/except block:

```python
        for manifest, model in interval_models:
            try:
                t0 = time.perf_counter()
                df = provider.fetch_ohlcv(manifest.ticker, manifest.interval, manifest.window)
                features = compute_features(df, manifest.feature_names)
                features = features.dropna()
                features = normalize(features, manifest)
                x = build_input(features, manifest)
                logits = model.run(x)
                inference_latency_seconds.labels(run_name=manifest.run_name).observe(
                    time.perf_counter() - t0
                )
                ticker_logits[manifest.ticker].append(logits)
                ticker_manifest[manifest.ticker] = manifest
            except Exception as exc:
                inference_errors_total.labels(run_name=manifest.run_name).inc()
                log.error("Inference error for %s: %s", manifest.run_name, exc)
```

Note: `import time` is already at the top of `main.py` — no new import needed.

- [ ] **Step 5: Add metric calls in tick() — equity, positions, pnl, signals**

After `eq_repo.record(equity)` (the line that records equity to DB), add:

```python
            metric_equity_total.set(equity)
```

After `daily_loss_pct = (equity - today_open) / (today_open or 1)`, add:

```python
            metric_daily_pnl_pct.set(daily_loss_pct)
```

After `signal_repo.save(sig_rec)`, add:

```python
                signals_total.labels(ticker=yf_ticker, signal=signal).inc()
```

- [ ] **Step 6: Add metric calls in tick() — orders**

After `log.info("Duplicate order skipped (cid=%s)", cid)` (inside the `if resp.get("skipped_duplicate"):` block), add:

```python
                        orders_total.labels(side=signal, status="skipped_duplicate").inc()
```

After `log.info("Filled %s %s qty=%s", signal, t212_ticker, qty)`, add:

```python
                    orders_total.labels(side=signal, status="filled").inc()
```

After `order_repo.update_fill(err_rec.id, "error", None, "")` (in the outer `except Exception` block for order failures), add:

```python
                    orders_total.labels(side=signal, status="error").inc()
```

- [ ] **Step 7: Add open_positions gauge**

After `pos_repo.upsert(Position(...))` for BUY signal (inside `if signal == "BUY":`), add:

```python
                        metric_open_positions.set(len(pos_repo.all()))
```

After `pos_repo.upsert(Position(...))` for SELL signal (inside `elif signal == "SELL":`), add:

```python
                        metric_open_positions.set(len(pos_repo.all()))
```

- [ ] **Step 8: Run new tests**

```bash
pytest tests/unit/test_metrics.py -v
```

Expected: all tests `PASSED`.

- [ ] **Step 9: Run full test suite to catch regressions**

```bash
pytest tests/unit/ -v
```

Expected: all `PASSED` (or same failures as before this task).

- [ ] **Step 10: Commit**

```bash
git add alphaTrade/main.py tests/unit/test_metrics.py
git commit -m "feat: instrument tick() with prometheus business metrics (alphaTrade-c9e)"
```

---

### Task 5: Wire start_http_server(9090) in run()

**Files:**
- Modify: `alphaTrade/main.py`

No unit test for this step — `start_http_server` spawns a background daemon thread; verifying it in unit tests requires binding a real port.

- [ ] **Step 1: Add start_http_server call in run()**

In `alphaTrade/main.py`'s `run()` function, after `configure_logging(...)` and before the `provider = _build_data_provider(settings)` line, add:

```python
    from prometheus_client import start_http_server as _start_metrics
    try:
        _start_metrics(9090)
        log.info("Metrics server listening on :9090")
    except OSError as exc:
        log.error("Metrics server failed to start on :9090: %s", exc)
```

- [ ] **Step 2: Run tests to confirm nothing broke**

```bash
pytest tests/unit/ -v
```

Expected: all `PASSED`.

- [ ] **Step 3: Commit**

```bash
git add alphaTrade/main.py
git commit -m "feat: expose prometheus metrics on :9090 (alphaTrade-c9e)"
```

---

### Task 6: Manual smoke verification

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/ -v
```

Expected: all pass (or same pre-existing failures, none new).

- [ ] **Step 2: Verify metrics endpoint reachable**

If you have a model loaded and T212 credentials, start the bot:

```bash
alphaTrade run
```

In another terminal:

```bash
curl -s http://localhost:9090/metrics | head -40
```

Expected: Prometheus text format output containing lines like:
```
# HELP signals_total Consensus signals generated per ticker and direction
# TYPE signals_total counter
# HELP equity_total Total portfolio equity in account currency
# TYPE equity_total gauge
```

- [ ] **Step 3: Close issue**

```bash
bd close alphaTrade-c9e
```
