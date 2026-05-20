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
