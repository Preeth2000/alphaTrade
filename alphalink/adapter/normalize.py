"""Normalization. Mirrors alphaGen training normalization exactly. HANDOVER §5f."""
from __future__ import annotations

import numpy as np
import pandas as pd

from alphalink.adapter.manifest import Manifest, NormStats


def normalize(df: pd.DataFrame, manifest: Manifest) -> pd.DataFrame:
    """Apply per-column normalization using manifest.norm_stats.

    Must be called after dropna() so no NaN rows remain.
    """
    mode = manifest.normalize
    if mode == "none":
        return df.copy()

    stats: NormStats | dict = manifest.norm_stats
    if isinstance(stats, dict):
        mean = stats.get("mean", {})
        std = stats.get("std", {})
        mn = stats.get("min", {})
        mx = stats.get("max", {})
    else:
        mean = stats.mean
        std = stats.std
        mn = stats.min
        mx = stats.max

    out = df.copy()
    for col in df.columns:
        x = df[col].values.astype(np.float32)
        if mode == "zscore":
            m = mean.get(col, 0.0)
            s = std.get(col, 1.0) or 1.0
            out[col] = (x - m) / s
        elif mode == "minmax":
            lo = mn.get(col, 0.0)
            hi = mx.get(col, 1.0)
            out[col] = (x - lo) / ((hi - lo) or 1.0)
        else:
            raise ValueError(f"Unknown normalize mode: {mode!r}")
    return out
