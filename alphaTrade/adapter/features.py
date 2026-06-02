"""TA-Lib indicator registry. Mirrors alphaGen src/att/features/registry.py.

HANDOVER §5b: deviation from this registry produces silent wrong predictions.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def compute_features(df: pd.DataFrame, feature_names: list[str]) -> pd.DataFrame:
    """Compute all features in feature_names order from raw OHLCV df.

    df must have columns: Open, High, Low, Close, Volume (case-sensitive).
    Returns df with only the requested feature columns (some rows will be NaN
    from indicator warmup — caller must dropna() after).
    """
    try:
        import talib
    except ImportError as exc:
        raise RuntimeError(
            "TA-Lib not installed. Install libta-lib-dev system package then: pip install ta-lib"
        ) from exc

    out = pd.DataFrame(index=df.index)
    close = df["Close"].values.astype(float)
    high = df["High"].values.astype(float)
    low = df["Low"].values.astype(float)
    volume = df["Volume"].values.astype(float)

    computed: dict[str, np.ndarray] = {}

    for name in feature_names:
        if name in computed:
            out[name] = computed[name]
            continue

        if name == "RSI":
            computed["RSI"] = talib.RSI(close, timeperiod=14)
        elif name in ("MACD", "MACD_signal", "MACD_hist"):
            m, s, h = talib.MACD(close, fastperiod=12, slowperiod=26, signalperiod=9)
            computed["MACD"] = m
            computed["MACD_signal"] = s
            computed["MACD_hist"] = h
        elif name in ("BBANDS_upper", "BBANDS_mid", "BBANDS_lower"):
            u, mid, lo = talib.BBANDS(close, timeperiod=20, nbdevup=2, nbdevdn=2)
            computed["BBANDS_upper"] = u
            computed["BBANDS_mid"] = mid
            computed["BBANDS_lower"] = lo
        elif name == "ATR":
            computed["ATR"] = talib.ATR(high, low, close, timeperiod=14)
        elif name.startswith("EMA_"):
            tp = int(name.split("_")[1])
            computed[name] = talib.EMA(close, timeperiod=tp)
        elif name.startswith("SMA_"):
            tp = int(name.split("_")[1])
            computed[name] = talib.SMA(close, timeperiod=tp)
        elif name == "ADX":
            computed["ADX"] = talib.ADX(high, low, close, timeperiod=14)
        elif name in ("STOCH_k", "STOCH_d"):
            k, d = talib.STOCH(high, low, close, fastk_period=5, slowk_period=3, slowd_period=3)
            computed["STOCH_k"] = k
            computed["STOCH_d"] = d
        elif name == "OBV":
            computed["OBV"] = talib.OBV(close, volume)
        elif name in ("Open", "High", "Low", "Close", "Volume"):
            computed[name] = df[name].values.astype(float)
        elif name == "VWAP":
            # Cumulative session VWAP from OHLCV — matches alphaGen src/att/data/fetch.py
            tp = (high + low + close) / 3.0
            cum_tpv = np.cumsum(tp * volume)
            cum_vol = np.cumsum(volume)
            computed["VWAP"] = np.where(cum_vol == 0, np.nan, cum_tpv / cum_vol)
        elif name == "Transactions":
            # Trade-count passthrough; may not be available from all providers
            if "Transactions" in df.columns:
                computed["Transactions"] = df["Transactions"].values.astype(float)
            else:
                computed["Transactions"] = np.full(len(df), np.nan)
        else:
            raise ValueError(
                f"Unknown feature: {name!r}. Add it to features.py registry."
            )

        out[name] = computed[name]

    return out
