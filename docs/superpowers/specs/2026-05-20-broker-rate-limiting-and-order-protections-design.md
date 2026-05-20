# Broker Rate Limiting & Order Protections Design

**Date:** 2026-05-20  
**Status:** Approved

## Problem

Trading 212 enforces per-endpoint rate limits. The current `submit_order_async` calls T212 directly with no throttling. With multiple models firing simultaneously:
- `POST /equity/orders/stop` + `POST /equity/orders/limit`: 1 req/2s each — every BUY requires both, serial
- `GET /equity/account/cash`: 1 req/5s — called every tick
- `POST /equity/orders/market`: 50/min — not a bottleneck but should be tracked

Five concurrent BUYs = 10 stop + 10 limit calls = ~40s at max allowed rate. Without throttling, 429s occur, OCO orders fail silently, and risk management degrades.

Secondary problems: no signal freshness check, no within-tick dedup, no priority (SELL vs BUY), no submission latency visibility.

## T212 Rate Limits (per-endpoint, per-account)

| Endpoint | Limit | Min gap |
|----------|-------|---------|
| POST /equity/orders/market | 50/min | 1.2s |
| POST /equity/orders/stop | 1 req/2s | 2.0s |
| POST /equity/orders/limit | 1 req/2s | 2.0s |
| DELETE /equity/orders/{id} | 50/min | 1.2s |
| GET /equity/account/cash | 1 req/5s | 5.0s |
| GET /equity/portfolio | 1 req/1s | 1.0s |
| GET /equity/orders/{id} | 1 req/1s | 1.0s |

## Approach: AsyncBroker Wrapper Layer

New `AsyncBroker` class sits between `main.py` and `T212Client`. The tick loop collects `OrderRequest` objects, then calls `async_broker.execute_tick_orders()` once. All protections are co-located in the broker layer.

## Components

### `alphaTrade/broker/throttle.py` — `EndpointThrottle`

Per-endpoint async rate limiter using last-call timestamp + `asyncio.sleep`.

```python
class EndpointThrottle:
    _min_gap: dict[str, float]   # endpoint -> minimum seconds between calls
    _last_call: dict[str, float] # endpoint -> monotonic time of last call
    _lock: asyncio.Lock

    async def acquire(self, endpoint: str) -> None:
        """Block until a call to endpoint is within rate limit."""
```

Default gaps:
- `/equity/orders/stop`: 2.0s
- `/equity/orders/limit`: 2.0s
- `/equity/orders/market`: 1.2s
- `/equity/account/cash`: 5.0s
- `/equity/portfolio`: 1.0s
- `/equity/orders/id`: 1.0s

Acquired before every T212 HTTP call. Applied at the AsyncBroker level, not inside T212Client (T212Client stays sync).

### `alphaTrade/broker/order_queue.py` — `OrderRequest` + priority

```python
@dataclass
class OrderRequest:
    t212_ticker: str
    side: str           # BUY | SELL
    quantity: float
    client_order_id: str
    interval: str
    signal_ts: datetime
    yf_ticker: str
    stop_loss_pct: float
    take_profit_pct: float
    entry_price: float
    run_name: str

@dataclass
class OrderResult:
    request: OrderRequest
    status: str         # filled | stale_dropped | deduped | failed
    fill_price: float | None
    t212_order_id: str
    stop_order_id: str
    limit_order_id: str
    submitted_at: datetime | None
    error: str

_INTERVAL_ORDER = ["1m", "5m", "15m", "1h", "4h", "1d", "1wk"]

def order_priority(side: str, interval: str) -> int:
    """Lower number = higher priority.
    SELL=0 (always first). BUY priority by interval urgency (shorter = more urgent)."""
    if side == "SELL":
        return 0
    idx = _INTERVAL_ORDER.index(interval) if interval in _INTERVAL_ORDER else 3
    return 1 + idx  # BUY/1m=1, BUY/5m=2, ... BUY/1wk=7
```

### `alphaTrade/broker/async_broker.py` — `AsyncBroker`

