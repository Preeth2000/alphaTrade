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

### Updating Rate Limits

Limits are defaults on `T212ThrottleConfig` (a `BaseSettings` dataclass in `config.py`). Override any value in `settings.yaml` under `executors.trading212.throttle`, then restart. No code change required.

```yaml
# settings.yaml — only specify values that differ from defaults
executors:
  trading212:
    throttle:
      orders_stop_min_gap_secs: 1.5   # if T212 relaxes this to 40/min
```

Adding a new executor: define `<Executor>ThrottleConfig` dataclass in `config.py` and add `executors.<name>` section. `EndpointThrottle` is instantiated from whichever executor config is active.

## Approach: AsyncBroker Wrapper Layer + Cross-Tick Queue

New `AsyncBroker` class sits between `main.py` and `T212Client`. Ticks enqueue `OrderRequest` objects into a single shared `asyncio.PriorityQueue`. A background drain task pulls from the queue, applies rate limiting, and submits orders. All protections are co-located in the broker layer.

**Why cross-tick queue is required:** At 1 req/2s for stop/limit orders, 15+ models at 1m interval produce OCO setup time exceeding the bar duration. Without a shared queue, multiple tick coroutines fight over the throttle lock with no global priority. A persistent queue ensures SELL orders from any tick always preempt BUY orders from any other tick, and the drain task is the sole writer to T212.

## Components

### `alphaTrade/config.py` additions — `T212ThrottleConfig`

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

# Added to Settings:
executors: ExecutorsConfig = ExecutorsConfig()
```

To update any limit: edit `settings.yaml` under `executors.trading212.throttle.<key>`, restart.

### `alphaTrade/broker/throttle.py` — `EndpointThrottle`

Per-endpoint async rate limiter using last-call timestamp + `asyncio.sleep`. Constructed from `T212ThrottleConfig`.

```python
class EndpointThrottle:
    _min_gap: dict[str, float]   # endpoint key -> minimum seconds between calls
    _last_call: dict[str, float] # endpoint key -> monotonic time of last call
    _lock: asyncio.Lock

    @classmethod
    def from_t212_config(cls, cfg: T212ThrottleConfig) -> "EndpointThrottle": ...

    async def acquire(self, endpoint: str) -> None:
        """Block until a call to endpoint is within rate limit."""
```

`endpoint` keys match `T212ThrottleConfig` field names (e.g., `"orders_stop"`, `"account_cash"`). Acquired before every T212 HTTP call in `AsyncBroker`. T212Client stays sync and unchanged.

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

    def enqueue(self, request: OrderRequest) -> None:
        """Add order to the persistent priority queue. Called by tick coroutines."""

    async def start_drain(self) -> None:
        """Start background drain task. Called once at bot startup."""

    async def stop_drain(self) -> None:
        """Graceful shutdown: drain remaining queue then exit."""
```

**Queue structure:** `asyncio.PriorityQueue` of `(priority, sequence_num, OrderRequest)`. `sequence_num` is a monotonic counter ensuring FIFO within equal priority (avoids `dataclass` comparison on `OrderRequest`).

**Drain task — continuous loop:**
1. `request = await queue.get()`
2. **Stale check**: `now - signal_ts > interval_secs * stale_window_multiplier` → drop + metric
3. **Dedup check**: `(t212_ticker, side)` in `seen: set` (persists across ticks, TTL = interval_secs) → skip + metric
4. `throttle.acquire("orders_market")`
5. `place_market_order()` via `asyncio.to_thread`
6. Record `order_submission_age_seconds` metric
7. On BUY success: `throttle.acquire("orders_stop")` → `place_stop_order()` → `throttle.acquire("orders_limit")` → `place_limit_order()`
8. If stop OR limit fails: log + alert, continue (partial OCO better than none)
9. `queue.task_done()`

**Queue depth enforcement:** `enqueue()` checks `queue.qsize() >= max_queue_depth`. If full: find lowest-priority item already in queue, drop it if new request has higher priority, else drop new request. Log + metric either way.

**Dedup TTL:** `seen` entries expire after `interval_secs` so a ticker can re-enter after its bar has closed.

### `alphaTrade/main.py` changes

**Tick loop refactor:**
- Remove direct `submit_order_async` call
- Build `OrderRequest` for each gate-approved signal
- Call `broker.enqueue(request)` for each — tick returns immediately, drain task handles submission
- Post-fill result handling (position upsert, OCO monitor, alerts) moves into the drain task's result callback

