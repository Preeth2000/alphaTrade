"""Operator kill switch: ALPHALINK_HALT env var or ./HALT sentinel file."""
from __future__ import annotations

import os
from pathlib import Path

ENV_VAR = "ALPHALINK_HALT"
SENTINEL_FILE = "HALT"

_TRUTHY = {"1", "true", "yes", "on"}


def is_halted() -> bool:
    """Return True if operator has engaged the kill switch."""
    val = os.environ.get(ENV_VAR, "").strip().lower()
    if val in _TRUTHY:
        return True
    return Path(SENTINEL_FILE).exists()
