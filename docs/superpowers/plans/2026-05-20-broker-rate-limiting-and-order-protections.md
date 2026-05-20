# Broker Rate Limiting & Order Protections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-endpoint rate limiting, a persistent cross-tick priority queue, stale/dedup guards, submission metrics, and backtest OCO lag simulation to the alphaTrade broker layer.

**Architecture:** A new `AsyncBroker` wraps `T212Client` with an `asyncio.PriorityQueue` drained by a single background task. Tick coroutines call `broker.enqueue()` and return immediately; the drain task handles throttling, staleness, dedup, submission, and fires a post-fill callback registered from `main.py`. A separate `EndpointThrottle` enforces per-endpoint min-gap delays derived from `T212ThrottleConfig` in `settings.yaml`.

**Tech Stack:** Python 3.11, asyncio, pydantic-settings, prometheus-client, pytest, unittest.mock

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `alphaTrade/config.py` | Modify | Add `T212ThrottleConfig`, `T212ExecutorConfig`, `ExecutorsConfig`; add risk + backtest fields |
| `alphaTrade/metrics.py` | Modify | Add 4 new broker metrics |
| `alphaTrade/broker/throttle.py` | Create | `EndpointThrottle` — per-endpoint async rate limiter |
| `alphaTrade/broker/order_queue.py` | Create | `OrderRequest`, `OrderResult`, `order_priority()` |
| `alphaTrade/broker/async_broker.py` | Create | `AsyncBroker` — queue, drain task, stale/dedup, execution |
| `alphaTrade/main.py` | Modify | Equity throttle, broker instantiation, tick enqueue, post-fill callback, startup/shutdown |
| `alphaTrade/backtest/engine.py` | Modify | Per-bar OCO lag simulation |
| `tests/unit/test_throttle.py` | Create | Unit tests for `EndpointThrottle` |
| `tests/unit/test_order_queue.py` | Create | Unit tests for priority and dataclasses |
| `tests/unit/test_async_broker.py` | Create | Unit tests for enqueue, drain, stale, dedup, execution |
| `tests/unit/test_backtest_oco_lag.py` | Create | Unit tests for OCO lag computation |

---

## Task 1: Config additions

**Files:**
- Modify: `alphaTrade/config.py`
- Test: `tests/unit/test_config_extensions.py` (already exists — add to it)

- [ ] **Step 1: Write failing tests**

```python
# Add to tests/unit/test_config_extensions.py

def test_t212_throttle_config_defaults():
    from alphaTrade.config import T212ThrottleConfig
    cfg = T212ThrottleConfig()
    assert cfg.orders_stop_min_gap_secs == 2.0
    assert cfg.orders_limit_min_gap_secs == 2.0
    assert cfg.orders_market_min_gap_secs == 1.2
    assert cfg.account_cash_min_gap_secs == 5.0


def test_executors_config_nested():
    from alphaTrade.config import ExecutorsConfig
    cfg = ExecutorsConfig()
    assert cfg.trading212.throttle.orders_stop_min_gap_secs == 2.0


def test_settings_has_executors():
    from alphaTrade.config import Settings
    s = Settings()
    assert s.executors.trading212.throttle.account_cash_min_gap_secs == 5.0


def test_risk_config_queue_fields():
    from alphaTrade.config import RiskConfig
    cfg = RiskConfig()
    assert cfg.order_stale_window_multiplier == 0.5
    assert cfg.order_queue_max_depth == 50


def test_backtest_config_simulate_oco_lag_default_false():
    from alphaTrade.config import BacktestConfig
    cfg = BacktestConfig()
    assert cfg.simulate_oco_lag is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/preeth/projects/alphaTrade
pytest tests/unit/test_config_extensions.py::test_t212_throttle_config_defaults tests/unit/test_config_extensions.py::test_executors_config_nested tests/unit/test_config_extensions.py::test_settings_has_executors tests/unit/test_config_extensions.py::test_risk_config_queue_fields tests/unit/test_config_extensions.py::test_backtest_config_simulate_oco_lag_default_false -v
```
Expected: FAIL with `ImportError` or `AttributeError`

- [ ] **Step 3: Add config classes**

Add to `alphaTrade/config.py` after the existing `VixSizingConfig` class and before `AlertSlackConfig`:

```python
class T212ThrottleConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    orders_market_min_gap_secs: float = 1.2
    orders_stop_min_gap_secs: float = 2.0
    orders_limit_min_gap_secs: float = 2.0
    orders_cancel_min_gap_secs: float = 1.2
    account_cash_min_gap_secs: float = 5.0
    portfolio_min_gap_secs: float = 1.0
    orders_status_min_gap_secs: float = 1.0


class T212ExecutorConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    throttle: T212ThrottleConfig = T212ThrottleConfig()


class ExecutorsConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    trading212: T212ExecutorConfig = T212ExecutorConfig()
```

- [ ] **Step 4: Add fields to `RiskConfig`**

In `alphaTrade/config.py`, add to `RiskConfig` after `dangerously_allow_pyramid`:

```python
    order_stale_window_multiplier: float = 0.5
    order_queue_max_depth: int = 50
```

- [ ] **Step 5: Add field to `BacktestConfig`**

In `alphaTrade/config.py`, add to `BacktestConfig` after `lookback_days`:

```python
    simulate_oco_lag: bool = False
```

- [ ] **Step 6: Add `executors` to `Settings` and wire overrides**

In `alphaTrade/config.py`, add to `Settings` after `backtest: BacktestConfig = BacktestConfig()`:

```python
    executors: ExecutorsConfig = ExecutorsConfig()
```

In the `_load_overrides` model_validator, after the `if "backtest" in raw:` block, add:

```python
            if "executors" in raw:
                self.executors = ExecutorsConfig(**raw["executors"])
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
pytest tests/unit/test_config_extensions.py::test_t212_throttle_config_defaults tests/unit/test_config_extensions.py::test_executors_config_nested tests/unit/test_config_extensions.py::test_settings_has_executors tests/unit/test_config_extensions.py::test_risk_config_queue_fields tests/unit/test_config_extensions.py::test_backtest_config_simulate_oco_lag_default_false -v
```
Expected: all PASS

- [ ] **Step 8: Commit**

```bash
git add alphaTrade/config.py tests/unit/test_config_extensions.py
git commit -m "feat(config): add T212ThrottleConfig, ExecutorsConfig, order queue risk fields"
```

---

## Task 2: New Prometheus metrics

**Files:**
- Modify: `alphaTrade/metrics.py`
- Test: `tests/unit/test_metrics.py` (already exists — add to it)

- [ ] **Step 1: Write failing tests**

```python
# Add to tests/unit/test_metrics.py

def test_new_broker_metrics_importable():
    from alphaTrade.metrics import (
        order_submission_age_seconds,
        orders_stale_dropped_total,
        orders_deduped_total,
        order_throttle_wait_seconds,
    )
    assert order_submission_age_seconds is not None
    assert orders_stale_dropped_total is not None
    assert orders_deduped_total is not None
    assert order_throttle_wait_seconds is not None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_metrics.py::test_new_broker_metrics_importable -v
```
Expected: FAIL with `ImportError`

