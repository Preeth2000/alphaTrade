"""DataProvider ABC."""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class DataProvider(ABC):
    @abstractmethod
    def fetch_ohlcv(self, ticker: str, interval: str, bars: int) -> pd.DataFrame:
        """Fetch OHLCV bars.

        Returns df with columns: Open, High, Low, Close, Volume (case-sensitive).
        Fetches at least `bars` rows. Callers pass window + 100 for TA-Lib warmup.
        """

    @abstractmethod
    def fetch_ohlcv_range(
        self,
        ticker: str,
        interval: str,
        start: str,
        end: str,
        extra_bars: int = 50,
    ) -> "pd.DataFrame | None":
        """Fetch OHLCV for [start, end] plus extra_bars warm-up before start.

        Implementations must clamp start to provider/interval history limits.
        Returns None on failure or insufficient data.
        """

    @abstractmethod
    def health_probe(self) -> None:
        """Fetch a small recent slice of data to verify the feed is working.
        Raises on any failure. Should be cheap (short date range, few bars)."""
        ...

    def max_lookback_days(self, interval: str) -> int:
        """Max calendar days of history available for interval. Override per provider."""
        return 36500

    def fetch_vix(self) -> float | None:
        """Return latest VIX close. Returns None on failure or if unsupported."""
        return None
