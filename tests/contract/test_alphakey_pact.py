"""Consumer-driven Pact contract between alphaTrade (consumer) and alphaKey
(provider), covering JWKS, token-version, and secrets.

Generates tests/contract/pacts/alphaTrade-alphaKey.json — manually copied
into alphaKey's repo for provider verification.

Calls _fetch_jwks/_fetch_token_version/_fetch_secrets_from_api directly
rather than going through get_secrets()'s wrapper, since that wrapper has a
module-level 60s TTL cache keyed by user_id that would otherwise return
stale data across these test functions instead of hitting the mock server
each time.

Each test function builds its own `Pact` instance rather than sharing one
module-level instance: pact-python's mock server handle becomes unusable
for defining *new* interactions once a prior `pact.serve()` context manager
has exited (confirmed against the installed pact-python==3.4.0 — reusing a
single Pact object's interaction builder across multiple serve() calls
raises `RuntimeError: The interaction state could not be updated.` /
similar on the second and subsequent interactions). Each test instead writes
its own interaction(s) to the shared pact file with `overwrite=False`, which
pact-python merges/de-dupes by interaction description rather than
truncating the file.

Note: unlike alphaGen's equivalent contract, alphaTrade's
_fetch_secrets_from_api takes no provider/account parameters at all and
never sends query params — it always fetches all secrets for a user and
filters client-side afterward, to avoid partial-fetch cache poisoning. This
is intentional, confirmed against the actual alphaTrade source.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from pact import Pact, match

_SERVICE_TOKEN = "pact-test-service-token"
_PACTS_DIR = Path(__file__).parent / "pacts"


def _b64url(n: int) -> str:
    return base64.urlsafe_b64encode(n.to_bytes(32, "big")).rstrip(b"=").decode()


@pytest.fixture(autouse=True)
def _service_token_env():
    old = os.environ.get("ALPHAKEY_SERVICE_TOKEN")
    os.environ["ALPHAKEY_SERVICE_TOKEN"] = _SERVICE_TOKEN
    yield
    if old is None:
        os.environ.pop("ALPHAKEY_SERVICE_TOKEN", None)
    else:
        os.environ["ALPHAKEY_SERVICE_TOKEN"] = old


def test_jwks_fetch():
    from alphaTrade.security.alphakey_auth import _fetch_jwks

    pact = Pact("alphaTrade", "alphaKey")

    # Use real EC P-256 public key coordinates (32-byte x/y) so the
    # consumer's actual JWK-parsing code (jwt.PyJWK) succeeds, rather than
    # silently failing to parse a placeholder key and leaving the cache
    # empty (which it does — see _fetch_jwks's per-key try/except).
    public_key = ec.generate_private_key(ec.SECP256R1()).public_key()
    nums = public_key.public_numbers()

    (
        pact.upon_receiving("a request for the JWKS")
        .with_request("GET", "/auth/.well-known/jwks.json")
        .will_respond_with(200)
        .with_header("Content-Type", "application/json")
        .with_body({"keys": match.each_like({
            "kty": "EC", "crv": "P-256", "kid": match.like("kid-1"), "use": "sig",
            "alg": "ES256", "x": _b64url(nums.x), "y": _b64url(nums.y),
        })})
    )

    with pact.serve() as srv:
        os.environ["ALPHAKEY_URL"] = str(srv.url)
        jwks = _fetch_jwks(force=True)
        assert jwks, "expected at least one parsed signing key in JWKS cache"

    pact.write_file(_PACTS_DIR, overwrite=False)


def test_token_version_found():
    from alphaTrade.security.alphakey_auth import _fetch_token_version

    pact = Pact("alphaTrade", "alphaKey")

    (
        pact.upon_receiving("a token-version request for an existing user")
        .given("a user exists with a known token version", {"userId": "33333333-3333-3333-3333-333333333333", "tokenVersion": 1})
        .with_request("GET", "/auth/internal/token-version/33333333-3333-3333-3333-333333333333")
        .with_header("X-Service-Token", _SERVICE_TOKEN)
        .will_respond_with(200)
        .with_header("Content-Type", "application/json")
        .with_body({"user_id": "33333333-3333-3333-3333-333333333333", "token_version": 1})
    )

    with pact.serve() as srv:
        os.environ["ALPHAKEY_URL"] = str(srv.url)
        result = _fetch_token_version("33333333-3333-3333-3333-333333333333")
        assert result == 1

    pact.write_file(_PACTS_DIR, overwrite=False)


def test_token_version_not_found():
    from alphaTrade.security.alphakey_auth import _fetch_token_version

    pact = Pact("alphaTrade", "alphaKey")

    (
        pact.upon_receiving("a token-version request for a missing user")
        .given("no such user exists for token-version lookup")
        .with_request("GET", "/auth/internal/token-version/00000000-0000-0000-0000-000000000000")
        .with_header("X-Service-Token", _SERVICE_TOKEN)
        .will_respond_with(404)
        .with_header("Content-Type", "application/json")
        .with_body({"detail": match.like("User not found")})
    )

    with pact.serve() as srv:
        os.environ["ALPHAKEY_URL"] = str(srv.url)
        result = _fetch_token_version("00000000-0000-0000-0000-000000000000")
        assert result is None

    pact.write_file(_PACTS_DIR, overwrite=False)


def test_secrets_fetch_all_no_filter():
    from alphaTrade.broker.alphakey_client import _fetch_secrets_from_api

    pact = Pact("alphaTrade", "alphaKey")

    (
        pact.upon_receiving("a secrets request with no provider/account filter")
        .given("a user has stored secrets", {"userId": "44444444-4444-4444-4444-444444444444", "provider": "t212", "account": "demo", "name": "api_key", "value": "secret-value-456"})
        .with_request("GET", "/auth/internal/secrets/44444444-4444-4444-4444-444444444444")
        .with_header("X-Service-Token", _SERVICE_TOKEN)
        .will_respond_with(200)
        .with_header("Content-Type", "application/json")
        .with_body({
            "user_id": "44444444-4444-4444-4444-444444444444",
            "secrets": match.each_like({"provider": "t212", "account": "demo", "name": "api_key", "value": match.like("secret-value-456")}),
        })
    )

    with pact.serve() as srv:
        os.environ["ALPHAKEY_URL"] = str(srv.url)
        secrets = _fetch_secrets_from_api("44444444-4444-4444-4444-444444444444")
        assert ("t212", "demo", "api_key") in secrets

    pact.write_file(_PACTS_DIR, overwrite=False)


def test_secrets_user_not_found():
    from alphaTrade.broker.alphakey_client import AlphaKeyError, _fetch_secrets_from_api

    pact = Pact("alphaTrade", "alphaKey")

    (
        pact.upon_receiving("a secrets request for a missing user")
        .given("no such user exists for secrets lookup")
        .with_request("GET", "/auth/internal/secrets/00000000-0000-0000-0000-000000000000")
        .with_header("X-Service-Token", _SERVICE_TOKEN)
        .will_respond_with(404)
        .with_header("Content-Type", "application/json")
        .with_body({"detail": match.like("User not found")})
    )

    with pact.serve() as srv:
        os.environ["ALPHAKEY_URL"] = str(srv.url)
        with pytest.raises(AlphaKeyError):
            _fetch_secrets_from_api("00000000-0000-0000-0000-000000000000")

    pact.write_file(_PACTS_DIR, overwrite=False)
