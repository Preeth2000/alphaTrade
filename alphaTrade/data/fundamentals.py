"""Point-in-time fundamentals loader. Mirrors alphaGen att/data/fundamentals.py."""
from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger(__name__)


def load_fundamentals(
    ticker: str,
    start: str | None,
    end: str | None,
    include_earnings: bool = True,
    include_sector: bool = False,
    announcement_lag_days: int = 1,
) -> pd.DataFrame:
    """Return point-in-time fundamentals indexed by UTC date, forward-filled.

    Columns (when enabled):
        reported_eps       — EPS from last announced quarter
        eps_surprise_pct   — analyst surprise % from last announced quarter
        sector_<name>      — one-hot binary for ticker's GICS sector

    announcement_lag_days shifts earnings dates forward to prevent look-ahead
    from after-hours announcements.
    """
    import yfinance as yf

    t = yf.Ticker(ticker)
    result: dict[str, pd.Series] = {}

    if include_earnings:
        try:
            ed = t.earnings_dates
            if ed is not None and not ed.empty:
                ed = ed.sort_index()
                if ed.index.tz is None:
                    ed.index = ed.index.tz_localize("UTC")
                else:
                    ed.index = ed.index.tz_convert("UTC")

                if "Reported EPS" in ed.columns:
                    eps = ed["Reported EPS"].dropna().rename("reported_eps")
                    eps.index = eps.index + pd.Timedelta(days=announcement_lag_days)
                    result["reported_eps"] = eps

                if "Surprise(%)" in ed.columns:
                    surp = ed["Surprise(%)"].dropna().rename("eps_surprise_pct")
                    surp.index = surp.index + pd.Timedelta(days=announcement_lag_days)
                    result["eps_surprise_pct"] = surp
        except Exception as exc:
            log.warning("earnings_dates fetch failed for %s: %s", ticker, exc)

    if include_sector:
        try:
            info = t.info
            sector = info.get("sector", "Unknown") or "Unknown"
            col = f"sector_{sector.replace(' ', '_').lower()}"
            result[col] = pd.Series(dtype=float)  # filled below as scalar
        except Exception as exc:
            log.warning("sector fetch failed for %s: %s", ticker, exc)

    if not result:
        return pd.DataFrame()

    # Build date range covering start..end (daily UTC)
    if start is not None and end is not None:
        idx = pd.date_range(start=start, end=end, freq="D", tz="UTC")
    else:
        # Fall back to union of all known dates
        all_dates = pd.DatetimeIndex([])
        for s in result.values():
            if hasattr(s, "index") and len(s) > 0:
                all_dates = all_dates.union(s.index)
        if all_dates.empty:
            return pd.DataFrame()
        idx = pd.date_range(start=all_dates.min(), end=all_dates.max(), freq="D", tz="UTC")

    df = pd.DataFrame(index=idx)

    for col, series in result.items():
        if isinstance(series, pd.Series) and len(series) > 0:
            df[col] = series.reindex(idx, method=None)
            df[col] = df[col].ffill()
        else:
            # Scalar sector column — fill entire range
            df[col] = 1.0

    return df.dropna(how="all")


def needs_fundamentals(feature_names: list[str]) -> tuple[bool, bool]:
    """Return (needs_earnings, needs_sector) from manifest feature_names."""
    needs_eps = any(n in ("reported_eps", "eps_surprise_pct") for n in feature_names)
    needs_sector = any(n.startswith("sector_") for n in feature_names)
    return needs_eps, needs_sector


def merge_fundamentals_into(df: pd.DataFrame, ticker: str, feature_names: list[str]) -> pd.DataFrame:
    """Merge point-in-time fundamentals into OHLCV df in-place (returns new df).

    No-op if no fundamentals features are in feature_names.
    """
    needs_eps, needs_sector = needs_fundamentals(feature_names)
    if not needs_eps and not needs_sector:
        return df

    # Determine date range from df index
    if df.index.tz is not None:
        idx_utc = df.index.tz_convert("UTC")
    else:
        idx_utc = df.index.tz_localize("UTC")

    start = idx_utc.min().strftime("%Y-%m-%d")
    end = idx_utc.max().strftime("%Y-%m-%d")

    fund = load_fundamentals(
        ticker=ticker,
        start=start,
        end=end,
        include_earnings=needs_eps,
        include_sector=needs_sector,
    )

    if fund.empty:
        log.warning("fundamentals returned empty for %s — features will be NaN", ticker)
        return df

    # Align: normalize df index to date, reindex fund to it, then assign columns
    df_dates = idx_utc.normalize()
    fund_reindexed = fund.reindex(fund.index.normalize().unique())
    fund_aligned = fund_reindexed.reindex(df_dates).ffill()
    fund_aligned.index = df.index

    for col in fund_aligned.columns:
        df[col] = fund_aligned[col].values

    return df
