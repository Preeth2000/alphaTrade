"""Unit tests for PolygonProvider."""
from __future__ import annotations

import pandas as pd
import pytest
from unittest.mock import MagicMock, patch

from alphaTrade.data.polygon_provider import PolygonProvider

_COLS = ["Open", "High", "Low", "Close", "Volume"]
_N = 10


def _make_agg(o=100.0, h=105.0, lo=99.0, c=102.0, v=1000.0) -> MagicMock:
    a = MagicMock()
    a.open = o
    a.high = h
    a.low = lo
    a.close = c
    a.volume = v
    return a


def _good_aggs(n: int = _N) -> list[MagicMock]:
    return [_make_agg() for _ in range(n)]


def _run(aggs, ticker: str = "AAPL", interval: str = "1d", bars: int = 5) -> pd.DataFrame:
    with patch("polygon.RESTClient") as MockClient:
        MockClient.return_value.get_aggs.return_value = aggs
        return PolygonProvider(api_key="fake").fetch_ohlcv(ticker, interval, bars)


class TestHealthProbe:
    def _run_probe(self, aggs):
        with patch("polygon.RESTClient") as MockClient:
            MockClient.return_value.get_aggs.return_value = aggs
            PolygonProvider(api_key="fake").health_probe()

    def test_raises_on_empty_list(self):
        with pytest.raises(RuntimeError, match="no bars for SPY"):
            self._run_probe([])

    def test_raises_on_none(self):
        with pytest.raises(RuntimeError, match="no bars for SPY"):
            self._run_probe(None)

    def test_succeeds_on_non_empty_list(self):
        self._run_probe(_good_aggs(n=3))  # should not raise

    def test_propagates_sdk_exception(self):
        with patch("polygon.RESTClient") as MockClient:
            MockClient.return_value.get_aggs.side_effect = RuntimeError("auth failure")
            with pytest.raises(RuntimeError, match="auth failure"):
                PolygonProvider(api_key="fake").health_probe()


class TestValidateOhlcvCalled:
    def test_raises_on_corrupt_data(self):
        """validate_ohlcv should catch negative prices before returning."""
        bad_aggs = [_make_agg(o=-1.0, h=-1.0, lo=-1.0, c=-1.0) for _ in range(_N)]
        with pytest.raises(ValueError, match="non-positive price"):
            _run(bad_aggs)

    def test_raises_on_nan_data(self):
        """validate_ohlcv should catch NaN values before returning."""
        nan_agg = _make_agg(o=float("nan"))
        with pytest.raises(ValueError, match="NaN"):
            _run([nan_agg] * _N)


class TestNormalFetch:
    def test_returns_dataframe(self):
        result = _run(_good_aggs())
        assert isinstance(result, pd.DataFrame)

    def test_columns_present(self):
        result = _run(_good_aggs())
        assert set(_COLS).issubset(result.columns)

    def test_tail_applied(self):
        result = _run(_good_aggs(n=20), bars=3)
        assert len(result) <= 20

    def test_raises_on_empty_response(self):
        with pytest.raises(RuntimeError, match="no data"):
            _run([])

    def test_raises_on_unsupported_interval(self):
        with pytest.raises(ValueError, match="Unsupported interval"):
            _run(_good_aggs(), interval="3m")

    def test_hourly_interval_accepted(self):
        result = _run(_good_aggs(), interval="1h", bars=5)
        assert set(_COLS).issubset(result.columns)

    def test_api_key_passed_to_client(self):
        with patch("polygon.RESTClient") as MockClient:
            MockClient.return_value.get_aggs.return_value = _good_aggs()
            PolygonProvider(api_key="secret-key").fetch_ohlcv("AAPL", "1d", 5)
        MockClient.assert_called_once_with(api_key="secret-key")
