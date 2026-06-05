"""alphaKey secrets client for alphaTrade.

Fetches per-user encrypted secrets from alphaKey's internal API at runtime.
Replaces direct BotSettings DB column reads when SECRETS_SOURCE=alphakey.

Design mirrors t212_client.py: httpx, tenacity retry, TTL cache.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

log = logging.getLogger(__name__)

_CACHE_TTL = 60.0  # seconds


class AlphaKeyError(Exception):
    """Raised when alphaKey is unreachable or returns an error."""


@dataclass
class _CacheEntry:
    secrets: dict[tuple[str, str, str], str]  # {(provider, account, name): value}
    fetched_at: float = field(default_factory=time.monotonic)

    def is_fresh(self) -> bool:
        return (time.monotonic() - self.fetched_at) < _CACHE_TTL


_cache: dict[str, _CacheEntry] = {}  # {user_id: CacheEntry}


def _alphakey_url() -> str:
    return os.environ.get("ALPHAKEY_URL", "http://alphakey-api:8000").rstrip("/")


def _service_token() -> str:
    return os.environ.get("ALPHAKEY_SERVICE_TOKEN", "")


@retry(
    retry=retry_if_exception_type(httpx.HTTPError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
    reraise=True,
)
def _fetch_secrets_from_api(
    user_id: str,
    provider: Optional[str] = None,
    account: Optional[str] = None,
) -> dict[tuple[str, str, str], str]:
    """Fetch secrets from alphaKey /auth/internal/secrets/{user_id}."""
    token = _service_token()
    if not token:
        raise AlphaKeyError("ALPHAKEY_SERVICE_TOKEN not set — cannot fetch secrets")

    url = f"{_alphakey_url()}/auth/internal/secrets/{user_id}"
    params: dict[str, str] = {}
    if provider:
        params["provider"] = provider
    if account:
        params["account"] = account

    try:
        resp = httpx.get(
            url,
            params=params,
            headers={"X-Service-Token": token},
            timeout=5.0,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise AlphaKeyError(
            f"alphaKey returned {exc.response.status_code} for user {user_id}"
        ) from exc

    data = resp.json()
    result: dict[tuple[str, str, str], str] = {}
    for entry in data.get("secrets", []):
        key = (entry["provider"], entry["account"], entry["name"])
        result[key] = entry["value"]
    return result


def get_secrets(
    user_id: str,
    provider: Optional[str] = None,
    account: Optional[str] = None,
) -> dict[tuple[str, str, str], str]:
    """Return secrets for user_id, using a 60s TTL cache.

    Keys are (provider, account, name) tuples.
    Raises AlphaKeyError on failure.
    """
    cached = _cache.get(user_id)
    if cached and cached.is_fresh():
        # Filter if provider/account requested
        if provider or account:
            return {
                k: v for k, v in cached.secrets.items()
                if (not provider or k[0] == provider)
                and (not account or k[1] == account)
            }
        return cached.secrets

    secrets = _fetch_secrets_from_api(user_id, provider=provider, account=account)
    _cache[user_id] = _CacheEntry(secrets=secrets)
    return secrets


def get_secret(
    user_id: str,
    provider: str,
    account: str,
    name: str,
    default: str = "",
) -> str:
    """Convenience: get a single secret value or return default."""
    try:
        secrets = get_secrets(user_id, provider=provider, account=account)
        return secrets.get((provider, account, name), default)
    except AlphaKeyError as exc:
        log.warning("alphaKey secret fetch failed (%s) — using default for %s/%s/%s",
                    exc, provider, account, name)
        return default
