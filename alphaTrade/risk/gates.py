"""Risk gate pipeline. Applied per signal before order submission."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from alphaTrade.utils import utcnow
from typing import Optional

from alphaTrade.store.repos import PositionRepo

# Intervals where pyramiding is meaningful enough to allow when safe_mode=False
_PYRAMID_SAFE_INTERVALS: frozenset[str] = frozenset({"1h", "1d", "1wk"})
# Intervals where pyramiding is too dangerous for normal use — require dangerously_allow_pyramid=True
_PYRAMID_DANGEROUS_INTERVALS: frozenset[str] = frozenset({"1m", "5m", "15m"})


@dataclass
class GateResult:
    approved: bool
    reason: str = ""


def run_gates(
    signal: str,
    t212_ticker: str,
    position_repo: PositionRepo,
    max_positions: int,
    daily_loss_halted: bool,
    now: Optional[datetime] = None,
    model_id: str = "",
    perf_repo=None,
    yf_ticker: str = "",
    equity: float = 0.0,
    sector_repo=None,
    risk_cfg=None,
    interval: str = "",
    safe_mode: bool = True,
    dangerously_allow_pyramid: bool = False,
) -> GateResult:
    if now is None:
        now = utcnow()

    if daily_loss_halted and signal != "HOLD":
        return GateResult(False, "daily_loss_halt active")

    if signal == "HOLD":
        return GateResult(False, "HOLD — no action")

    # Retirement check
    if model_id and perf_repo is not None:
        if perf_repo.is_retired(model_id):
            return GateResult(False, f"model {model_id!r} retired")

    pos = position_repo.get(t212_ticker)

    # Cooldown check
    if pos and pos.cooldown_until_ts and now < pos.cooldown_until_ts:
        return GateResult(False, f"cooldown until {pos.cooldown_until_ts.isoformat()}")

    # Sector gate (BUY only)
    if signal == "BUY" and yf_ticker and sector_repo is not None and risk_cfg is not None:
        from alphaTrade.risk.sector import check_sector_gate
        rejection = check_sector_gate(
            yf_ticker=yf_ticker,
            signal=signal,
            equity=equity,
            position_repo=position_repo,
            sector_repo=sector_repo,
            portfolio_mode=risk_cfg.portfolio_mode,
            balanced_cfg=risk_cfg.balanced,
            unbalanced_cfg=risk_cfg.unbalanced,
        )
        if rejection:
            return GateResult(False, rejection)

    all_positions = position_repo.all()
    open_count = len([p for p in all_positions if p.quantity > 0])

    if signal == "BUY":
        if pos and pos.quantity > 0:
            if safe_mode:
                return GateResult(False, "already long — safe mode: no pyramiding")
            if interval in _PYRAMID_SAFE_INTERVALS:
                pass  # pyramiding allowed for this interval
            elif dangerously_allow_pyramid:
                pass  # explicitly unlocked despite dangerous interval
            else:
                return GateResult(
                    False,
                    f"already long — {interval or 'short'} interval pyramiding requires dangerously_allow_pyramid",
                )
        if open_count >= max_positions:
            return GateResult(False, f"max positions {max_positions} reached")

    if signal == "SELL":
        if not pos or pos.quantity <= 0:
            return GateResult(False, "no long position to SELL (short selling disabled in v1)")

    return GateResult(True)
