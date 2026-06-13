"""Polygon.io DataProvider implementation.

Requires POLYGON_API_KEY env var.
Polygon tickers for US equities match yfinance format (e.g. AAPL).
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

from alphaTrade.adapter.validators import validate_ohlcv
from alphaTrade.data.provider import DataProvider

log = logging.getLogger(__name__)

# Map manifest["interval"] (yfinance format) → (multiplier, timespan) for Polygon
_INTERVAL_MAP: dict[str, tuple[int, str]] = {
    "1m":  (1,  "minute"),
    "5m":  (5,  "minute"),
    "15m": (15, "minute"),
    "1h":  (1,  "hour"),
    "1d":  (1,  "day"),
    "1wk": (1,  "week"),
}

# Approximate calendar days to look back to get enough bars (fetch_ohlcv)
_LOOKBACK_DAYS: dict[str, int] = {
    "1m":  7,
    "5m":  60,
    "15m": 60,
    "1h":  730,
    "1d":  3650,
    "1wk": 3650,
}

# Max history available via Polygon (conservative; free tier gets ~2y intraday)
_MAX_LOOKBACK: dict[str, int] = {
    "1m":  730,
    "5m":  730,
    "15m": 730,
    "1h":  3650,
    "1d":  36500,
    "1wk": 36500,
}


class PolygonProvider(DataProvider):
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def max_lookback_days(self, interval: str) -> int:
        return _MAX_LOOKBACK.get(interval, 36500)

    def health_probe(self) -> None:
        from polygon import RESTClient
        to_date = date.today()
        from_date = to_date - timedelta(days=7)
        client = RESTClient(api_key=self._api_key)
        aggs = client.get_aggs(
            ticker="SPY",
            multiplier=1,
            timespan="day",
            from_=from_date.isoformat(),
            to=to_date.isoformat(),
            limit=10,
        )
        if not aggs:
            raise RuntimeError("Polygon health probe returned no bars for SPY in the last 7 days")

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
                "Open":         a.open,
                "High":         a.high,
                "Low":          a.low,
                "Close":        a.close,
                "Volume":       a.volume,
                "Transactions": getattr(a, "transactions", None),
            }
            for a in aggs
        ]
        df = pd.DataFrame(rows)
        result = df.tail(bars + 100)
        validate_ohlcv(result, interval, ticker)
        return result

    def fetch_ohlcv_range(
        self,
        ticker: str,
        interval: str,
        start: str,
        end: str,
        extra_bars: int = 50,
    ) -> "pd.DataFrame | None":
        """Fetch OHLCV for [start, end] plus extra_bars warm-up before start."""
        from datetime import timezone

        if interval not in _INTERVAL_MAP:
            log.warning("fetch_ohlcv_range: unsupported interval %r for Polygon", interval)
            return None

        try:
            from polygon import RESTClient

            _SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600,
                        "1d": 86400, "1wk": 604800}
            secs = _SECONDS.get(interval, 86400)
            warmup_delta = timedelta(seconds=secs * extra_bars)

            start_dt = pd.Timestamp(start, tz="UTC") - warmup_delta

            # Clamp to Polygon history limit
            earliest = pd.Timestamp.now(tz=timezone.utc).normalize() - timedelta(days=self.max_lookback_days(interval))
            if start_dt < earliest:
                log.debug(
                    "fetch_ohlcv_range: clamping start %s → %s (polygon %s limit=%dd)",
                    start_dt.date(), earliest.date(), interval, self.max_lookback_days(interval),
                )
                start_dt = earliest

            multiplier, timespan = _INTERVAL_MAP[interval]
            client = RESTClient(api_key=self._api_key)
            aggs = client.get_aggs(
                ticker=ticker,
                multiplier=multiplier,
                timespan=timespan,
                from_=start_dt.strftime("%Y-%m-%d"),
                to=end,
                limit=50000,
            )
            if not aggs:
                return None

            rows = [
                {
                    "Open":   a.open,
                    "High":   a.high,
                    "Low":    a.low,
                    "Close":  a.close,
                    "Volume": a.volume,
                    "timestamp": a.timestamp,
                }
                for a in aggs
            ]
            df = pd.DataFrame(rows)
            if "timestamp" in df.columns:
                df.index = pd.to_datetime(df.pop("timestamp"), unit="ms", utc=True)
            df = df.sort_index()
            return df
        except Exception as exc:
            log.warning("fetch_ohlcv_range failed for %s: %s", ticker, exc)
            return None