```python
class AsyncBroker:
    def __init__(
        self,
        t212: T212Client,
        throttle: EndpointThrottle,
        order_repo: OrderRepo,
        max_queue_depth: int = 50,
        stale_window_multiplier: float = 0.5,
    ): ...

    async def execute_tick_orders(
        self,
        requests: list[OrderRequest],
        bar_close_iso: str,
    ) -> list[OrderResult]: ...
```

Execution sequence per tick:
1. Sort requests by `order_priority(side, interval)`
2. If `len > max_queue_depth`: keep top N by priority, log dropped count + metric
3. For each request in sorted order:
   a. **Stale check**: `now - signal_ts > interval_secs * stale_window_multiplier` → `status=stale_dropped`, metric inc
   b. **Dedup check**: `(t212_ticker, side)` in `seen_this_tick` → `status=deduped`, metric inc
   c. `throttle.acquire("/equity/orders/market")`
   d. `place_market_order()` via `asyncio.to_thread`
   e. Record `order_submission_age_seconds` histogram metric
   f. On BUY success: `throttle.acquire("/equity/orders/stop")` → `place_stop_order()` → `throttle.acquire("/equity/orders/limit")` → `place_limit_order()`
   g. If stop OR limit fails: log + alert, record partial failure in result (do not abort the other)
4. Return `list[OrderResult]`

### `alphaTrade/main.py` changes

**Tick loop refactor:**
- Remove direct `submit_order_async` call
- Build `OrderRequest` for each gate-approved signal
- After signal loop, call `broker.execute_tick_orders(requests, bar_close_iso)`
- Process results: upsert positions, update order fill in DB, start OCO monitor tasks, send alerts

**Equity fetch throttling:**
```python
await throttle.acquire("/equity/account/cash")
equity = await asyncio.to_thread(t212.get_total_equity)
```

**`AsyncBroker` instantiation:** Created once at bot startup alongside `T212Client`, stored in holder list for hot-reload compatibility.

## Six Protections Summary

| # | Protection | Location | Config key |
|---|-----------|----------|------------|
| 1 | Per-endpoint rate limiting | EndpointThrottle | hardcoded limits per T212 docs |
| 2 | Priority ordering (SELL > short-interval BUY > long-interval BUY) | AsyncBroker.execute_tick_orders | n/a |
| 3 | Stale signal guard | AsyncBroker.execute_tick_orders | `risk.order_stale_window_multiplier` (default 0.5) |
| 4 | Queue depth cap | AsyncBroker.execute_tick_orders | `risk.order_queue_max_depth` (default 50) |
| 5 | Within-tick dedup | AsyncBroker.execute_tick_orders | n/a |
| 6 | Submission age metrics | AsyncBroker + metrics.py | n/a |

## New Prometheus Metrics

| Metric | Type | Labels |
|--------|------|--------|
| `order_submission_age_seconds` | Histogram | `interval`, `side` |
| `orders_stale_dropped_total` | Counter | `interval`, `ticker` |
| `orders_deduped_total` | Counter | `ticker`, `side` |
| `order_throttle_wait_seconds` | Histogram | `endpoint` |

## Config Additions (Settings.risk)

```yaml
risk:
  order_stale_window_multiplier: 0.5   # drop if age > interval_secs * this
  order_queue_max_depth: 50            # max orders per tick before priority-dropping
```

## Files Changed / Created

| File | Action |
|------|--------|
| `alphaTrade/broker/throttle.py` | CREATE |
| `alphaTrade/broker/order_queue.py` | CREATE |
| `alphaTrade/broker/async_broker.py` | CREATE |
| `alphaTrade/broker/orders.py` | KEEP (used by backtest/tests) — `submit_order_async` stays |
| `alphaTrade/main.py` | MODIFY — tick loop, equity fetch, broker instantiation |
| `alphaTrade/metrics.py` | MODIFY — add 4 new metrics |
| `alphaTrade/config.py` | MODIFY — add 2 new risk fields |

## Out of Scope

- Cross-tick persistent queue (signals from one tick don't carry into the next)
- Modifying T212Client internals (stays sync, throttle lives outside it)
- Backtest changes (backtest uses `submit_order_async` directly, unchanged)
