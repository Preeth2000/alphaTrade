"""OHLCV data sanity checks. Raises ValueError on bad data."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

log = logging.getLogger(__name__)

_INTERVAL_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "1d": 86400,
    "1wk": 604800,
}

# Max age of latest bar before flagging as stale.
# Daily/weekly use 5d/14d to cover weekends and public holidays.
_MAX_AGE_SECONDS: dict[str, int] = {
    "1m":  120,
    "5m":  600,
    "15m": 1800,
    "1h":  7200,
    "1d":  5 * 86400,
    "1wk": 14 * 86400,
}


def validate_ohlcv(df: pd.DataFrame, interval: str, ticker: str = "") -> None:
    """Validate OHLCV DataFrame. Raises ValueError describing the first violation.

    Checks:
    - DataFrame not empty
    - Required columns present
    - No NaN in OHLC
    - All prices > 0
    - High >= Low >= 0 per row
    - Volume >= 0
    - Latest bar timestamp within 2× interval of now
    """
    tag = f"[{ticker}@{interval}] " if ticker else f"[{interval}] "

    if df.empty:
        raise ValueError(f"{tag}empty DataFrame")

    required = {"Open", "High", "Low", "Close", "Volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{tag}missing columns: {missing}")

    ohlc = df[["Open", "High", "Low", "Close"]]

    if ohlc.isnull().any().any():
        bad = ohlc.columns[ohlc.isnull().any()].tolist()
        raise ValueError(f"{tag}NaN in OHLC columns: {bad}")

    if (ohlc <= 0).any().any():
        bad = ohlc.columns[(ohlc <= 0).any()].tolist()
        raise ValueError(f"{tag}non-positive price in: {bad}")

    if (df["Volume"] < 0).any():
        raise ValueError(f"{tag}negative Volume values")

    zero_vol = int((df["Volume"] == 0).sum())
    if zero_vol:
        log.warning("%szero-volume bars: %d row(s)", tag, zero_vol)

    if (df["High"] < df["Low"]).any():
        raise ValueError(f"{tag}High < Low on some rows")

    if (df["Low"] < 0).any():
        raise ValueError(f"{tag}negative Low values")

    if (df["Close"] > df["High"]).any() or (df["Close"] < df["Low"]).any():
        raise ValueError(f"{tag}Close outside [Low, High] on some rows")

    # Staleness check — index must be DatetimeTzAware or naive UTC
    if hasattr(df.index, "to_pydatetime"):
        try:
            latest = df.index[-1]
            if hasattr(latest, "tzinfo") and latest.tzinfo is not None:
                latest_utc = latest.to_pydatetime().astimezone(timezone.utc)
            else:
                latest_utc = latest.to_pydatetime().replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            max_age = _MAX_AGE_SECONDS.get(interval, _INTERVAL_SECONDS.get(interval, 86400) * 2)
            cutoff = now - timedelta(seconds=max_age)
            if latest_utc < cutoff:
                raise ValueError(
                    f"{tag}latest bar {latest_utc.isoformat()} is stale "
                    f"(older than 2× interval={interval})"
                )
        except (AttributeError, TypeError):
            pass  # non-datetime index — skip staleness check
