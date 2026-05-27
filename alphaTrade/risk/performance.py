"""Rolling model performance tracking and auto-retirement."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlmodel import Session

from alphaTrade.config import ModelRetirementConfig, ModelRetirementOverride
from alphaTrade.store.repos import ModelPerformanceRepo

log = logging.getLogger(__name__)


def _parse_period(s: str) -> timedelta:
    if s.endswith("d"):
        return timedelta(days=int(s[:-1]))
    raise ValueError(f"Invalid period format: {s!r}. Use e.g. '30d'")


def _effective_config(
    global_cfg: ModelRetirementConfig,
    per_model: ModelRetirementOverride,
) -> ModelRetirementConfig:
    overrides = {k: v for k, v in per_model.model_dump().items() if v is not None}
    return ModelRetirementConfig(**{**global_cfg.model_dump(), **overrides})


def record_trade(
    session: Session,
    model_id: str,
    realized_pnl: float,
    cfg: ModelRetirementConfig,
) -> None:
    """Record a closed trade's P&L into rolling window for model_id."""
    repo = ModelPerformanceRepo(session)
    perf = repo.get_or_create(model_id)

    trades: list[float] = list(perf.rolling_trades_json)
    trades.append(realized_pnl)
    if len(trades) > cfg.lookback_trades:
        trades = trades[-cfg.lookback_trades:]

    perf.rolling_trades_json = trades
    if perf.trade_count == 0:
        perf.first_trade_at = datetime.utcnow()
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
    """Return True and mark retired if model breaches thresholds, False otherwise."""
    if not cfg.enabled:
        return False

    repo = ModelPerformanceRepo(session)
    perf = repo.get_or_create(model_id)

    if perf.retired:
        return True

    period = _parse_period(cfg.min_evaluation_period)
    age_ok = (
        perf.first_trade_at is not None
        and (datetime.utcnow() - perf.first_trade_at) >= period
    )
    trades_ok = perf.trade_count >= cfg.min_trades_before_evaluation

    if not (age_ok or trades_ok):
        return False

    trades: list[float] = list(perf.rolling_trades_json)
    win_rate = sum(1 for t in trades if t > 0) / len(trades) if trades else 0.0
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
