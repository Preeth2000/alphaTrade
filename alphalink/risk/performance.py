"""Rolling model performance tracking and auto-retirement."""
from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlmodel import Session

from alphalink.config import ModelRetirementConfig
from alphalink.store.repos import ModelPerformanceRepo

log = logging.getLogger(__name__)


def record_trade(
    session: Session,
    model_id: str,
    realized_pnl: float,
    cfg: ModelRetirementConfig,
) -> None:
    """Record a closed trade's P&L into rolling window for model_id."""
    repo = ModelPerformanceRepo(session)
    perf = repo.get_or_create(model_id)

    trades: list[float] = json.loads(perf.rolling_trades_json)
    trades.append(realized_pnl)
    if len(trades) > cfg.lookback_trades:
        trades = trades[-cfg.lookback_trades:]

    perf.rolling_trades_json = json.dumps(trades)
    perf.trade_count += 1
    if realized_pnl > 0:
        perf.win_count += 1
    perf.rolling_pnl = sum(trades)

    repo.update(perf)


def check_retirement(
    session: Session,
    model_id: str,
    cfg: ModelRetirementConfig,
) -> bool:
    """Return True and mark retired if model breaches thresholds. Return False otherwise."""
    if not cfg.enabled:
        return False

    repo = ModelPerformanceRepo(session)
    perf = repo.get_or_create(model_id)

    if perf.retired:
        return True

    trades: list[float] = json.loads(perf.rolling_trades_json)
    if len(trades) < cfg.lookback_trades:
        return False

    win_rate = sum(1 for t in trades if t > 0) / len(trades)
    rolling_pnl = sum(trades)

    should_retire = win_rate < cfg.min_win_rate or rolling_pnl < cfg.min_rolling_pnl
    if should_retire:
        perf.retired = True
        perf.retired_at = datetime.utcnow()
        repo.update(perf)
        log.warning(
            "Model %s retired: win_rate=%.2f (min=%.2f) rolling_pnl=%.2f (min=%.2f)",
            model_id, win_rate, cfg.min_win_rate, rolling_pnl, cfg.min_rolling_pnl,
        )
    return should_retire
