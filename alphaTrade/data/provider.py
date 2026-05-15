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

    def fetch_vix(self) -> float | None:
        """Return latest VIX close. Returns None on failure or if unsupported."""
        return None
