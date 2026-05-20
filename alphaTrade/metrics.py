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
