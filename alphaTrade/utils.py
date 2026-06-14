"""Shared utilities."""
from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return current UTC time as a naive datetime (replaces deprecated datetime.utcnow())."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
