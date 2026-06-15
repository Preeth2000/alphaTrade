"""alphaKey JWT verification — vendored snippet.

Provides offline JWT signature verification + Redis denylist check for instant logout.
This file is byte-identical in alphaTrade and alphaGen. Do not diverge them.

Usage:
    from alphaTrade.security.alphakey_auth import verify_token, is_revoked, AuthError, Claims

    try:
        claims = verify_token(token, fail_closed=True)
    except AuthError as e:
        # reject request

    if await is_revoked(claims.jti, redis_url, fail_closed=True):
        # reject request
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class AuthError(Exception):
    """Raised when a token is invalid, expired, or revoked."""


@dataclass
class Claims:
    sub: str          # user_id
    role: str         # "developer" | "standard"
    jti: str          # JWT ID — used for denylist lookup
    tv: int           # token_version — offline revocation backstop
    kid: str          # signing key id
    exp: int          # unix timestamp
    iat: int          # unix timestamp


# ---------------------------------------------------------------------------
# JWKS cache
# ---------------------------------------------------------------------------

_jwks_cache: dict[str, object] = {}   # {kid: public_key_object}
_jwks_fetched_at: float = 0.0
_JWKS_TTL = 600.0  # seconds — refresh cache every 10 minutes

_TV_CACHE: dict[str, tuple[int, float]] = {}   # {user_id: (token_version, fetched_at)}
_TV_CACHE_MAX = 1000  # max entries; oldest evicted on overflow
_TV_TTL = 60.0  # seconds — revoked tokens may be accepted for up to 60s after tv bump


def _tv_cache_put(user_id: str, tv: int, now: float) -> None:
    """Insert into _TV_CACHE, evicting oldest entry if at capacity."""
    if len(_TV_CACHE) >= _TV_CACHE_MAX and user_id not in _TV_CACHE:
        # Remove the oldest entry (smallest fetched_at)
        oldest = min(_TV_CACHE, key=lambda k: _TV_CACHE[k][1])
        del _TV_CACHE[oldest]
    _TV_CACHE[user_id] = (tv, now)


def _alphakey_url() -> str:
    url = os.environ.get("ALPHAKEY_URL", "http://alphakey-api:8000")
    return url.rstrip("/")


def _alphakey_service_token() -> str:
    return os.environ.get("ALPHAKEY_SERVICE_TOKEN", "")


def _fetch_token_version(user_id: str) -> int | None:
    """Fetch current token_version for user_id from alphaKey (with TTL cache).

    Returns None when alphaKey is unreachable (caller should fail-open).
    Returns the token_version int on success.
    """
    now = time.monotonic()
    cached = _TV_CACHE.get(user_id)
    if cached and (now - cached[1]) < _TV_TTL:
        return cached[0]

    token = _alphakey_service_token()
    if not token:
        logger.warning("alphakey_auth: ALPHAKEY_SERVICE_TOKEN not set — skipping tv check")
        return None

    try:
        import httpx
        url = f"{_alphakey_url()}/auth/internal/token-version/{user_id}"
        resp = httpx.get(url, headers={"X-Service-Token": token}, timeout=3.0)
        if resp.status_code == 404:
            return None  # Unknown user — let other checks handle it
        resp.raise_for_status()
        tv = resp.json()["token_version"]
        _tv_cache_put(user_id, tv, now)
        return tv
    except Exception as exc:
        logger.warning("alphakey_auth: could not fetch token_version for %s: %s", user_id, exc)
        return None


def _fetch_jwks(force: bool = False) -> dict[str, object]:
    """Fetch JWKS from alphaKey and update the module-level cache.

    Returns the updated {kid: public_key} dict.
    Raises AuthError if the fetch fails.
    """
    global _jwks_cache, _jwks_fetched_at

    now = time.monotonic()
    if not force and (now - _jwks_fetched_at) < _JWKS_TTL and _jwks_cache:
        return _jwks_cache

    try:
        import httpx
        url = f"{_alphakey_url()}/auth/.well-known/jwks.json"
        resp = httpx.get(url, timeout=5.0)
        resp.raise_for_status()
        jwks = resp.json()
    except Exception as exc:
        raise AuthError(f"Failed to fetch JWKS from alphaKey: {exc}") from exc

    try:
        import jwt as _jwt
        new_cache: dict[str, object] = {}
        for jwk_dict in jwks.get("keys", []):
            kid = jwk_dict.get("kid")
            if kid:
                try:
                    new_cache[kid] = _jwt.PyJWK(jwk_dict).key
                except Exception as key_exc:
                    logger.warning("Failed to parse JWK kid=%s: %s", kid, key_exc)
        _jwks_cache = new_cache
        _jwks_fetched_at = now
        return _jwks_cache
    except Exception as exc:
        raise AuthError(f"Failed to parse JWKS: {exc}") from exc


def _get_public_key(kid: str) -> object:
    """Return the public key for the given kid, refreshing JWKS if unknown."""
    cache = _fetch_jwks()
    if kid in cache:
        return cache[kid]
    # Unknown kid — force refresh (key may have been rotated)
    cache = _fetch_jwks(force=True)
    if kid not in cache:
        raise AuthError(f"Unknown signing key kid={kid!r}")
    return cache[kid]


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------

def verify_token(token: str) -> Claims:
    """Verify an alphaKey access JWT offline.

    Steps:
    1. Decode header (no verify) to get kid
    2. Fetch public key from JWKS cache (refresh on unknown kid)
    3. Verify ES256 signature + expiry
    4. Return Claims

    Raises AuthError on any failure (invalid, expired, unknown kid, JWKS unreachable).
    """
    import jwt as _jwt

    # Decode without verification to extract kid
    try:
        unverified = _jwt.decode(token, options={"verify_signature": False})
        # kid is in the header, not payload — get it from the header
        header = _jwt.get_unverified_header(token)
        kid = header.get("kid") or unverified.get("kid", "")
    except Exception as exc:
        raise AuthError(f"Malformed token: {exc}") from exc

    if not kid:
        raise AuthError("Token missing kid header")

    public_key = _get_public_key(kid)

    expected_issuer = os.environ.get("JWT_ISSUER", "alphakey")
    expected_audience = os.environ.get("JWT_AUDIENCE", "alphakey")

    try:
        payload = _jwt.decode(
            token,
            public_key,
            algorithms=["ES256"],
            options={"verify_exp": True},
            issuer=expected_issuer,
            audience=expected_audience,
        )
    except _jwt.ExpiredSignatureError as exc:
        raise AuthError("Token has expired") from exc
    except _jwt.InvalidTokenError as exc:
        raise AuthError(f"Token invalid: {exc}") from exc

    try:
        claims = Claims(
            sub=payload["sub"],
            role=payload.get("role", "standard"),
            jti=payload["jti"],
            tv=payload.get("tv", 0),
            kid=kid,
            exp=payload["exp"],
            iat=payload.get("iat", 0),
        )
    except KeyError as exc:
        raise AuthError(f"Token missing required claim: {exc}") from exc

    # token_version (tv) offline-revocation backstop
    current_tv = _fetch_token_version(claims.sub)
    if current_tv is not None and claims.tv < current_tv:
        raise AuthError("Token revoked (token_version mismatch)")

    return claims


# ---------------------------------------------------------------------------
# Redis denylist check
# ---------------------------------------------------------------------------

_DENYLIST_PREFIX = "alphakey:denylist:"


async def is_revoked(
    jti: str,
    redis_url: str,
    fail_closed: bool = True,
) -> bool:
    """Check if a JWT's jti is in the alphaKey Redis denylist.

    fail_closed=True  → return True (deny) when Redis is unreachable.
                         Use for trade-write paths (financial safety).
    fail_closed=False → return False (allow) when Redis is unreachable.
                         Use for read-only paths.
    """
    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url(redis_url, decode_responses=True)
        result = await client.exists(f"{_DENYLIST_PREFIX}{jti}")
        await client.aclose()
        return bool(result)
    except Exception as exc:
        if fail_closed:
            logger.error(
                "alphakey_auth: Redis unreachable (fail-closed) jti=%s: %s", jti, exc
            )
            return True
        else:
            logger.warning(
                "alphakey_auth: Redis unreachable (fail-open) jti=%s: %s", jti, exc
            )
            return False


def is_revoked_sync(
    jti: str,
    redis_url: str,
    fail_closed: bool = True,
) -> bool:
    """Synchronous variant for non-async contexts (CLI, tests)."""
    try:
        import redis as sync_redis
        client = sync_redis.from_url(redis_url, decode_responses=True)
        result = client.exists(f"{_DENYLIST_PREFIX}{jti}")
        client.close()
        return bool(result)
    except Exception as exc:
        if fail_closed:
            logger.error(
                "alphakey_auth: Redis unreachable (fail-closed) jti=%s: %s", jti, exc
            )
            return True
        else:
            logger.warning(
                "alphakey_auth: Redis unreachable (fail-open) jti=%s: %s", jti, exc
            )
            return False
