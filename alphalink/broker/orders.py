"""Build and submit market orders.

Note: Trading212 market order API does not support broker-side SL/TP parameters.
SL/TP enforcement is handled by the risk gate (drawdown halt, cooldown).
"""
from __future__ import annotations

import asyncio
import hashlib

from alphaTrade.broker.t212_client import T212Client
from alphaTrade.store.repos import Order, OrderRepo


def make_client_order_id(run_name: str, ticker: str, bar_close_iso: str, side: str) -> str:
    """Deterministic 16-char hex id = sha256(run_name|ticker|bar_close_iso|side)[:16]."""
    raw = f"{run_name}|{ticker}|{bar_close_iso}|{side}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def submit_order(
    t212: T212Client,
    instrument_ticker: str,
    side: str,       # BUY | SELL
    quantity: float,
    order_repo: OrderRepo | None = None,
    client_order_id: str = "",
) -> dict:
    """Submit MARKET order. Idempotent when order_repo + client_order_id provided."""
    if order_repo and client_order_id:
        existing = order_repo.find_by_client_order_id(client_order_id)
        if existing:
            return {"skipped_duplicate": True, "existing_order_id": existing.id}

        rec = Order(
            t212_ticker=instrument_ticker,
            side=side,
            quantity=quantity,
            client_order_id=client_order_id,
        )
        order_repo.save(rec)

    signed_qty = quantity if side == "BUY" else -quantity
    return t212.place_market_order(
        instrument_ticker=instrument_ticker,
        quantity=signed_qty,
    )


async def submit_order_async(
    t212: T212Client,
    instrument_ticker: str,
    side: str,       # BUY | SELL
    quantity: float,
    order_repo: OrderRepo | None = None,
    client_order_id: str = "",
) -> dict:
    """Async version: DB idempotency check on event loop, HTTP call off-thread."""
    if order_repo and client_order_id:
        existing = order_repo.find_by_client_order_id(client_order_id)
        if existing:
            return {"skipped_duplicate": True, "existing_order_id": existing.id}

        rec = Order(
            t212_ticker=instrument_ticker,
            side=side,
            quantity=quantity,
            client_order_id=client_order_id,
        )
        order_repo.save(rec)

    signed_qty = quantity if side == "BUY" else -quantity
    return await asyncio.to_thread(
        t212.place_market_order,
        instrument_ticker,
        signed_qty,
    )
