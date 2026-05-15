"""yfinance DataProvider implementation."""
from __future__ import annotations

import logging

import pandas as pd

from alphaTrade.adapter.validators import validate_ohlcv
from alphaTrade.data.provider import DataProvider

log = logging.getLogger(__name__)


class YFinanceProvider(DataProvider):
    def fetch_ohlcv(self, ticker: str, interval: str, bars: int) -> pd.DataFrame:
        import yfinance as yf

        # yfinance period strings per interval for sufficient history
        _PERIOD = {
            "1m": "7d",
            "5m": "60d",
            "15m": "60d",
            "1h": "730d",
            "1d": "max",
            "1wk": "max",
        }
        period = _PERIOD.get(interval, "max")

        df = yf.download(
            ticker,
            period=period,
            interval=interval,
            auto_adjust=True,
            progress=False,
        )
        if df.empty:
            raise RuntimeError(f"yfinance returned no data for {ticker} at {interval}")

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        result = df.tail(bars + 100)
        validate_ohlcv(result, interval, ticker)
        return result

    def fetch_vix(self) -> float | None:
        """Return latest VIX close. Returns None on failure."""
        try:
            import yfinance as yf
            hist = yf.Ticker("^VIX").history(period="1d")
            if hist.empty:
                raise ValueError("Empty VIX history")
            return float(hist["Close"].iloc[-1])
        except Exception as exc:
            log.warning("VIX fetch failed: %s", exc)
            return None

    def fetch_ohlcv_range(
        self,
        ticker: str,
        interval: str,
        start: str,
        end: str,
        extra_bars: int = 50,
    ) -> "pd.DataFrame | None":
        """Fetch OHLCV for date range [start, end] plus extra_bars warm-up before start."""
        import pandas as pd
        from datetime import timedelta

        try:
            # Compute warm-up start: walk back extra_bars × interval duration
            _SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600,
                        "1d": 86400, "1wk": 604800}
            secs = _SECONDS.get(interval, 86400)
            warmup_delta = timedelta(seconds=secs * extra_bars)
            if interval in ("1d", "1wk"):
                warmup_delta = timedelta(seconds=int(secs * extra_bars * 1.5))
            start_dt = pd.Timestamp(start) - warmup_delta
            start_str = start_dt.strftime("%Y-%m-%d")

            import yfinance as yf
            df = yf.download(ticker, start=start_str, end=end, interval=interval,
                             auto_adjust=True, progress=False)
            if df is None or df.empty:
                return None
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df.sort_index()
            # Flatten MultiIndex columns (yfinance >= 0.2.x may return them)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            # Rename lowercase columns if needed
            col_map = {c: c.capitalize() for c in df.columns if c in ("open","high","low","close","volume")}
            if col_map:
                df = df.rename(columns=col_map)
            return df
        except Exception as exc:
            log.warning("fetch_ohlcv_range failed for %s: %s", ticker, exc)
            return None
