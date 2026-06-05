"""Tests for make_jwt_dep — JWT auth for alphaTrade API (only auth mode).

Covers:
- Valid JWT accepted, user context attached to request.state
- Expired/tampered JWT → 401
- Denylisted jti → 401 even before token expires
- Redis unreachable (fail-closed) → 503
- Missing auth entirely → 401
"""
from __future__ import annotations

import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alphaTrade.api.auth import make_jwt_dep


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ec_keypair():
    priv = ec.generate_private_key(ec.SECP256R1())
    return priv, priv.public_key()


def _make_token(private_key, kid: str, sub: str = "user-123", role: str = "standard",
                tv: int = 0, ttl: int = 600) -> tuple[str, str]:
    now = int(time.time())
    jti = str(uuid.uuid4())
    payload = {"sub": sub, "role": role, "jti": jti, "tv": tv, "kid": kid,
               "iat": now, "exp": now + ttl}
    token = jwt.encode(payload, private_key, algorithm="ES256", headers={"kid": kid})
    return token, jti


def _mock_jwks(public_key, kid: str) -> dict:
    from jwt import PyJWK
    import base64
    pub = public_key.public_numbers()
    def _b64(n):
        return base64.urlsafe_b64encode(n.to_bytes(32, "big")).rstrip(b"=").decode()
    return {kid: PyJWK({"kty": "EC", "crv": "P-256", "kid": kid, "use": "sig",
                        "alg": "ES256", "x": _b64(pub.x), "y": _b64(pub.y)}).key}


def _make_settings(auth_mode: str = "jwt", redis_enabled: bool = False) -> MagicMock:
    s = MagicMock()
    s.auth_mode = auth_mode
    s.redis.enabled = redis_enabled
    s.redis.url = "redis://localhost:6379/0"
    return s


def _make_app(auth_mode: str = "jwt", redis_enabled: bool = False) -> tuple[FastAPI, MagicMock]:
    """Build a minimal FastAPI app with make_jwt_dep wired."""
    settings = _make_settings(auth_mode=auth_mode, redis_enabled=redis_enabled)
    engine = MagicMock()  # not used in jwt mode
    auth_dep = make_jwt_dep(engine, settings)

    app = FastAPI()

    @app.get("/protected")
    async def protected(auth=__import__("fastapi").Depends(auth_dep)):
        from fastapi import Request
        return {"ok": True}

    return app, settings


# ---------------------------------------------------------------------------
# Tests: JWT mode
# ---------------------------------------------------------------------------

def test_valid_jwt_accepted():
    priv, pub = _make_ec_keypair()
    kid = str(uuid.uuid4())
    token, jti = _make_token(priv, kid)
    mock_cache = _mock_jwks(pub, kid)
    app, _ = _make_app()

    with patch("alphaTrade.security.alphakey_auth._fetch_jwks", return_value=mock_cache):
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_expired_jwt_returns_401():
    priv, pub = _make_ec_keypair()
    kid = str(uuid.uuid4())
    token, _ = _make_token(priv, kid, ttl=-10)  # already expired
    mock_cache = _mock_jwks(pub, kid)
    app, _ = _make_app()

    with patch("alphaTrade.security.alphakey_auth._fetch_jwks", return_value=mock_cache):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_tampered_jwt_returns_401():
    priv, pub = _make_ec_keypair()
    kid = str(uuid.uuid4())
    token, _ = _make_token(priv, kid)
    parts = token.split(".")
    parts[2] = parts[2][:-4] + "XXXX"
    tampered = ".".join(parts)
    mock_cache = _mock_jwks(pub, kid)
    app, _ = _make_app()

    with patch("alphaTrade.security.alphakey_auth._fetch_jwks", return_value=mock_cache):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/protected", headers={"Authorization": f"Bearer {tampered}"})
    assert resp.status_code == 401


def test_denylisted_jti_returns_401():
    """Valid, non-expired token BUT jti is in Redis denylist → 401."""
    priv, pub = _make_ec_keypair()
    kid = str(uuid.uuid4())
    token, jti = _make_token(priv, kid)
    mock_cache = _mock_jwks(pub, kid)
    app, settings = _make_app(redis_enabled=True)

    with patch("alphaTrade.security.alphakey_auth._fetch_jwks", return_value=mock_cache), \
         patch("alphaTrade.security.alphakey_auth.is_revoked", new=AsyncMock(return_value=True)):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_redis_unreachable_fail_closed_returns_503():
    """Redis down on a trade write → fail-closed → 503."""
    priv, pub = _make_ec_keypair()
    kid = str(uuid.uuid4())
    token, _ = _make_token(priv, kid)
    mock_cache = _mock_jwks(pub, kid)
    app, settings = _make_app(redis_enabled=True)

    async def _raise(*a, **kw):
        raise ConnectionError("Redis unreachable")

    with patch("alphaTrade.security.alphakey_auth._fetch_jwks", return_value=mock_cache), \
         patch("alphaTrade.security.alphakey_auth.is_revoked", side_effect=_raise):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 503


def test_missing_auth_returns_401():
    app, _ = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/protected")
    assert resp.status_code == 401


def test_dual_auth_xapikey_accepted_in_jwt_mode():
    """JWT mode but X-Api-Key header provided and no active key → allowed (migration window)."""
    app, _ = _make_app(auth_mode="jwt", redis_enabled=False)

    # BotSettingsRepo.get() → None → active_key="" → legacy allows all requests
    with patch("alphaTrade.store.repos.BotSettingsRepo.get", return_value=None):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/protected", headers={"X-Api-Key": "anykey"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Tests: legacy mode unchanged
# ---------------------------------------------------------------------------

def test_legacy_mode_no_key_allows():
    app, _ = _make_app(auth_mode="legacy")

    with patch("alphaTrade.store.repos.BotSettingsRepo.get", return_value=None):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/protected")
    assert resp.status_code == 200