**Equity fetch throttling:**
```python
await throttle.acquire("account_cash")
equity = await asyncio.to_thread(t212.get_total_equity)
```

**Bot startup:** `await broker.start_drain()` after T212Client init. `await broker.stop_drain()` on SIGTERM/SIGINT before shutdown.

**`AsyncBroker` instantiation:** Created once at bot startup alongside `T212Client`, stored in holder list for hot-reload compatibility. Hot-reload updates `T212Client` reference inside broker.

## Protections Summary

| # | Protection | Location | Config key |
|---|-----------|----------|------------|
| 1 | Per-endpoint rate limiting | EndpointThrottle | `executors.trading212.throttle.*` |
| 2 | Cross-tick priority queue (SELL > short BUY > long BUY) | AsyncBroker drain task | n/a |
| 3 | Stale signal guard | drain task | `risk.order_stale_window_multiplier` (default 0.5) |
| 4 | Queue depth cap with priority eviction | `enqueue()` | `risk.order_queue_max_depth` (default 50) |
| 5 | Cross-interval dedup with TTL | drain task | n/a |
| 6 | Submission age metrics | drain task + metrics.py | n/a |
| 7 | Backtest OCO lag simulation | backtest engine | `backtest.simulate_oco_lag` (default false) |

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

## Backtest OCO Lag Simulation

Backtest currently checks SL/TP at bar level, assuming OCO placed instantly at bar open. In live trading, OCO placement is delayed by the throttle queue. For short intervals (1m, 5m) with many models, the backtest overstates performance by assuming instant stop/limit protection.

### Per-Bar Computed Lag (Option 3)

Since backtest runs all models together, it knows exactly how many BUY signals fire on each bar. Use that to compute each ticker's actual queue position and apply a precise lag before checking SL/TP.

**Algorithm per bar:**

```
1. Run inference for all models → collect all (ticker, side, signal) for this bar
2. Sort by order_priority(side, interval)  ← same sort as live drain task
3. For each BUY at queue position K (0-indexed):
     oco_lag_secs = K * (orders_stop_min_gap_secs + orders_limit_min_gap_secs)
                  = K * (2.0 + 2.0) = K * 4.0s
4. When checking SL/TP for this ticker on this bar:
     effective_bar_open = bar_open + timedelta(seconds=oco_lag_secs)
     only check SL/TP for sub-bar timestamps >= effective_bar_open
     for OHLC-only data (no sub-bar): scale high/low range proportionally
         protected_fraction = max(0, 1 - oco_lag_secs / bar_duration_secs)
         adjusted_high = open + (high - open) * protected_fraction
         adjusted_low  = open - (open - low)  * protected_fraction
```

For OHLC-only bars, the proportional scaling is an approximation assuming price moves linearly through the bar. This is conservative (slightly underestimates protection) but correct on average.

**SELL signals:** Queue position 0, lag = 0. SELLs always get priority and are assumed to submit instantly.

**Config:**
```yaml
backtest:
  simulate_oco_lag: true   # default false for backwards compatibility
```

When `simulate_oco_lag: false` (default): existing behaviour, no lag applied.

### Files Changed for Backtest

| File | Action |
|------|--------|
| `alphaTrade/backtest/engine.py` | MODIFY — per-bar lag computation in `_run_single_model`, `_check_sl_tp` accepts `protected_fraction` |
| `alphaTrade/config.py` | MODIFY — add `BacktestConfig.simulate_oco_lag: bool = False` |

## Files Changed / Created

| File | Action |
|------|--------|
| `alphaTrade/broker/throttle.py` | CREATE |
| `alphaTrade/broker/order_queue.py` | CREATE |
| `alphaTrade/broker/async_broker.py` | CREATE |
| `alphaTrade/broker/orders.py` | KEEP — `submit_order_async` stays for tests |
| `alphaTrade/main.py` | MODIFY — tick loop enqueues, startup/shutdown wires drain task |
| `alphaTrade/metrics.py` | MODIFY — add 4 new metrics |
| `alphaTrade/config.py` | MODIFY — add `T212ThrottleConfig`, `T212ExecutorConfig`, `ExecutorsConfig`; add 2 risk fields; add `BacktestConfig.simulate_oco_lag` |
| `alphaTrade/backtest/engine.py` | MODIFY — per-bar OCO lag computation |

## Out of Scope

- Modifying T212Client internals (stays sync, throttle lives outside it)
