"""Window assembly and tensor construction. HANDOVER §5g."""
from __future__ import annotations

import numpy as np

from alphaTrade.adapter.manifest import Manifest


def build_input(feature_df, manifest: Manifest) -> np.ndarray:
    """Take last manifest.window rows, return shaped float32 tensor (1, *input_shape)."""
    window = manifest.window
    if len(feature_df) < window:
        raise ValueError(
            f"Need at least {window} rows after dropna, got {len(feature_df)}"
        )

    arr = feature_df.values[-window:].astype(np.float32)  # (window, n_features)

    if manifest.model_arch == "mlp":
        x = arr.flatten()[np.newaxis, :]  # (1, window * n_features)
    else:
        x = arr[np.newaxis, :, :]  # (1, window, n_features)

    expected = tuple(manifest.input_shape)
    actual = x.shape[1:]
    if actual != expected:
        raise ValueError(
            f"Input shape mismatch: expected {expected}, got {actual}"
        )

    return x
