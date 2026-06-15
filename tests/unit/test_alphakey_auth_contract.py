"""Contract tests for the vendored alphakey_auth.py snippet.

These tests use a locally-generated EC keypair to simulate alphaKey signing,
bypassing the need for a live alphaKey service. They verify the snippet correctly
validates real alphaKey-format JWTs and handles all failure modes.

If these tests fail after updating alphakey_auth.py, the snippet has drifted
from the alphaKey token format. Sync with alphaKey/alphakey/security/tokens.py.
"""
from __future__ import annotations

import json
import time
import uuid
from unittest.mock import patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

from alphaTrade.security.alphakey_auth import AuthError, Claims, verify_token, _fetch_jwks


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ec_keypair():
    private_key = ec.generate_private_key(ec.SECP256R1())
    return private_key, private_key.public_key()


@pytest.fixture(scope="module")
def kid():
    return str(uuid.uuid4())


def _make_token(
    private_key,
    kid: str,
    sub: str = "user-abc",
    role: str = "standard",
    tv: int = 0,
    ttl: int = 600,
    iss: str = "alphakey",
    aud: str = "alphakey",
) -> tuple[str, str]:
    """Issue a test JWT with the same claim shape as alphaKey."""
    now = int(time.time())
    jti = str(uuid.uuid4())
    payload = {
        "sub": sub,
        "role": role,
        "jti": jti,
        "tv": tv,
        "kid": kid,
        "iss": iss,
        "aud": aud,
        "iat": now,
        "exp": now + ttl,
    }
    token = jwt.encode(payload, private_key, algorithm="ES256", headers={"kid": kid})
    return token, jti


def _mock_jwks(public_key, kid: str):
    """Return a mock JWKS cache for patching _fetch_jwks."""
    from jwt import PyJWK

    pub_numbers = public_key.public_numbers()

    def _b64url(n: int) -> str:
        import base64
        return base64.urlsafe_b64encode(n.to_bytes(32, "big")).rstrip(b"=").decode()

    jwk_dict = {
        "kty": "EC",
        "crv": "P-256",
        "kid": kid,
        "use": "sig",
        "alg": "ES256",
        "x": _b64url(pub_numbers.x),
        "y": _b64url(pub_numbers.y),
    }
    return {kid: PyJWK(jwk_dict).key}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_valid_token_returns_claims(ec_keypair, kid):
    private_key, public_key = ec_keypair
    token, jti = _make_token(private_key, kid, sub="user-123", role="developer", tv=2)

    mock_cache = _mock_jwks(public_key, kid)
    with patch("alphaTrade.security.alphakey_auth._fetch_jwks", return_value=mock_cache):
        claims = verify_token(token)

    assert claims.sub == "user-123"
    assert claims.role == "developer"
    assert claims.jti == jti
    assert claims.tv == 2
    assert claims.kid == kid
    assert claims.exp > int(time.time())


def test_standard_role_claims(ec_keypair, kid):
    private_key, public_key = ec_keypair
    token, _ = _make_token(private_key, kid, role="standard")
    mock_cache = _mock_jwks(public_key, kid)
    with patch("alphaTrade.security.alphakey_auth._fetch_jwks", return_value=mock_cache):
        claims = verify_token(token)
    assert claims.role == "standard"


def test_expired_token_raises_auth_error(ec_keypair, kid):
    private_key, public_key = ec_keypair
    token, _ = _make_token(private_key, kid, ttl=-10)  # already expired
    mock_cache = _mock_jwks(public_key, kid)
    with patch("alphaTrade.security.alphakey_auth._fetch_jwks", return_value=mock_cache):
        with pytest.raises(AuthError, match="expired"):
            verify_token(token)


def test_tampered_token_raises_auth_error(ec_keypair, kid):
    private_key, public_key = ec_keypair
    token, _ = _make_token(private_key, kid)
    # Corrupt the signature
    parts = token.split(".")
    parts[2] = parts[2][:-4] + "XXXX"
    tampered = ".".join(parts)
    mock_cache = _mock_jwks(public_key, kid)
    with patch("alphaTrade.security.alphakey_auth._fetch_jwks", return_value=mock_cache):
        with pytest.raises(AuthError):
            verify_token(tampered)


def test_wrong_key_raises_auth_error(ec_keypair, kid):
    private_key, _ = ec_keypair
    other_private = ec.generate_private_key(ec.SECP256R1())
    other_public = other_private.public_key()
    token, _ = _make_token(private_key, kid)  # signed with private_key
    mock_cache = _mock_jwks(other_public, kid)  # but public key is different
    with patch("alphaTrade.security.alphakey_auth._fetch_jwks", return_value=mock_cache):
        with pytest.raises(AuthError):
            verify_token(token)


