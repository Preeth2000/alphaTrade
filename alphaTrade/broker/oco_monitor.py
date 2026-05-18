"""OCO monitor: polls stop/limit legs after BUY fill, cancels surviving leg on exit."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.engine import Engine
from sqlmodel import Session

from alphaTrade.broker.t212_client import T212Client
from alphaTrade.config import ModelRetirementConfig
from alphaTrade.notify import webhook as wh
from alphaTrade.store.repos import Position, PositionRepo, TradeJournal, TradeJournalRepo

log = logging.getLogger(__name__)

_TERMINAL = frozenset({"FILLED", "CANCELLED", "REJECTED"})


async def monitor_oco(
    t212: T212Client,
    t212_ticker: str,
    stop_order_id: str,
    limit_order_id: str,
    engine: Engine,
    cooldown_td: timedelta,
    entry_price: float = 0.0,
    sl_price: float = 0.0,
    tp_price: float = 0.0,
    quantity: float = 0.0,
    model_id: str = "",
    entry_time: Optional[datetime] = None,
    poll_interval_s: float = 10.0,
    retirement_cfg: Optional[ModelRetirementConfig] = None,
) -> None:
    """Poll stop and limit orders until one fills. Cancel the other leg and close position."""
    while True:
        await asyncio.sleep(poll_interval_s)
        try:
            stop_order = t212.get_order(stop_order_id)
            limit_order = t212.get_order(limit_order_id)
            stop_status = stop_order.get("status", "")
            limit_status = limit_order.get("status", "")
        except Exception as exc:
            log.error("OCO poll error for %s: %s", t212_ticker, exc)
            continue

        if stop_status == "FILLED":
            fill_price = float(stop_order.get("fillPrice") or sl_price)
            _close_position(
                engine, t212_ticker, cooldown_td, "OCO_SL",
                exit_price=fill_price, entry_price=entry_price,
                quantity=quantity, model_id=model_id,
                sl_price=sl_price, tp_price=tp_price,
                entry_time=entry_time,
                retirement_cfg=retirement_cfg,
            )
            _cancel_leg(t212, limit_order_id, t212_ticker)
            break

        if limit_status == "FILLED":
            fill_price = float(limit_order.get("fillPrice") or tp_price)
            _close_position(
                engine, t212_ticker, cooldown_td, "OCO_TP",
                exit_price=fill_price, entry_price=entry_price,
                quantity=quantity, model_id=model_id,
                sl_price=sl_price, tp_price=tp_price,
                entry_time=entry_time,
                retirement_cfg=retirement_cfg,
            )
            _cancel_leg(t212, stop_order_id, t212_ticker)
            break

        if stop_status in _TERMINAL and stop_status != "FILLED":
            log.warning(
                "Stop leg %s for %s reached %s without fill — cancelling limit leg",
                stop_order_id, t212_ticker, stop_status,
            )
            _cancel_leg(t212, limit_order_id, t212_ticker)
            wh.notify("WARNING", f"OCO stop leg cancelled/rejected for {t212_ticker} — limit leg cancelled",
                      category="oco")
            break

        if limit_status in _TERMINAL and limit_status != "FILLED":
            log.warning(
                "Limit leg %s for %s reached %s without fill — cancelling stop leg",
                limit_order_id, t212_ticker, limit_status,
            )
            _cancel_leg(t212, stop_order_id, t212_ticker)
            wh.notify("WARNING", f"OCO limit leg cancelled/rejected for {t212_ticker} — stop leg cancelled",
                      category="oco")
            break


def _cancel_leg(t212: T212Client, order_id: str, t212_ticker: str) -> None:
    try:
        t212.cancel_order(order_id)
    except Exception as exc:
        log.warning("Failed to cancel OCO leg %s for %s: %s", order_id, t212_ticker, exc)


def _close_position(
    engine: Engine,
    t212_ticker: str,
    cooldown_td: timedelta,
    exit_reason: str,
    exit_price: float = 0.0,
    entry_price: float = 0.0,
    quantity: float = 0.0,
    model_id: str = "",
    sl_price: float = 0.0,
    tp_price: float = 0.0,
    entry_time: Optional[datetime] = None,
    retirement_cfg: Optional[ModelRetirementConfig] = None,
) -> None:
    now = datetime.utcnow()
    realized_pnl = (exit_price - entry_price) * quantity
    pnl_pct = (exit_price - entry_price) / entry_price if entry_price else 0.0

    with Session(engine) as session:
        pos_repo = PositionRepo(session)
        pos_repo.remove(t212_ticker)
        pos_repo.upsert(Position(
            t212_ticker=t212_ticker,
            quantity=0,
            avg_entry=0,
            cooldown_until_ts=now + cooldown_td,
        ))
        if model_id:
            journal_repo = TradeJournalRepo(session)
            journal_repo.save(TradeJournal(
                model_id=model_id,
                ticker=t212_ticker,
                entry_price=entry_price,
                exit_price=exit_price,
                quantity=quantity,
                entry_time=entry_time or now,
                exit_time=now,
                exit_reason=exit_reason,
                sl_price=sl_price if sl_price else None,
                tp_price=tp_price if tp_price else None,
                realized_pnl=realized_pnl,
                pnl_pct=pnl_pct,
            ))
            try:
                from alphaTrade.risk.performance import check_retirement, record_trade as _record_trade
                cfg = retirement_cfg or ModelRetirementConfig()
                _record_trade(session, model_id=model_id, realized_pnl=realized_pnl, cfg=cfg)
                if check_retirement(session, model_id=model_id, cfg=cfg):
                    wh.notify("WARNING", f"Model {model_id} auto-retired: performance below threshold",
                              category="model-retirement")
            except Exception as exc:
                log.warning("Performance record failed for %s: %s", model_id, exc)

    log.info("OCO %s hit for %s — position closed pnl=%.2f", exit_reason, t212_ticker, realized_pnl)
    wh.notify("INFO", f"OCO {exit_reason} hit for {t212_ticker} pnl={realized_pnl:.2f}", category="oco")
