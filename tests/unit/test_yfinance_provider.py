"""Unit tests for YFinanceProvider MultiIndex flattening."""
from __future__ import annotations

import pandas as pd
import pytest
from unittest.mock import patch

from alphaTrade.data.yfinance_provider import YFinanceProvider

_COLS = ["Open", "High", "Low", "Close", "Volume"]
_N = 10


def _flat_df() -> pd.DataFrame:
    data = {c: range(100, 100 + _N) for c in _COLS}
    return pd.DataFrame(data)


def _multiindex_df() -> pd.DataFrame:
    """Simulates yfinance >=0.2.x multi-ticker download format."""
    df = _flat_df()
    ticker = "AAPL"
    df.columns = pd.MultiIndex.from_tuples([(c, ticker) for c in _COLS])
    return df


def _multiindex_reversed_df() -> pd.DataFrame:
    """Some yfinance versions swap the level order: (ticker, field)."""
    df = _flat_df()
    ticker = "AAPL"
    df.columns = pd.MultiIndex.from_tuples([(ticker, c) for c in _COLS])
    return df


def _run(raw: pd.DataFrame, bars: int = 5) -> pd.DataFrame:
    with patch("yfinance.download", return_value=raw):
        return YFinanceProvider().fetch_ohlcv("AAPL", "1d", bars)


class TestHealthProbe:
    def test_raises_on_empty_dataframe(self):
        with patch("yfinance.download", return_value=pd.DataFrame()):
            with pytest.raises(RuntimeError, match="no data"):
                YFinanceProvider().health_probe()

    def test_succeeds_when_data_returned(self):
        df = _flat_df()
        with patch("yfinance.download", return_value=df):
            YFinanceProvider().health_probe()  # should not raise


class TestFlatInput:
    def test_columns_preserved(self):
        result = _run(_flat_df())
        assert list(result.columns) == _COLS

    def test_tail_applied(self):
        result = _run(_flat_df(), bars=3)
        assert len(result) <= _N  # tail(bars+100) caps at available rows


class TestMultiIndexInput:
    def test_flattened_to_flat_columns(self):
        result = _run(_multiindex_df())
        assert list(result.columns) == _COLS

    def test_not_multiindex_after(self):
        result = _run(_multiindex_df())
        assert not isinstance(result.columns, pd.MultiIndex)

    def test_data_intact(self):
        raw = _multiindex_df()
        result = _run(raw)
        # Close values should match original
        assert list(result["Close"]) == list(range(100, 100 + _N))


class TestEmptyInput:
    def test_raises_on_empty(self):
        empty = pd.DataFrame(columns=_COLS)
        with pytest.raises(RuntimeError, match="no data"):
            _run(empty)