def test_unknown_kid_triggers_jwks_refresh(ec_keypair):
    private_key, public_key = ec_keypair
    kid_a = "known-kid"
    kid_b = "unknown-kid"
    token, _ = _make_token(private_key, kid_b)

    # First call returns cache without kid_b; second (forced refresh) includes it
    cache_without = _mock_jwks(public_key, kid_a)
    cache_with = _mock_jwks(public_key, kid_b)

    call_count = {"n": 0}

    def mock_fetch(force=False):
        call_count["n"] += 1
        return cache_with if force else cache_without

    with patch("alphaTrade.security.alphakey_auth._fetch_jwks", side_effect=mock_fetch):
        claims = verify_token(token)

    assert claims.kid == kid_b
    assert call_count["n"] == 2  # first call (no force) + refresh (force=True)


def test_unknown_kid_after_refresh_raises(ec_keypair):
    private_key, _ = ec_keypair
    token, _ = _make_token(private_key, "ghost-kid")
    with patch("alphaTrade.security.alphakey_auth._fetch_jwks", return_value={}):
        with pytest.raises(AuthError, match="Unknown signing key"):
            verify_token(token)


def test_wrong_issuer_raises_auth_error(ec_keypair, kid):
    private_key, public_key = ec_keypair
    token, _ = _make_token(private_key, kid, iss="rogue-issuer")
    mock_cache = _mock_jwks(public_key, kid)
    with patch("alphaTrade.security.alphakey_auth._fetch_jwks", return_value=mock_cache):
        with pytest.raises(AuthError):
            verify_token(token)


def test_wrong_audience_raises_auth_error(ec_keypair, kid):
    private_key, public_key = ec_keypair
    token, _ = _make_token(private_key, kid, aud="other-service")
    mock_cache = _mock_jwks(public_key, kid)
    with patch("alphaTrade.security.alphakey_auth._fetch_jwks", return_value=mock_cache):
        with pytest.raises(AuthError):
            verify_token(token)


def test_missing_sub_raises_auth_error(ec_keypair, kid):
    private_key, public_key = ec_keypair
    now = int(time.time())
    payload = {"role": "standard", "jti": str(uuid.uuid4()), "tv": 0,
               "iss": "alphakey", "aud": "alphakey", "iat": now, "exp": now + 600}
    token = jwt.encode(payload, private_key, algorithm="ES256", headers={"kid": kid})
    mock_cache = _mock_jwks(public_key, kid)
    with patch("alphaTrade.security.alphakey_auth._fetch_jwks", return_value=mock_cache):
        with pytest.raises(AuthError, match="missing required claim"):
            verify_token(token)


# ---------------------------------------------------------------------------
# Token-version (tv) backstop tests
# ---------------------------------------------------------------------------

def test_stale_tv_raises_auth_error(ec_keypair, kid):
    """Token with tv=1 is rejected when current tv=3."""
    private_key, public_key = ec_keypair
    token, _ = _make_token(private_key, kid, tv=1)
    mock_cache = _mock_jwks(public_key, kid)
    with patch("alphaTrade.security.alphakey_auth._fetch_jwks", return_value=mock_cache):
        with patch("alphaTrade.security.alphakey_auth._fetch_token_version", return_value=3):
            with pytest.raises(AuthError, match="revoked"):
                verify_token(token)


def test_matching_tv_passes(ec_keypair, kid):
    """Token with tv=2 is accepted when current tv=2."""
    private_key, public_key = ec_keypair
    token, _ = _make_token(private_key, kid, tv=2)
    mock_cache = _mock_jwks(public_key, kid)
    with patch("alphaTrade.security.alphakey_auth._fetch_jwks", return_value=mock_cache):
        with patch("alphaTrade.security.alphakey_auth._fetch_token_version", return_value=2):
            claims = verify_token(token)
    assert claims.tv == 2


def test_unreachable_alphakey_tv_check_fails_open(ec_keypair, kid):
    """When alphaKey is unreachable (_fetch_token_version returns None), token still passes."""
    private_key, public_key = ec_keypair
    token, _ = _make_token(private_key, kid, tv=1)
    mock_cache = _mock_jwks(public_key, kid)
    with patch("alphaTrade.security.alphakey_auth._fetch_jwks", return_value=mock_cache):
        with patch("alphaTrade.security.alphakey_auth._fetch_token_version", return_value=None):
            claims = verify_token(token)
    assert claims.tv == 1
