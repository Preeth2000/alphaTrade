"""Polygon.io DataProvider implementation.

Requires POLYGON_API_KEY env var.
Polygon tickers for US equities match yfinance format (e.g. AAPL).
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from alphalink.adapter.validators import validate_ohlcv
from alphalink.data.provider import DataProvider


# Map manifest["interval"] (yfinance format) → (multiplier, timespan) for Polygon
_INTERVAL_MAP: dict[str, tuple[int, str]] = {
    "1m":  (1,  "minute"),
    "5m":  (5,  "minute"),
    "15m": (15, "minute"),
    "1h":  (1,  "hour"),
    "1d":  (1,  "day"),
    "1wk": (1,  "week"),
}

# Approximate calendar days to look back to get enough bars
_LOOKBACK_DAYS: dict[str, int] = {
    "1m":  7,
    "5m":  60,
    "15m": 60,
    "1h":  730,
    "1d":  3650,
    "1wk": 3650,
}


class PolygonProvider(DataProvider):
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def fetch_ohlcv(self, ticker: str, interval: str, bars: int) -> pd.DataFrame:
        from polygon import RESTClient

        if interval not in _INTERVAL_MAP:
            raise ValueError(
                f"Unsupported interval for Polygon provider: {interval!r}. "
                f"Supported: {list(_INTERVAL_MAP)}"
            )

        multiplier, timespan = _INTERVAL_MAP[interval]
        lookback_days = _LOOKBACK_DAYS.get(interval, 3650)
        to_date = date.today()
        from_date = to_date - timedelta(days=lookback_days)

        client = RESTClient(api_key=self._api_key)
        aggs = client.get_aggs(
            ticker=ticker,
            multiplier=multiplier,
            timespan=timespan,
            from_=from_date.isoformat(),
            to=to_date.isoformat(),
            limit=50000,
        )

        if not aggs:
            raise RuntimeError(f"Polygon returned no data for {ticker} at {interval}")

        rows = [
            {
                "Open":   a.open,
                "High":   a.high,
                "Low":    a.low,
                "Close":  a.close,
                "Volume": a.volume,
            }
            for a in aggs
        ]
        df = pd.DataFrame(rows)
        result = df.tail(bars + 100)
        validate_ohlcv(result, interval, ticker)
        return result