- [ ] **Step 3: Add metrics to `alphaTrade/metrics.py`**

```python
order_submission_age_seconds = Histogram(
    "order_submission_age_seconds",
    "Seconds from signal generation to order submission",
    ["interval", "side"],
)

orders_stale_dropped_total = Counter(
    "orders_stale_dropped_total",
    "Orders dropped because signal was too old at dequeue time",
    ["interval", "ticker"],
)

orders_deduped_total = Counter(
    "orders_deduped_total",
    "Orders skipped due to same ticker+side+bar already queued",
    ["ticker", "side"],
)

order_throttle_wait_seconds = Histogram(
    "order_throttle_wait_seconds",
    "Seconds spent waiting for endpoint throttle before submission",
    ["endpoint"],
)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/unit/test_metrics.py::test_new_broker_metrics_importable -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add alphaTrade/metrics.py tests/unit/test_metrics.py
git commit -m "feat(metrics): add order queue and throttle prometheus metrics"
```

---

## Task 3: EndpointThrottle

**Files:**
- Create: `alphaTrade/broker/throttle.py`
- Create: `tests/unit/test_throttle.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_throttle.py
"""Tests for EndpointThrottle per-endpoint async rate limiter."""
from __future__ import annotations
import asyncio
import time
import pytest
from alphaTrade.broker.throttle import EndpointThrottle
from alphaTrade.config import T212ThrottleConfig


@pytest.mark.asyncio
async def test_acquire_returns_immediately_when_no_prior_call():
    throttle = EndpointThrottle({"orders_stop": 2.0})
    t0 = time.monotonic()
    wait = await throttle.acquire("orders_stop")
    elapsed = time.monotonic() - t0
    assert elapsed < 0.1
    assert wait == pytest.approx(0.0, abs=0.05)


@pytest.mark.asyncio
async def test_acquire_waits_min_gap_on_second_call(monkeypatch):
    throttle = EndpointThrottle({"orders_stop": 0.1})
    await throttle.acquire("orders_stop")
    t0 = time.monotonic()
    await throttle.acquire("orders_stop")
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.08  # at least 80% of gap


@pytest.mark.asyncio
async def test_acquire_unknown_endpoint_does_not_block():
    throttle = EndpointThrottle({"orders_stop": 2.0})
    t0 = time.monotonic()
    wait = await throttle.acquire("unknown_endpoint")
    elapsed = time.monotonic() - t0
    assert elapsed < 0.1
    assert wait == 0.0


@pytest.mark.asyncio
async def test_from_t212_config_maps_fields():
    cfg = T212ThrottleConfig(orders_stop_min_gap_secs=3.0, account_cash_min_gap_secs=6.0)
    throttle = EndpointThrottle.from_t212_config(cfg)
    assert throttle._min_gap["orders_stop"] == 3.0
    assert throttle._min_gap["account_cash"] == 6.0


@pytest.mark.asyncio
async def test_different_endpoints_do_not_block_each_other():
    throttle = EndpointThrottle({"orders_stop": 0.2, "account_cash": 0.2})
    await throttle.acquire("orders_stop")
    t0 = time.monotonic()
    await throttle.acquire("account_cash")  # different endpoint — no wait
    elapsed = time.monotonic() - t0
    assert elapsed < 0.05
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_throttle.py -v
```
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `alphaTrade/broker/throttle.py`**

```python
"""Per-endpoint async rate limiter using last-call timestamp."""
from __future__ import annotations

import asyncio
import time


class EndpointThrottle:
    """Enforces a minimum gap between calls to each named endpoint."""

    def __init__(self, min_gaps: dict[str, float]) -> None:
        self._min_gap: dict[str, float] = dict(min_gaps)
        self._last_call: dict[str, float] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    @classmethod
    def from_t212_config(cls, cfg) -> "EndpointThrottle":
        """Build from a T212ThrottleConfig instance."""
        return cls({
            "orders_market": cfg.orders_market_min_gap_secs,
            "orders_stop": cfg.orders_stop_min_gap_secs,
            "orders_limit": cfg.orders_limit_min_gap_secs,
            "orders_cancel": cfg.orders_cancel_min_gap_secs,
            "account_cash": cfg.account_cash_min_gap_secs,
            "portfolio": cfg.portfolio_min_gap_secs,
            "orders_status": cfg.orders_status_min_gap_secs,
        })

    async def acquire(self, endpoint: str) -> float:
        """Wait until endpoint is within rate limit. Returns seconds waited."""
        gap = self._min_gap.get(endpoint, 0.0)
        if gap <= 0.0:
            return 0.0
        async with self._lock:
            now = time.monotonic()
            last = self._last_call.get(endpoint, 0.0)
            wait = (last + gap) - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call[endpoint] = time.monotonic()
            return max(0.0, wait)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_throttle.py -v
```
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add alphaTrade/broker/throttle.py tests/unit/test_throttle.py
git commit -m "feat(broker): add EndpointThrottle per-endpoint async rate limiter"
```

---

## Task 4: OrderRequest, OrderResult, order_priority

**Files:**
- Create: `alphaTrade/broker/order_queue.py`
- Create: `tests/unit/test_order_queue.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_order_queue.py
"""Tests for OrderRequest, OrderResult, and order_priority."""
from __future__ import annotations
from datetime import datetime
import pytest
from alphaTrade.broker.order_queue import OrderRequest, OrderResult, order_priority


def test_sell_always_priority_zero():
    assert order_priority("SELL", "1m") == 0
    assert order_priority("SELL", "1d") == 0


def test_buy_priority_by_interval_short_first():
    p_1m = order_priority("BUY", "1m")
    p_5m = order_priority("BUY", "5m")
    p_1h = order_priority("BUY", "1h")
    p_1d = order_priority("BUY", "1d")
    assert p_1m < p_5m < p_1h < p_1d


def test_sell_beats_all_buys():
    sell_p = order_priority("SELL", "1m")
    buy_p = order_priority("BUY", "1m")
    assert sell_p < buy_p


def test_unknown_interval_falls_back_to_middle():
    p = order_priority("BUY", "3m")
    assert 1 <= p <= 8


def _make_request(ticker="AAPL", side="BUY", interval="1m") -> OrderRequest:
    return OrderRequest(
        t212_ticker=ticker,
        side=side,
        quantity=1.0,
        client_order_id="cid-001",
        interval=interval,
        signal_ts=datetime.utcnow(),
        yf_ticker=ticker,
        stop_loss_pct=0.02,
        take_profit_pct=0.05,
        entry_price=100.0,
        run_name="model-a",
        bar_close_iso="2026-01-01T09:00:00Z",
    )


def test_order_request_fields():
    req = _make_request()
    assert req.t212_ticker == "AAPL"
    assert req.side == "BUY"


