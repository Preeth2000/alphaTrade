"""Position sizing: fixed, ATR-based, and VIX-based modes."""
from __future__ import annotations

import logging

from alphaTrade.utils import utcnow

log = logging.getLogger(__name__)

_vix_cache: dict[str, float] = {}  # date-string → VIX value


def _get_vix() -> float:
    """Fetch VIX from yfinance, cached by calendar date. Returns 20.0 on failure."""
    today = utcnow().date().isoformat()
    if today in _vix_cache:
        return _vix_cache[today]
    try:
        import yfinance as yf
        hist = yf.Ticker("^VIX").history(period="1d")
        if hist.empty:
            raise ValueError("Empty VIX history")
        vix = float(hist["Close"].iloc[-1])
        _vix_cache[today] = vix
        return vix
    except Exception as exc:
        log.warning("VIX fetch failed, using 20.0: %s", exc)
        return 20.0


def compute_quantity(
    equity: float,
    current_price: float,
    size_pct: float,
    mode: str = "fixed",
    # ATR mode params
    atr: float = 0.0,
    atr_risk_pct: float = 0.01,
    atr_multiplier: float = 2.0,
    # VIX mode params
    current_vix: float | None = None,
    vix_scalar: float = 20.0,
    vix_base_size_pct: float = 0.05,
    vix_max_size_pct: float = 0.15,
) -> float:
    """Return share quantity. mode: 'fixed' | 'atr' | 'vix'."""
    if current_price <= 0:
        raise ValueError(f"Invalid current_price: {current_price}")

    if mode == "atr":
        if atr <= 0:
            cash = equity * size_pct  # fallback to fixed
        else:
            dollar_risk = equity * atr_risk_pct
            stop_distance = atr * atr_multiplier
            cash = dollar_risk / stop_distance * current_price

    elif mode == "vix":
        vix = current_vix if current_vix is not None else _get_vix()
        multiplier = vix_scalar / vix if vix > 0 else 1.0
        effective_pct = min(vix_base_size_pct * multiplier, vix_max_size_pct)
        cash = equity * effective_pct

    else:  # fixed
        cash = equity * size_pct

    return max(round(cash / current_price, 6), 0.0)
