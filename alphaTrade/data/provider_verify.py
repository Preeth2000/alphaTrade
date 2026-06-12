"""Shared helper for verifying data-provider credentials.

This module exposes the same lightweight endpoint checks used by the
account-page 'Connected' badge so that health probes never trigger heavy
aggregate fetches on free/limited Polygon tiers.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from alphaTrade.config import Settings

_POLYGON_PROBE_URL = "https://api.polygon.io/v3/reference/tickers?ticker=AAPL&limit=1"
_TIMEOUT = 10.0


def verify_provider_credentials(settings: "Settings") -> tuple[bool, dict[str, Any]]:
    """Verify provider credentials using the appropriate cheap check.

    Uses the same lightweight endpoint as the account-page 'Connected' check.
    For polygon: hits reference/tickers (1 row) — never aggregates.
    For yfinance: downloads 5d AAPL bars.
    """
    provider = settings.data_provider

    if provider == "polygon":
        return _verify_polygon_key(settings.polygon_api_key)
    elif provider == "yfinance":
        return _verify_yfinance()
    else:
        return False, {"valid": False, "error": f"unknown provider {provider!r}"}


def _verify_polygon_key(api_key: str) -> tuple[bool, dict[str, Any]]:
    if not api_key:
        return False, {"valid": False, "error": "polygon_api_key is not configured"}
    try:
        r = httpx.get(
            _POLYGON_PROBE_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=_TIMEOUT,
        )
        if r.status_code == 200:
            return True, {"valid": True}
        return False, {"valid": False, "error": f"{r.status_code} {r.reason_phrase}"}
    except httpx.HTTPError as exc:
        return False, {"valid": False, "error": str(exc)}


def _verify_yfinance() -> tuple[bool, dict[str, Any]]:
    try:
        import yfinance as yf
        df = yf.download("AAPL", period="5d", interval="1d", progress=False, auto_adjust=True)
        if df.empty:
            return False, {"valid": False, "error": "no data returned"}
        return True, {"valid": True}
    except Exception as exc:
        return False, {"valid": False, "error": str(exc)}
