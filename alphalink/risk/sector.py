"""Sector/correlation position limit gate."""
from __future__ import annotations

import logging
from typing import Optional

from alphaTrade.config import BalancedPortfolioConfig, UnbalancedPortfolioConfig
from alphaTrade.store.repos import PositionRepo, SectorCacheRepo

log = logging.getLogger(__name__)


def fetch_sector(yf_ticker: str, sector_repo: SectorCacheRepo) -> str:
    """Return GICS sector string for yf_ticker. Fetches from cache, then yfinance."""
    cached = sector_repo.get(yf_ticker)
    if cached:
        return cached.sector

    try:
        import yfinance as yf
        info = yf.Ticker(yf_ticker).info
        sector = info.get("sector", "Unknown") or "Unknown"
    except Exception as exc:
        log.warning("Sector fetch failed for %s: %s", yf_ticker, exc)
        sector = "Unknown"

    if sector != "Unknown":
        sector_repo.put(yf_ticker, sector)
    return sector


def check_sector_gate(
    yf_ticker: str,
    signal: str,
    equity: float,
    position_repo: PositionRepo,
    sector_repo: SectorCacheRepo,
    portfolio_mode: str,
    balanced_cfg: BalancedPortfolioConfig,
    unbalanced_cfg: UnbalancedPortfolioConfig,
) -> Optional[str]:
    """Return rejection reason string if BUY should be blocked, None if approved.

    SELL signals always pass. Unknown sector always passes (fail-open).
    """
    if signal != "BUY":
        return None

    ticker_sector = fetch_sector(yf_ticker, sector_repo)
    if ticker_sector == "Unknown":
        log.warning("Unknown sector for %s — skipping sector gate", yf_ticker)
        return None

    all_positions = [p for p in position_repo.all() if p.quantity > 0]

    ticker_to_sector: dict[str, str] = {}
    for pos in all_positions:
        cached = sector_repo.get(pos.t212_ticker)
        ticker_to_sector[pos.t212_ticker] = cached.sector if cached else "Unknown"

    same_sector_positions = [
        p for p in all_positions
        if ticker_to_sector.get(p.t212_ticker, "Unknown").lower() == ticker_sector.lower()
    ]

    if portfolio_mode == "balanced":
        if equity <= 0:
            return None
        sector_value = sum(p.quantity * p.avg_entry for p in same_sector_positions)
        sector_pct = sector_value / equity
        if sector_pct >= balanced_cfg.max_sector_pct:
            return (
                f"sector limit: {ticker_sector} at {sector_pct:.1%} "
                f">= max {balanced_cfg.max_sector_pct:.1%}"
            )
    else:  # unbalanced
        sector_key = ticker_sector.lower()
        limit = unbalanced_cfg.sector_overrides.get(sector_key, unbalanced_cfg.max_per_sector)
        if len(same_sector_positions) >= limit:
            return (
                f"sector limit: {ticker_sector} has {len(same_sector_positions)} "
                f">= max {limit} positions"
            )

    return None