def test_order_result_default_fields():
    req = _make_request()
    result = OrderResult(request=req, status="filled")
    assert result.fill_price is None
    assert result.stop_order_id == ""
    assert result.error == ""
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_order_queue.py -v
```
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `alphaTrade/broker/order_queue.py`**

```python
"""OrderRequest, OrderResult dataclasses and priority logic for the broker queue."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

_INTERVAL_ORDER = ["1m", "5m", "15m", "1h", "4h", "1d", "1wk"]


def order_priority(side: str, interval: str) -> int:
    """Lower number = higher priority.

    SELL=0 (always first — exit positions before opening new ones).
    BUY priority by interval urgency: shorter interval = more time-sensitive = lower number.
    """
    if side == "SELL":
        return 0
    idx = _INTERVAL_ORDER.index(interval) if interval in _INTERVAL_ORDER else 3
    return 1 + idx  # BUY/1m=1 ... BUY/1wk=7


@dataclass
class OrderRequest:
    t212_ticker: str
    side: str           # "BUY" | "SELL"
    quantity: float
    client_order_id: str
    interval: str
    signal_ts: datetime
    yf_ticker: str
    stop_loss_pct: float
    take_profit_pct: float
    entry_price: float
    run_name: str
    bar_close_iso: str


@dataclass
class OrderResult:
    request: OrderRequest
    status: str         # "filled" | "stale_dropped" | "deduped" | "failed" | "shutdown_dropped"
    fill_price: float | None = None
    t212_order_id: str = ""
    stop_order_id: str = ""
    limit_order_id: str = ""
    submitted_at: datetime | None = None
    error: str = ""
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_order_queue.py -v
```
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add alphaTrade/broker/order_queue.py tests/unit/test_order_queue.py
git commit -m "feat(broker): add OrderRequest, OrderResult, order_priority"
```

---

## Task 5: AsyncBroker — queue, enqueue, dedup, stale

**Files:**
- Create: `alphaTrade/broker/async_broker.py`
- Create: `tests/unit/test_async_broker.py`

- [ ] **Step 1: Write failing tests for enqueue, stale, dedup**

```python
# tests/unit/test_async_broker.py
"""Tests for AsyncBroker queue management."""
from __future__ import annotations
import asyncio
import itertools
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from alphaTrade.broker.order_queue import OrderRequest, OrderResult, order_priority
from alphaTrade.broker.throttle import EndpointThrottle
from alphaTrade.broker.async_broker import AsyncBroker


def _throttle() -> EndpointThrottle:
    return EndpointThrottle({})  # zero gaps for tests


def _t212() -> MagicMock:
    t212 = MagicMock()
    t212.place_market_order.return_value = {"id": "ord-1", "fillPrice": 101.0}
    t212.place_stop_order.return_value = {"id": "stop-1"}
    t212.place_limit_order.return_value = {"id": "limit-1"}
    return t212


def _make_request(
    ticker="AAPL",
    side="BUY",
    interval="1m",
    signal_ts=None,
    bar_close_iso="2026-01-01T09:00:00Z",
) -> OrderRequest:
    return OrderRequest(
        t212_ticker=ticker,
        side=side,
        quantity=1.0,
        client_order_id=f"cid-{ticker}-{side}",
        interval=interval,
        signal_ts=signal_ts or datetime.utcnow(),
        yf_ticker=ticker,
        stop_loss_pct=0.02,
        take_profit_pct=0.05,
        entry_price=100.0,
        run_name="model-a",
        bar_close_iso=bar_close_iso,
    )


def test_enqueue_adds_to_queue():
    broker = AsyncBroker(t212=_t212(), throttle=_throttle())
    broker.enqueue(_make_request())
    assert broker._queue.qsize() == 1


def test_enqueue_dedup_same_bar_same_ticker_side():
    broker = AsyncBroker(t212=_t212(), throttle=_throttle())
    req1 = _make_request(bar_close_iso="2026-01-01T09:00:00Z")
    req2 = _make_request(bar_close_iso="2026-01-01T09:00:00Z")
    broker.enqueue(req1)
    broker.enqueue(req2)
    assert broker._queue.qsize() == 1  # second dropped


def test_enqueue_allows_different_bar_close():
    broker = AsyncBroker(t212=_t212(), throttle=_throttle())
    req1 = _make_request(bar_close_iso="2026-01-01T09:00:00Z")
    req2 = _make_request(bar_close_iso="2026-01-01T09:01:00Z")
    broker.enqueue(req1)
    broker.enqueue(req2)
    assert broker._queue.qsize() == 2


def test_enqueue_respects_max_queue_depth_evicts_lowest_priority():
    broker = AsyncBroker(t212=_t212(), throttle=_throttle(), max_queue_depth=2)
    # Fill queue with low-priority orders (1d BUYs)
    broker.enqueue(_make_request(ticker="AAA", interval="1d", bar_close_iso="2026-01-01T09:00:00Z"))
    broker.enqueue(_make_request(ticker="BBB", interval="1d", bar_close_iso="2026-01-01T09:00:00Z"))
    assert broker._queue.qsize() == 2
    # Now enqueue a high-priority SELL — should evict a 1d BUY
    broker.enqueue(_make_request(ticker="CCC", side="SELL", interval="1d", bar_close_iso="2026-01-01T09:00:00Z"))
    assert broker._queue.qsize() == 2  # still 2, one 1d evicted


@pytest.mark.asyncio
async def test_stale_order_dropped_by_drain():
    broker = AsyncBroker(t212=_t212(), throttle=_throttle(), stale_window_multiplier=0.5)
    callback_results = []

    async def cb(result: OrderResult) -> None:
        callback_results.append(result)

    broker.set_callback(cb)

    stale_ts = datetime.utcnow() - timedelta(seconds=200)  # 1m stale window = 30s; 200s >> 30s
    req = _make_request(signal_ts=stale_ts, interval="1m")
    broker.enqueue(req)

    await broker.start_drain()
    await asyncio.sleep(0.1)
    await broker.stop_drain()

    assert len(callback_results) == 1
    assert callback_results[0].status == "stale_dropped"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_async_broker.py -v
```
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `alphaTrade/broker/async_broker.py` (queue + enqueue + stale/dedup)**

```python
"""AsyncBroker: priority queue, rate-limited drain task, order execution."""
from __future__ import annotations

import asyncio
import itertools
import logging
from datetime import datetime
from typing import Awaitable, Callable

from alphaTrade.broker.order_queue import OrderRequest, OrderResult, order_priority
from alphaTrade.broker.throttle import EndpointThrottle
from alphaTrade.broker.t212_client import T212Client
from alphaTrade.metrics import (
    order_submission_age_seconds,
    order_throttle_wait_seconds,
    orders_deduped_total,
    orders_stale_dropped_total,
)

log = logging.getLogger(__name__)

PostFillCallback = Callable[[OrderResult], Awaitable[None]]

_INTERVAL_SECONDS: dict[str, int] = {
    "1m": 60, "5m": 300, "15m": 900, "1h": 3600,
    "4h": 14400, "1d": 86400, "1wk": 604800,
}


class AsyncBroker:
    """Single drain task processes all orders across ticks in priority order."""

    def __init__(
        self,
        t212: T212Client,
        throttle: EndpointThrottle,
        stale_window_multiplier: float = 0.5,
        max_queue_depth: int = 50,
    ) -> None:
        self._t212 = t212
        self._throttle = throttle
        self._stale_multiplier = stale_window_multiplier
        self._max_depth = max_queue_depth
        # (priority, seq, request) — seq breaks ties without comparing dataclasses
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._seq = itertools.count()
        # dedup: (t212_ticker, side, bar_close_iso) — no TTL needed
        self._seen: set[tuple[str, str, str]] = set()
        # heap-mirror for priority eviction (list of same tuples as queue)
        self._heap_mirror: list[tuple[int, int, OrderRequest]] = []
        self._drain_task: asyncio.Task | None = None
        self._stopping: bool = False
        self._callback: PostFillCallback | None = None

    def set_callback(self, cb: PostFillCallback) -> None:
        self._callback = cb

    def enqueue(self, request: OrderRequest) -> None:
        """Add order to priority queue. Drops duplicates and lowest-priority on overflow."""
        dedup_key = (request.t212_ticker, request.side, request.bar_close_iso)
        if dedup_key in self._seen:
            orders_deduped_total.labels(
                ticker=request.t212_ticker, side=request.side
            ).inc()
            log.debug("Dedup: skipping %s %s bar=%s", request.side, request.t212_ticker, request.bar_close_iso)
            return

        priority = order_priority(request.side, request.interval)
        seq = next(self._seq)

        if self._queue.qsize() >= self._max_depth:
            # Find lowest-priority item in mirror
            if not self._heap_mirror:
                return  # nothing to evict
            worst_priority, worst_seq, worst_req = max(self._heap_mirror, key=lambda x: x[0])
            if worst_priority <= priority:
                log.warning(
                    "Queue full: dropping new %s %s (priority %d >= worst %d)",
                    request.side, request.t212_ticker, priority, worst_priority,
                )
                return
            # Remove worst from mirror and seen; new item takes its slot
            self._heap_mirror.remove((worst_priority, worst_seq, worst_req))
            worst_key = (worst_req.t212_ticker, worst_req.side, worst_req.bar_close_iso)
            self._seen.discard(worst_key)
            # Can't remove from asyncio.PriorityQueue — mark evicted items via seen discard;
            # drain task skips items whose dedup_key is no longer in seen
            log.info(
                "Queue full: evicted %s %s to make room for %s %s",
                worst_req.side, worst_req.t212_ticker, request.side, request.t212_ticker,
            )

        self._seen.add(dedup_key)
        self._heap_mirror.append((priority, seq, request))
        self._queue.put_nowait((priority, seq, request))

    async def start_drain(self) -> None:
        self._stopping = False
        self._drain_task = asyncio.create_task(self._drain_loop(), name="broker-drain")

    async def stop_drain(self) -> None:
        self._stopping = True
        try:
            await asyncio.wait_for(self._queue.join(), timeout=30.0)
        except asyncio.TimeoutError:
            log.warning("AsyncBroker: drain timeout — %d orders dropped at shutdown", self._queue.qsize())
        if self._drain_task:
            self._drain_task.cancel()
            await asyncio.gather(self._drain_task, return_exceptions=True)

    async def _drain_loop(self) -> None:
        while True:
            try:
                priority, seq, request = await self._queue.get()
            except asyncio.CancelledError:
                return

            dedup_key = (request.t212_ticker, request.side, request.bar_close_iso)

            # Remove from mirror
            try:
                self._heap_mirror.remove((priority, seq, request))
            except ValueError:
                pass  # already evicted

            # If dedup key was evicted after enqueue, skip silently
            if dedup_key not in self._seen:
                self._queue.task_done()
                continue

            self._seen.discard(dedup_key)

            result = await self._process_one(request)

            if self._callback is not None:
                try:
                    await self._callback(result)
                except Exception as exc:
                    log.error("Post-fill callback failed for %s: %s", request.t212_ticker, exc)

            self._queue.task_done()

            if self._stopping and self._queue.empty():
                return

    async def _process_one(self, request: OrderRequest) -> OrderResult:
        """Stale check → submit market order → (BUY) submit stop + limit."""
        # Stale check
        interval_secs = _INTERVAL_SECONDS.get(request.interval, 86400)
        age = (datetime.utcnow() - request.signal_ts).total_seconds()
        if age > interval_secs * self._stale_multiplier:
            orders_stale_dropped_total.labels(
                interval=request.interval, ticker=request.t212_ticker
            ).inc()
            log.info(
                "Stale drop: %s %s age=%.1fs window=%.1fs",
                request.side, request.t212_ticker, age, interval_secs * self._stale_multiplier,
            )
            return OrderResult(request=request, status="stale_dropped")

        # Market order
        wait = await self._throttle.acquire("orders_market")
        order_throttle_wait_seconds.labels(endpoint="orders_market").observe(wait)
        submitted_at = datetime.utcnow()

        try:
            resp = await asyncio.to_thread(
                self._t212.place_market_order,
                instrument_ticker=request.t212_ticker,
                quantity=request.quantity if request.side == "BUY" else -request.quantity,
            )
        except Exception as exc:
            log.error("Market order failed for %s %s: %s", request.side, request.t212_ticker, exc)
            return OrderResult(request=request, status="failed", error=str(exc), submitted_at=submitted_at)

        fill_price = float(resp.get("fillPrice") or request.entry_price)
        t212_order_id = str(resp.get("id", ""))

        age_at_submit = (submitted_at - request.signal_ts).total_seconds()
        order_submission_age_seconds.labels(
            interval=request.interval, side=request.side
        ).observe(age_at_submit)

        result = OrderResult(
            request=request,
            status="filled",
            fill_price=fill_price,
            t212_order_id=t212_order_id,
            submitted_at=submitted_at,
        )

        # OCO orders for BUY
        if request.side == "BUY" and (request.stop_loss_pct or request.take_profit_pct):
            sl_price = fill_price * (1 - request.stop_loss_pct) if request.stop_loss_pct else None
            tp_price = fill_price * (1 + request.take_profit_pct) if request.take_profit_pct else None

            if sl_price is not None:
                wait = await self._throttle.acquire("orders_stop")
                order_throttle_wait_seconds.labels(endpoint="orders_stop").observe(wait)
                try:
                    stop_resp = await asyncio.to_thread(
                        self._t212.place_stop_order, request.t212_ticker, request.quantity, sl_price
                    )
                    result.stop_order_id = str(stop_resp["id"])
                except Exception as exc:
                    log.error("Stop order failed for %s: %s", request.t212_ticker, exc)

            if tp_price is not None:
                wait = await self._throttle.acquire("orders_limit")
                order_throttle_wait_seconds.labels(endpoint="orders_limit").observe(wait)
                try:
                    limit_resp = await asyncio.to_thread(
                        self._t212.place_limit_order, request.t212_ticker, request.quantity, tp_price
                    )
                    result.limit_order_id = str(limit_resp["id"])
                except Exception as exc:
                    log.error("Limit order failed for %s: %s", request.t212_ticker, exc)

        return result
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_async_broker.py -v
```
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add alphaTrade/broker/async_broker.py tests/unit/test_async_broker.py
git commit -m "feat(broker): add AsyncBroker with priority queue and drain task"
```

---

## Task 6: AsyncBroker — full drain integration test

**Files:**
- Modify: `tests/unit/test_async_broker.py`

- [ ] **Step 1: Add drain integration tests**

```python
# Add to tests/unit/test_async_broker.py

@pytest.mark.asyncio
async def test_sell_drains_before_buy():
    """SELL order must be processed before BUY even if BUY was enqueued first."""
    t212 = _t212()
    broker = AsyncBroker(t212=t212, throttle=_throttle())
    processed_order: list[str] = []

    async def cb(result: OrderResult) -> None:
        processed_order.append(result.request.side)

    broker.set_callback(cb)

    buy_req = _make_request(ticker="AAPL", side="BUY", interval="1m", bar_close_iso="2026-01-01T09:00:00Z")
    sell_req = _make_request(ticker="TSLA", side="SELL", interval="1m", bar_close_iso="2026-01-01T09:00:00Z")

    broker.enqueue(buy_req)
    broker.enqueue(sell_req)

    await broker.start_drain()
    await asyncio.sleep(0.2)
    await broker.stop_drain()

    assert processed_order[0] == "SELL"
    assert processed_order[1] == "BUY"


@pytest.mark.asyncio
async def test_filled_order_triggers_callback():
    t212 = _t212()
    broker = AsyncBroker(t212=t212, throttle=_throttle())
    results: list[OrderResult] = []

    async def cb(result: OrderResult) -> None:
        results.append(result)

    broker.set_callback(cb)
    broker.enqueue(_make_request())

    await broker.start_drain()
    await asyncio.sleep(0.2)
    await broker.stop_drain()

    assert len(results) == 1
    assert results[0].status == "filled"
    assert results[0].fill_price == 101.0


@pytest.mark.asyncio
async def test_buy_places_oco_orders():
    t212 = _t212()
    broker = AsyncBroker(t212=t212, throttle=_throttle())
    results: list[OrderResult] = []

    async def cb(result: OrderResult) -> None:
        results.append(result)

    broker.set_callback(cb)
    broker.enqueue(_make_request(side="BUY"))

    await broker.start_drain()
    await asyncio.sleep(0.2)
    await broker.stop_drain()

    t212.place_stop_order.assert_called_once()
    t212.place_limit_order.assert_called_once()
    assert results[0].stop_order_id == "stop-1"
    assert results[0].limit_order_id == "limit-1"
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
pytest tests/unit/test_async_broker.py -v
```
Expected: all PASS

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_async_broker.py
git commit -m "test(broker): add drain integration tests for AsyncBroker"
```

---

## Task 7: main.py — broker wiring and equity throttle

**Files:**
- Modify: `alphaTrade/main.py`

- [ ] **Step 1: Add imports**

At the top of `alphaTrade/main.py`, add alongside existing broker imports:

```python
from alphaTrade.broker.throttle import EndpointThrottle
from alphaTrade.broker.async_broker import AsyncBroker
from alphaTrade.broker.order_queue import OrderRequest, OrderResult
```

- [ ] **Step 2: Instantiate throttle and broker in `run()`**

In `run()`, after `t212_holder: list = [t212]` (around line 732), add:

```python
    throttle = EndpointThrottle.from_t212_config(settings.executors.trading212.throttle)
    broker = AsyncBroker(
        t212=t212,
        throttle=throttle,
        stale_window_multiplier=settings.risk.order_stale_window_multiplier,
        max_queue_depth=settings.risk.order_queue_max_depth,
    )
```

- [ ] **Step 3: Add `_on_order_fill` callback before `make_tick`**

Add this function inside `run()`, after the broker instantiation and before the `make_tick` calls. It closes over `engine`, `settings`, `alert_manager`, `_oco_tasks`, and `static_map`:

```python
    async def _on_order_fill(result: OrderResult) -> None:
        from sqlmodel import Session
        from alphaTrade.store.repos import (
            OrderRepo, PositionRepo, TradeJournalRepo, Position,
        )
        from alphaTrade.risk.performance import _effective_config, check_retirement, record_trade
        from alphaTrade.notify.alerting import AlertLevel
        from alphaTrade.broker.oco_monitor import monitor_oco
        from alphaTrade.config import ModelOverride
        from datetime import timedelta, datetime

        req = result.request

        if result.status not in ("filled",):
            if result.status == "stale_dropped":
                log.info("Order stale-dropped: %s %s", req.side, req.t212_ticker)
            elif result.status == "failed":
                log.error("Order failed: %s %s — %s", req.side, req.t212_ticker, result.error)
                if alert_manager is not None:
                    alert_manager.notify(
                        f"Order error for {req.t212_ticker}: {result.error}",
                        AlertLevel.ERROR,
                    )
            return

        fill_price = result.fill_price or req.entry_price

        with Session(engine) as session:
            order_repo = OrderRepo(session)
            pos_repo = PositionRepo(session)

            saved = order_repo.find_by_client_order_id(req.client_order_id)
            if saved:
                order_repo.update_fill(saved.id, "filled", fill_price, result.t212_order_id)

            cooldown_secs = _INTERVAL_SECONDS.get(req.interval, 86400) * settings.defaults.cooldown_bars
            cooldown_td = timedelta(seconds=cooldown_secs)

            if req.side == "BUY":
                pos_repo.upsert(Position(
                    t212_ticker=req.t212_ticker,
                    quantity=req.quantity,
                    avg_entry=fill_price,
                    last_signal_ts=datetime.utcnow(),
                ))
                from alphaTrade.metrics import open_positions as metric_open_positions
                metric_open_positions.set(len(pos_repo.all()))

                if result.stop_order_id and result.limit_order_id:
                    pos_repo.update_oco_ids(req.t212_ticker, result.stop_order_id, result.limit_order_id)

                sl_price = fill_price * (1 - req.stop_loss_pct)
                tp_price = fill_price * (1 + req.take_profit_pct)

                yaml_ov = settings.model_overrides.get(req.run_name, ModelOverride())
                per_model_ret = yaml_ov.retirement
                _ret_cfg = _effective_config(settings.risk.model_retirement, per_model_ret)

                if result.stop_order_id and result.limit_order_id:
                    _task = asyncio.create_task(monitor_oco(
                        t212=t212_holder[0],
                        t212_ticker=req.t212_ticker,
                        stop_order_id=result.stop_order_id,
                        limit_order_id=result.limit_order_id,
                        engine=engine,
                        cooldown_td=cooldown_td,
                        entry_price=fill_price,
                        sl_price=sl_price,
                        tp_price=tp_price,
                        quantity=req.quantity,
                        model_id=req.run_name,
                        entry_time=result.submitted_at or datetime.utcnow(),
                        retirement_cfg=_ret_cfg,
                    ))
                    _oco_tasks.add(_task)
                    _task.add_done_callback(_oco_tasks.discard)

                if alert_manager is not None:
                    alert_manager.notify(
                        f"Order filled: BUY {req.quantity:.2f}x {req.t212_ticker} @ {fill_price:.4f}",
                        AlertLevel.INFO,
                    )

            elif req.side == "SELL":
                from alphaTrade.store.repos import TradeJournalRepo
                pos = pos_repo.get(req.t212_ticker)
                pos_repo.remove(req.t212_ticker)
                pos_repo.upsert(Position(
                    t212_ticker=req.t212_ticker,
                    quantity=0,
                    avg_entry=0,
                    cooldown_until_ts=datetime.utcnow() + cooldown_td,
                ))
                from alphaTrade.metrics import open_positions as metric_open_positions
                metric_open_positions.set(len(pos_repo.all()))

                if pos:
                    journal_repo = TradeJournalRepo(session)
                    journal_repo.save(build_sell_journal_entry(
                        model_id=req.run_name,
                        t212_ticker=req.t212_ticker,
                        exit_price=fill_price,
                        quantity=req.quantity,
                        position=pos,
                    ))
                    yaml_ov = settings.model_overrides.get(req.run_name, ModelOverride())
                    per_model_ret = yaml_ov.retirement
                    _ret_cfg = _effective_config(settings.risk.model_retirement, per_model_ret)
                    pnl = (fill_price - pos.avg_entry) * req.quantity
                    try:
                        record_trade(session, model_id=req.run_name, realized_pnl=pnl, cfg=_ret_cfg)
                        if check_retirement(session, model_id=req.run_name, cfg=_ret_cfg):
                            msg = f"Model {req.run_name} auto-retired"
                            wh.notify("WARNING", msg, category="model-retirement")
                            if alert_manager is not None:
                                alert_manager.notify(msg, AlertLevel.WARNING)
                    except Exception as perf_exc:
                        log.warning("Retirement tracking failed: %s", perf_exc)

                if alert_manager is not None:
                    alert_manager.notify(
                        f"Order filled: SELL {req.quantity:.2f}x {req.t212_ticker} @ {fill_price:.4f}",
                        AlertLevel.INFO,
                    )

    broker.set_callback(_on_order_fill)
```

- [ ] **Step 4: Start drain task and throttle equity fetch**

In `run()`, after reconcile_positions (around line 775), add the broker start:

```python
    await broker.start_drain()
    log.info("AsyncBroker drain task started")
```

Find `equity = await asyncio.to_thread(t212.get_total_equity)` in the tick function (inside `make_tick`) and add throttle before it. In `make_tick`, add `throttle` as a parameter:

```python
def make_tick(
    interval: str,
    *,
    registry,
    settings: Settings,
    engine,
    t212_holder: list,
    provider_holder: list,
    health_state,
    oco_tasks: set,
    static_map: dict[str, str],
    alert_manager=None,
    throttle: EndpointThrottle | None = None,
    broker: AsyncBroker | None = None,
):
```

Inside the tick function, replace:
```python
                equity = await asyncio.to_thread(t212.get_total_equity)
```
with:
```python
                if throttle is not None:
                    await throttle.acquire("account_cash")
                equity = await asyncio.to_thread(t212.get_total_equity)
```

Update the `make_tick` calls in `run()` to pass throttle and broker:

```python
        asyncio.create_task(
            schedule_bar_close(
                interval,
                make_tick(
                    interval,
                    registry=registry,
                    settings=settings,
                    engine=engine,
                    t212_holder=t212_holder,
                    provider_holder=provider_holder,
                    health_state=health_state,
                    oco_tasks=_oco_tasks,
                    static_map=static_map,
                    alert_manager=alert_manager,
                    throttle=throttle,
                    broker=broker,
                ),
                stop_event,
                extended_hours=settings.defaults.extended_hours,
            )
        )
```

- [ ] **Step 5: Add broker stop to shutdown sequence**

In `run()`, after `await asyncio.gather(*tasks)` and before the OCO task cancellation:

```python
    await broker.stop_drain()
    log.info("AsyncBroker drain task stopped")
```

- [ ] **Step 6: Run existing tests to verify no regressions**

```bash
pytest tests/unit/test_tick_nonblocking.py tests/unit/test_apply_bot_settings.py tests/unit/test_risk_gates.py -v
```
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add alphaTrade/main.py
git commit -m "feat(main): wire AsyncBroker, throttle equity fetch, start/stop drain task"
```

---

## Task 8: main.py — tick loop refactor to enqueue

**Files:**
- Modify: `alphaTrade/main.py`

- [ ] **Step 1: Replace submit_order_async with broker.enqueue in the tick loop**

Inside `make_tick`'s `tick()` function, find the block that starts after `if not gate.approved:` and currently calls `submit_order_async`. Replace the entire `try:` block (from `cid = make_client_order_id(...)` to the final `except Exception as exc:` handler for order failures) with:

```python
                cid = make_client_order_id(manifest.run_name, t212_ticker, bar_close_iso, signal)

                # DB idempotency: skip if already recorded for this bar
                existing = order_repo.find_by_client_order_id(cid)
                if existing:
                    log.info("Duplicate order skipped (cid=%s)", cid)
                    orders_total.labels(side=signal, status="skipped_duplicate").inc()
                    continue

                # Record intent before enqueue so crash between enqueue and drain is detectable
                rec = Order(
                    t212_ticker=t212_ticker,
                    side=signal,
                    quantity=qty,
                    client_order_id=cid,
                )
                order_repo.save(rec)

                if broker is not None:
                    req = OrderRequest(
                        t212_ticker=t212_ticker,
                        side=signal,
                        quantity=qty,
                        client_order_id=cid,
                        interval=manifest.interval,
                        signal_ts=datetime.utcnow(),
                        yf_ticker=yf_ticker,
                        stop_loss_pct=eff_stop_loss_pct,
                        take_profit_pct=eff_take_profit_pct,
                        entry_price=current_price,
                        run_name=manifest.run_name,
                        bar_close_iso=bar_close_iso,
                    )
                    broker.enqueue(req)
                    log.info("Enqueued %s %s qty=%s", signal, t212_ticker, qty)
                    orders_total.labels(side=signal, status="enqueued").inc()
                    _sb.publish({"type": "order_enqueued", "ticker": t212_ticker,
                                 "side": signal, "qty": qty, "ts": bar_close_iso})
```

Remove the old inline `await submit_order_async(...)`, OCO placement, position upsert, alert calls, retirement tracking — all of that now lives in `_on_order_fill`.

- [ ] **Step 2: Run tests to catch breakage**

```bash
pytest tests/unit/ -v --tb=short 2>&1 | tail -30
```
Fix any failures before proceeding.

- [ ] **Step 3: Run integration tests**

```bash
pytest tests/integration/ -v --tb=short 2>&1 | tail -40
```
Expected: PASS (integration tests mock T212; verify no regressions)

- [ ] **Step 4: Commit**

```bash
git add alphaTrade/main.py
git commit -m "refactor(main): tick loop enqueues to AsyncBroker instead of direct submit"
```

---

## Task 9: Backtest OCO lag simulation

**Files:**
- Modify: `alphaTrade/backtest/engine.py`
- Create: `tests/unit/test_backtest_oco_lag.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_backtest_oco_lag.py
"""Tests for per-bar OCO lag simulation in backtest engine."""
from __future__ import annotations
import pytest
from alphaTrade.backtest.engine import _compute_protected_fraction, _check_sl_tp_with_lag


def test_protected_fraction_zero_lag():
    # No lag = full bar protection
    frac = _compute_protected_fraction(queue_position=0, stop_gap=2.0, limit_gap=2.0, bar_duration_secs=60)
    assert frac == pytest.approx(1.0)


def test_protected_fraction_partial_lag():
    # Position 1 = 4s lag in 60s bar = 56/60 ≈ 0.933 protected
    frac = _compute_protected_fraction(queue_position=1, stop_gap=2.0, limit_gap=2.0, bar_duration_secs=60)
    assert frac == pytest.approx(56 / 60, rel=0.01)


def test_protected_fraction_full_bar_lag():
    # Lag >= bar duration → zero protection
    frac = _compute_protected_fraction(queue_position=15, stop_gap=2.0, limit_gap=2.0, bar_duration_secs=60)
    assert frac == pytest.approx(0.0)


def test_check_sl_tp_with_lag_full_protection_same_as_normal():
    # protected_fraction=1.0 should behave identically to _check_sl_tp
    from alphaTrade.backtest.engine import _check_sl_tp, BacktestState
    from datetime import datetime
    state = BacktestState(
        side="BUY", entry_price=100.0, quantity=1.0,
        sl_price=95.0, tp_price=110.0, entry_bar=0,
        entry_time=datetime.utcnow(), model_id="m",
    )
    # SL hit: low=94 < sl=95
    normal = _check_sl_tp(state, high=105.0, low=94.0)
    with_lag = _check_sl_tp_with_lag(state, high=105.0, low=94.0, protected_fraction=1.0)
    assert normal == with_lag == ("SL", 95.0)


def test_check_sl_tp_with_lag_zero_protection_misses_sl():
    # protected_fraction=0.0: no range = no SL/TP trigger
    from alphaTrade.backtest.engine import BacktestState, _check_sl_tp_with_lag
    from datetime import datetime
    state = BacktestState(
        side="BUY", entry_price=100.0, quantity=1.0,
        sl_price=95.0, tp_price=110.0, entry_bar=0,
        entry_time=datetime.utcnow(), model_id="m",
    )
    result = _check_sl_tp_with_lag(state, high=115.0, low=80.0, protected_fraction=0.0)
    assert result is None


def test_check_sl_tp_with_lag_partial_protection():
    # Bar open=100, low=90 (drops 10), protected_fraction=0.5 → adjusted_low = 100 - 5 = 95
    # SL at 96 should NOT trigger (adjusted_low=95 < 96? no, 95 < 96 means it WOULD trigger)
    # Let me think: adjusted_low = open - (open - low)*fraction = 100 - (100-90)*0.5 = 100 - 5 = 95
    # SL at 94: 95 > 94 → not triggered ✓
    from alphaTrade.backtest.engine import BacktestState, _check_sl_tp_with_lag
    from datetime import datetime
    state = BacktestState(
        side="BUY", entry_price=100.0, quantity=1.0,
        sl_price=94.0, tp_price=115.0, entry_bar=0,
        entry_time=datetime.utcnow(), model_id="m",
    )
    # open=100, low=90, fraction=0.5 → adjusted_low=95 → SL@94 not hit
    result = _check_sl_tp_with_lag(state, high=105.0, low=90.0, protected_fraction=0.5, bar_open=100.0)
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_backtest_oco_lag.py -v
```
Expected: FAIL with `ImportError`

- [ ] **Step 3: Add `_compute_protected_fraction` and `_check_sl_tp_with_lag` to `alphaTrade/backtest/engine.py`**

Add after the existing `_check_sl_tp` function:

```python
def _compute_protected_fraction(
    queue_position: int,
    stop_gap: float,
    limit_gap: float,
    bar_duration_secs: float,
) -> float:
    """Fraction of bar range protected by OCO, accounting for queue position lag.

    Queue position K means (K * (stop_gap + limit_gap)) seconds pass before OCO is placed.
    During that window, price moves are unprotected. We model the protected portion
    as the fraction of bar duration remaining after the lag.
    """
    lag_secs = queue_position * (stop_gap + limit_gap)
    return max(0.0, 1.0 - lag_secs / bar_duration_secs)


def _check_sl_tp_with_lag(
    state: "BacktestState",
    high: float,
    low: float,
    protected_fraction: float,
    bar_open: float | None = None,
) -> tuple[str, float] | None:
    """Check SL/TP with OCO lag applied. protected_fraction=1.0 is identical to _check_sl_tp.

    When fraction < 1.0, scales the bar's high/low range around bar_open (or entry_price)
    to approximate only the portion of the move that occurred after OCO was placed.
    """
    if protected_fraction >= 1.0:
        return _check_sl_tp(state, high=high, low=low)

    if protected_fraction <= 0.0:
        return None

    # Scale range around open (or entry_price as fallback)
    origin = bar_open if bar_open is not None else state.entry_price
    adj_high = origin + (high - origin) * protected_fraction
    adj_low = origin - (origin - low) * protected_fraction
    return _check_sl_tp(state, high=adj_high, low=adj_low)
```

- [ ] **Step 4: Add per-bar lag computation to `run_backtest`**

In `alphaTrade/backtest/engine.py`, modify `run_backtest` to support `simulate_oco_lag`. Replace the call to `_run_single_model` with:

```python
        if cfg.simulate_oco_lag and len(models) > 1:
            trades, status = _run_single_model_with_lag(
                manifest=manifest,
                model=model,
                all_models=models,
                provider=provider,
                start=start,
                end=end,
                cfg=cfg,
            )
        else:
            trades, status = _run_single_model(
                manifest=manifest,
                model=model,
                provider=provider,
                start=start,
                end=end,
                cfg=cfg,
            )
```

Add `_run_single_model_with_lag` after `_run_single_model`:

```python
def _run_single_model_with_lag(
    manifest: "Manifest",
    model: "OnnxModel",
    all_models: list[tuple["Manifest", "OnnxModel"]],
    provider: "DataProvider",
    start: str,
    end: str,
    cfg: "BacktestConfig",
) -> tuple[list[dict], str]:
    """Same as _run_single_model but applies OCO lag based on how many same-interval
    models also signalled BUY on each bar.

    Lag per queue position = (orders_stop_min_gap + orders_limit_min_gap) = 4.0s default.
    """
    from alphaTrade.config import T212ThrottleConfig
    from alphaTrade.broker.order_queue import order_priority
    _throttle_defaults = T212ThrottleConfig()
    oco_gap = _throttle_defaults.orders_stop_min_gap_secs + _throttle_defaults.orders_limit_min_gap_secs

    same_interval = [
        (m, mo) for m, mo in all_models
        if m.interval == manifest.interval and m.run_name != manifest.run_name
    ]

    warmup_bars = manifest.window + 50
    df = provider.fetch_ohlcv_range(manifest.ticker, manifest.interval, start=start, end=end, extra_bars=warmup_bars)
    if df is None or len(df) < manifest.window + 2:
        return [], "no_data"

    # Pre-compute peer signals for every bar to determine queue positions
    peer_signals: dict[int, list[str]] = {}  # bar_index -> list of peer sides
    for peer_manifest, peer_model in same_interval:
        try:
            peer_df = provider.fetch_ohlcv_range(
                peer_manifest.ticker, peer_manifest.interval, start=start, end=end, extra_bars=warmup_bars
            )
            if peer_df is None or len(peer_df) < len(df):
                continue
            for i in range(warmup_bars, min(len(df), len(peer_df)) - 1):
                window_df = peer_df.iloc[max(0, i - warmup_bars):i + 1]
                sig = _infer(peer_manifest, peer_model, window_df)
                if sig == "BUY":
                    peer_signals.setdefault(i, []).append("BUY")
        except Exception as exc:
            log.warning("backtest lag: peer inference failed for %s: %s", peer_manifest.run_name, exc)

    _interval_secs = _INTERVAL_SECONDS.get(manifest.interval, 3600)
    trades: list[dict] = []
    state: BacktestState | None = None
    equity = cfg.initial_equity

    for i in range(warmup_bars, len(df) - 1):
        bar = df.iloc[i]
        next_bar = df.iloc[i + 1]

        # Compute signal once per bar — reused for both SL/TP check and BUY/SELL decision
        window_df = df.iloc[max(0, i - warmup_bars):i + 1]
        signal = _infer(manifest, model, window_df)

        if state is not None:
            peers_buying = len(peer_signals.get(i, []))
            # Queue position = number of same-interval peers that also buy this bar (same priority, FIFO)
            queue_pos = peers_buying
            protected = _compute_protected_fraction(
                queue_position=queue_pos,
                stop_gap=_throttle_defaults.orders_stop_min_gap_secs,
                limit_gap=_throttle_defaults.orders_limit_min_gap_secs,
                bar_duration_secs=_interval_secs,
            )
            hit = _check_sl_tp_with_lag(
                state,
                high=float(bar["High"]),
                low=float(bar["Low"]),
                protected_fraction=protected,
                bar_open=float(bar["Open"]),
            )
            if hit is not None:
                reason, exit_price = hit
                realized = state.pnl(exit_price) - cfg.commission_per_trade
                equity += realized
                trades.append(_build_trade(
                    state=state, exit_price=exit_price, exit_bar=i,
                    exit_time=bar.name, realized_pnl=realized, exit_reason=reason,
                    model_id=manifest.run_name,
                ))
                state = None

        if signal in ("BUY", "SELL") and state is None:
            fill_price = _simulate_fill(signal, float(next_bar["Open"]), cfg.slippage_bps)
            size_pct = cfg.default_size_pct
            quantity = (equity * size_pct) / fill_price
            sl_price = fill_price * (1 - cfg.sl_pct / 100) if cfg.sl_pct is not None else None
            tp_price = fill_price * (1 + cfg.tp_pct / 100) if cfg.tp_pct is not None else None
            state = BacktestState(
                side=signal,
                entry_price=fill_price,
                quantity=quantity,
                sl_price=sl_price,
                tp_price=tp_price,
                entry_bar=i + 1,
                entry_time=next_bar.name,
                model_id=manifest.run_name,
            )
        elif signal != "HOLD" and state is not None and signal != state.side:
            fill_price = _simulate_fill(signal, float(next_bar["Open"]), cfg.slippage_bps)
            realized = state.pnl(fill_price) - cfg.commission_per_trade
            equity += realized
            trades.append(_build_trade(
                state=state, exit_price=fill_price, exit_bar=i + 1,
                exit_time=next_bar.name, realized_pnl=realized, exit_reason="SIGNAL",
                model_id=manifest.run_name,
            ))
            state = None

    if state is not None:
        last_bar = df.iloc[-1]
        exit_price = float(last_bar["Close"])
        realized = state.pnl(exit_price) - cfg.commission_per_trade
        equity += realized
        trades.append(_build_trade(
            state=state, exit_price=exit_price, exit_bar=len(df) - 1,
            exit_time=last_bar.name, realized_pnl=realized, exit_reason="END_OF_DATA",
            model_id=manifest.run_name,
        ))

    return trades, "ran"
```

Also add `_INTERVAL_SECONDS` reference at the top of `engine.py` (it's currently in `main.py`):

```python
_INTERVAL_SECONDS: dict[str, int] = {
    "1m": 60, "5m": 300, "15m": 900, "1h": 3600,
    "4h": 14400, "1d": 86400, "1wk": 604800,
}
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/unit/test_backtest_oco_lag.py -v
```
Expected: all PASS

- [ ] **Step 6: Run existing backtest tests to verify no regression**

```bash
pytest tests/unit/test_backtest_engine.py tests/unit/test_backtest_engine_filter.py -v
```
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add alphaTrade/backtest/engine.py tests/unit/test_backtest_oco_lag.py
git commit -m "feat(backtest): add per-bar OCO lag simulation via simulate_oco_lag config"
```

---

## Task 10: Full test suite + spec note on passive eviction

**Files:**
- Test only

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/ -v --tb=short 2>&1 | tail -50
```
Expected: all PASS. Fix any regressions before marking complete.

- [ ] **Step 2: Run integration tests**

```bash
pytest tests/integration/ -v --tb=short
```
Expected: all PASS

- [ ] **Step 3: Commit clean test run marker**

```bash
git commit --allow-empty -m "test: full suite green after broker rate limiting feature"
```

---

## Known Limitations (from spec)

- **Passive stale eviction only:** Stale orders are dropped at dequeue time, not actively removed from the queue. If drain throughput is perpetually saturated, migrate the queue to Redis Streams (supports `ZRANGEBYSCORE`/`ZREM` for active eviction). This is a Tier 1 scaling concern, not a current issue.
- **T212Client stays sync:** All T212 HTTP calls run via `asyncio.to_thread`. Making T212Client async-native (`httpx.AsyncClient`) is deferred.
- **Backtest lag assumes same-exchange tickers per interval:** `_run_single_model_with_lag` fetches peer data with `fetch_ohlcv_range`; if bars don't align exactly across tickers (e.g., different exchanges), queue position estimate may be off by 1.
