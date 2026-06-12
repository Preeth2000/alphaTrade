from __future__ import annotations
import os
from fastapi import Header, HTTPException, Request

import alphaTrade.security.alphakey_auth as _alphakey_auth


def make_api_key_dep():
    def require_api_key(x_api_key: str = Header(default="")) -> None:
        active_key = os.environ.get("alphaTrade_API_KEY", "")
        if not active_key:
            return
        if x_api_key != active_key:
            raise HTTPException(status_code=403, detail="Invalid API key")
    return require_api_key


def _api_key_allows(x_api_key: str) -> bool:
    """Return True if the request should be allowed under API-key rules.

    Allows unconditionally when no key is configured (migration window).
    """
    active_key = os.environ.get("alphaTrade_API_KEY", "")
    if not active_key:
        return True
    return x_api_key == active_key


def make_jwt_dep(settings):
    """Return a FastAPI dependency that enforces JWT or legacy API-key auth.

    JWT mode:
      - Bearer token → verify ES256 signature + optional Redis denylist check.
      - X-Api-Key fallback during migration window (allows when no key configured).
      - No credentials → 401.

    Legacy mode:
      - Delegates to API-key check; allows when no key configured.

    On valid JWT, attaches user_id and role to request.state.
    """
    async def _dep(
        request: Request,
        authorization: str = Header(default=""),
        x_api_key: str = Header(default=""),
    ) -> None:
        if getattr(settings, "auth_mode", "legacy") == "legacy":
            if not _api_key_allows(x_api_key):
                raise HTTPException(status_code=403, detail="Invalid API key")
            return

        # JWT mode
        if authorization.startswith("Bearer "):
            token = authorization[7:]
            try:
                claims = _alphakey_auth.verify_token(token)
            except _alphakey_auth.AuthError as exc:
                raise HTTPException(status_code=401, detail=str(exc))

            if getattr(settings.redis, "enabled", False):
                try:
                    revoked = await _alphakey_auth.is_revoked(claims.jti, settings.redis.url)
                except Exception:
                    raise HTTPException(status_code=503, detail="Auth service unavailable")
                if revoked:
                    raise HTTPException(status_code=401, detail="Token revoked")

            request.state.user_id = claims.sub
            request.state.role = claims.role
            return

        # X-Api-Key migration window fallback
        if x_api_key:
            if not _api_key_allows(x_api_key):
                raise HTTPException(status_code=403, detail="Invalid API key")
            return

        raise HTTPException(status_code=401, detail="Authentication required")

    return _dep
