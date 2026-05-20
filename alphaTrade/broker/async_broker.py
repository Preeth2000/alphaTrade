"""AsyncBroker: priority queue, rate-limited drain task, order execution."""
from __future__ import annotations

import asyncio
import heapq
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


class _EvictablePriorityQueue:
    """asyncio.PriorityQueue wrapper that supports logical eviction.

    Evicted items remain in the underlying heap but are skipped on get().
    qsize() returns only active (non-evicted) item count.
    """

    def __init__(self) -> None:
        self._heap: list[tuple[int, int, OrderRequest]] = []
        self._evicted: set[int] = set()  # seq numbers of evicted items
        self._active: int = 0
        self._event: asyncio.Event = asyncio.Event()

    def put_nowait(self, item: tuple[int, int, OrderRequest]) -> None:
        priority, seq, req = item
        heapq.heappush(self._heap, item)
        self._active += 1
        self._event.set()

    def evict_by_seq(self, seq: int) -> None:
        """Mark item with given seq as evicted. Drain will skip it."""
        self._evicted.add(seq)
        self._active -= 1
        if self._active == 0:
            self._event.clear()

    async def get(self) -> tuple[int, int, OrderRequest]:
        while True:
            await self._event.wait()
            while self._heap:
                item = heapq.heappop(self._heap)
                priority, seq, req = item
                if seq in self._evicted:
                    self._evicted.discard(seq)
                    continue  # skip evicted — already counted out via evict_by_seq
                # valid active item — caller must call task_done()
                if not self._heap:
                    self._event.clear()
                return item
            self._event.clear()

    def task_done(self) -> None:
        self._active -= 1
        if self._active < 0:
            self._active = 0

    def qsize(self) -> int:
        return self._active

    def empty(self) -> bool:
        return self._active == 0

    async def join(self) -> None:
        """Wait until all active items are task_done."""
        while self._active > 0:
            await asyncio.sleep(0.01)


class AsyncBroker:
    """Single drain task processes all orders across ticks in priority order."""

    def __init__(
        self,
        t212: T212Client,
        throttle: EndpointThrottle,
        stale_window_multiplier: float = 0.5,
        max_queue_depth: int = 50,
        t212_holder: list | None = None,
    ) -> None:
        self._t212 = t212
        self._t212_holder = t212_holder  # if set, always use holder[0] for hot-reload support
        self._throttle = throttle
        self._stale_multiplier = stale_window_multiplier
        self._max_depth = max_queue_depth
        # (priority, seq, request) — seq breaks ties without comparing dataclasses
        self._queue: _EvictablePriorityQueue = _EvictablePriorityQueue()
        self._seq = itertools.count()
        # dedup: (t212_ticker, side, bar_close_iso) — no TTL needed
        self._seen: set[tuple[str, str, str]] = set()
        # heap-mirror for priority eviction: (priority, seq, request)
        self._heap_mirror: list[tuple[int, int, OrderRequest]] = []
        self._drain_task: asyncio.Task | None = None
        self._stopping: bool = False
        self._callback: PostFillCallback | None = None

    @property
    def _live_t212(self) -> T212Client:
        """Always returns the current T212 client (supports hot-reload via t212_holder)."""
        return self._t212_holder[0] if self._t212_holder else self._t212

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
            # Remove worst from mirror and seen; evict from queue
            self._heap_mirror.remove((worst_priority, worst_seq, worst_req))
            worst_key = (worst_req.t212_ticker, worst_req.side, worst_req.bar_close_iso)
            self._seen.discard(worst_key)
            self._queue.evict_by_seq(worst_seq)
            log.info(
                "Queue full: evicted %s %s to make room for %s %s",
                worst_req.side, worst_req.t212_ticker, request.side, request.t212_ticker,
            )

        self._seen.add(dedup_key)
        self._heap_mirror.append((priority, seq, request))
        self._queue.put_nowait((priority, seq, request))

    async def start_drain(self) -> None:
        if self._drain_task is not None and not self._drain_task.done():
            raise RuntimeError("AsyncBroker drain already running")
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

            # Remove from mirror (may already be gone if evicted)
            try:
                self._heap_mirror.remove((priority, seq, request))
            except ValueError:
                pass

            # If evicted, skip silently
            if dedup_key not in self._seen:
                self._queue.task_done()
                continue

            try:
                result = await self._process_one(request)
                self._seen.discard(dedup_key)  # discard AFTER processing

                if self._callback is not None:
                    try:
                        await self._callback(result)
                    except Exception as exc:
                        log.error("Post-fill callback failed for %s: %s", request.t212_ticker, exc)
            except asyncio.CancelledError:
                self._seen.discard(dedup_key)
                self._queue.task_done()
                raise
            except Exception as exc:
                log.error(
                    "Unhandled exception in drain loop for %s %s: %s",
                    request.t212_ticker, request.side, exc,
                )
                self._seen.discard(dedup_key)
            finally:
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
                self._live_t212.place_market_order,
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
                        self._live_t212.place_stop_order, request.t212_ticker, request.quantity, sl_price
                    )
                    result.stop_order_id = str(stop_resp["id"])
                except Exception as exc:
                    log.error("Stop order failed for %s: %s", request.t212_ticker, exc)

            if tp_price is not None:
                wait = await self._throttle.acquire("orders_limit")
                order_throttle_wait_seconds.labels(endpoint="orders_limit").observe(wait)
                try:
                    limit_resp = await asyncio.to_thread(
                        self._live_t212.place_limit_order, request.t212_ticker, request.quantity, tp_price
                    )
                    result.limit_order_id = str(limit_resp["id"])
                except Exception as exc:
                    log.error("Limit order failed for %s: %s", request.t212_ticker, exc)

        return result
