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
