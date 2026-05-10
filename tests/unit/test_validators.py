"""Tests for OHLCV sanity validator."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from alphalink.adapter.validators import validate_ohlcv

_INTERVAL = "1h"


def _make_df(n: int = 5, interval: str = _INTERVAL, stale: bool = False) -> pd.DataFrame:
    """Build a valid OHLCV DataFrame with a recent DatetimeIndex."""
    now = datetime.now(timezone.utc)
    if stale:
        end = now - timedelta(hours=5)  # 5h > 2h max-age for 1h interval
    else:
        end = now - timedelta(minutes=30)
    idx = pd.date_range(end=end, periods=n, freq="1h", tz="UTC")
    return pd.DataFrame(
        {
            "Open":   [100.0] * n,
            "High":   [105.0] * n,
            "Low":    [95.0]  * n,
            "Close":  [102.0] * n,
            "Volume": [1000]  * n,
        },
        index=idx,
    )


class TestValidDF:
    def test_passes_clean_data(self):
        validate_ohlcv(_make_df(), _INTERVAL)  # no raise

    def test_passes_with_ticker_tag(self):
        validate_ohlcv(_make_df(), _INTERVAL, ticker="AAPL")


class TestEmptyAndMissingColumns:
    def test_raises_on_empty(self):
        with pytest.raises(ValueError, match="empty"):
            validate_ohlcv(pd.DataFrame(), _INTERVAL)

    def test_raises_missing_column(self):
        df = _make_df().drop(columns=["Volume"])
        with pytest.raises(ValueError, match="missing columns"):
            validate_ohlcv(df, _INTERVAL)


class TestNaNChecks:
    def test_raises_nan_close(self):
        df = _make_df()
        df.loc[df.index[2], "Close"] = float("nan")
        with pytest.raises(ValueError, match="NaN"):
            validate_ohlcv(df, _INTERVAL)

    def test_raises_nan_open(self):
        df = _make_df()
        df.loc[df.index[0], "Open"] = float("nan")
        with pytest.raises(ValueError, match="NaN"):
            validate_ohlcv(df, _INTERVAL)


class TestPriceChecks:
    def test_raises_zero_close(self):
        df = _make_df()
        df.loc[df.index[1], "Close"] = 0.0
        with pytest.raises(ValueError, match="non-positive"):
            validate_ohlcv(df, _INTERVAL)

    def test_raises_negative_open(self):
        df = _make_df()
        df.loc[df.index[0], "Open"] = -1.0
        with pytest.raises(ValueError, match="non-positive"):
            validate_ohlcv(df, _INTERVAL)

    def test_raises_negative_volume(self):
        df = _make_df()
        df.loc[df.index[0], "Volume"] = -1
        with pytest.raises(ValueError, match="negative Volume"):
            validate_ohlcv(df, _INTERVAL)

    def test_zero_volume_ok(self):
        df = _make_df()
        df.loc[df.index[0], "Volume"] = 0
        validate_ohlcv(df, _INTERVAL)  # no raise — zero volume valid (no-trade bar)


class TestHighLowChecks:
    def test_raises_high_less_than_low(self):
        df = _make_df()
        df.loc[df.index[0], "High"] = 80.0  # below Low=95
        with pytest.raises(ValueError, match="High < Low"):
            validate_ohlcv(df, _INTERVAL)

    def test_raises_negative_low(self):
        df = _make_df()
        df.loc[df.index[0], "Low"] = -5.0
        with pytest.raises(ValueError, match="non-positive"):
            validate_ohlcv(df, _INTERVAL)


class TestCloseConsistency:
    def test_raises_close_above_high(self):
        df = _make_df()
        df.loc[df.index[1], "Close"] = 200.0  # above High=105
        with pytest.raises(ValueError, match="Close outside"):
            validate_ohlcv(df, _INTERVAL)

    def test_raises_close_below_low(self):
        df = _make_df()
        df.loc[df.index[1], "Close"] = 50.0  # below Low=95
        with pytest.raises(ValueError, match="Close outside"):
            validate_ohlcv(df, _INTERVAL)

    def test_passes_close_at_high_boundary(self):
        df = _make_df()
        df.loc[df.index[0], "Close"] = 105.0  # exactly High — valid
        validate_ohlcv(df, _INTERVAL)  # no raise

    def test_passes_close_at_low_boundary(self):
        df = _make_df()
        df.loc[df.index[0], "Close"] = 95.0  # exactly Low — valid
        validate_ohlcv(df, _INTERVAL)  # no raise


class TestStalenessCheck:
    def test_raises_stale_data(self):
        df = _make_df(stale=True)
        with pytest.raises(ValueError, match="stale"):
            validate_ohlcv(df, _INTERVAL)

    def test_passes_fresh_data(self):
        df = _make_df(stale=False)
        validate_ohlcv(df, _INTERVAL)  # no raise

    def test_skips_staleness_for_non_datetime_index(self):
        df = _make_df()
        df.index = range(len(df))  # integer index
        validate_ohlcv(df, _INTERVAL)  # no raise — staleness skipped
