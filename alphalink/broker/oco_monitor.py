"""OCO monitor: polls stop/limit legs after BUY fill, cancels surviving leg on exit."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy.engine import Engine
from sqlmodel import Session

from alphalink.broker.t212_client import T212Client
from alphalink.notify import webhook as wh
from alphalink.store.repos import Position, PositionRepo

log = logging.getLogger(__name__)

_TERMINAL = frozenset({"FILLED", "CANCELLED", "REJECTED"})


async def monitor_oco(
    t212: T212Client,
    t212_ticker: str,
    stop_order_id: str,
    limit_order_id: str,
    engine: Engine,
    cooldown_td: timedelta,
    poll_interval_s: float = 10.0,
) -> None:
    """Poll stop and limit orders until one fills. Cancel the other leg and close position."""
    while True:
        await asyncio.sleep(poll_interval_s)
        try:
            stop_status = t212.get_order(stop_order_id).get("status", "")
            limit_status = t212.get_order(limit_order_id).get("status", "")
        except Exception as exc:
            log.error("OCO poll error for %s: %s", t212_ticker, exc)
            continue

        if stop_status == "FILLED":
            _close_position(engine, t212_ticker, cooldown_td, "stop_loss")
            _cancel_leg(t212, limit_order_id, t212_ticker)
            break

        if limit_status == "FILLED":
            _close_position(engine, t212_ticker, cooldown_td, "take_profit")
            _cancel_leg(t212, stop_order_id, t212_ticker)
            break

        if stop_status in _TERMINAL and stop_status != "FILLED":
            log.warning("Stop leg %s for %s reached %s without fill — cancelling limit leg", stop_order_id, t212_ticker, stop_status)
            _cancel_leg(t212, limit_order_id, t212_ticker)
            wh.notify("WARNING", f"OCO stop leg cancelled/rejected for {t212_ticker} — limit leg cancelled", category="oco")
            break

        if limit_status in _TERMINAL and limit_status != "FILLED":
            log.warning("Limit leg %s for %s reached %s without fill — cancelling stop leg", limit_order_id, t212_ticker, limit_status)
            _cancel_leg(t212, stop_order_id, t212_ticker)
            wh.notify("WARNING", f"OCO limit leg cancelled/rejected for {t212_ticker} — stop leg cancelled", category="oco")
            break


def _cancel_leg(t212: T212Client, order_id: str, t212_ticker: str) -> None:
    try:
        t212.cancel_order(order_id)
    except Exception as exc:
        log.warning("Failed to cancel OCO leg %s for %s: %s", order_id, t212_ticker, exc)


def _close_position(engine: Engine, t212_ticker: str, cooldown_td: timedelta, exit_reason: str) -> None:
    with Session(engine) as session:
        repo = PositionRepo(session)
        repo.remove(t212_ticker)
        repo.upsert(Position(
            t212_ticker=t212_ticker,
            quantity=0,
            avg_entry=0,
            cooldown_until_ts=datetime.utcnow() + cooldown_td,
        ))
    log.info("OCO %s hit for %s — position closed", exit_reason, t212_ticker)
    wh.notify("INFO", f"OCO {exit_reason} hit for {t212_ticker}", category="oco")
