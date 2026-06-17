"""Pact provider verification: alphaTrade against alphaLink's contract.

Starts a real uvicorn server (Pact's verifier makes real HTTP calls), with
PACT_VERIFICATION_MODE=true so the gated /internal/pact-state endpoint is
mounted, AUTH_MODE=jwt so JWT verification is exercised, MlflowClient
patched to the shared fake registry (tests.support.fake_mlflow), and
alphaTrade's own _fetch_jwks patched with a local EC keypair — the same
trick alphaTrade's own unit tests (tests/unit/test_alphakey_auth_contract.py)
already use to verify real JWT signatures without a live alphaKey.

Before running the verifier, the committed pact file's literal
"test-token-placeholder" Authorization header value is rewritten in-memory
to a real token signed with that same local keypair, since the consumer
side (alphaLink, TypeScript) has no way to mint a Python-verifiable token
itself — token generation happens entirely here, on the verifying side.
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import httpx
import jwt
import pytest
import uvicorn
from cryptography.hazmat.primitives.asymmetric import ec
from pact.verifier import Verifier
from sqlmodel import create_engine

pytestmark = pytest.mark.integration

_PACT_FILE = Path(__file__).parents[1] / "pacts" / "alphaLink-alphaTrade.json"


def _make_test_jwt() -> tuple[str, dict[str, object]]:
    """Generate a local EC keypair, a JWKS cache for it, and a real token.

    Returns (token, jwks_cache) where jwks_cache is the dict shape
    alphaTrade.security.alphakey_auth._fetch_jwks normally returns
    (kid -> PyJWK), per the pattern in
    tests/unit/test_alphakey_auth_contract.py.
    """
    from jwt import PyJWK

    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    kid = str(uuid.uuid4())

    now = int(time.time())
    payload = {
        "sub": "pact-test-user",
        "role": "standard",
        "jti": str(uuid.uuid4()),
        "tv": 0,
        "kid": kid,
        "iss": "alphakey",
        "aud": "alphakey",
        "iat": now,
        "exp": now + 600,
    }
    token = jwt.encode(payload, private_key, algorithm="ES256", headers={"kid": kid})

    nums = public_key.public_numbers()

    def _b64url(n: int) -> str:
        import base64
        return base64.urlsafe_b64encode(n.to_bytes(32, "big")).rstrip(b"=").decode()

    jwk_dict = {
        "kty": "EC", "crv": "P-256", "kid": kid, "use": "sig", "alg": "ES256",
        "x": _b64url(nums.x), "y": _b64url(nums.y),
    }
    jwks_cache = {kid: PyJWK(jwk_dict)}
    return token, jwks_cache


@pytest.fixture(scope="module")
def live_alphatrade_server():
    os.environ["PACT_VERIFICATION_MODE"] = "true"
    os.environ["AUTH_MODE"] = "jwt"
    os.environ["REDIS__ENABLED"] = "false"
    os.environ["MLFLOW_TRACKING_URI"] = "http://unused.invalid:5000"

    token, jwks_cache = _make_test_jwt()

    jwks_patch = patch("alphaTrade.security.alphakey_auth._fetch_jwks", return_value=jwks_cache)
    mlflow_patch = patch("alphaTrade.api.routers.models.MlflowClient")
    jwks_patch.start()
    mock_mlflow_cls = mlflow_patch.start()

    from tests.support.fake_mlflow import FakeMlflowClient
    mock_mlflow_cls.side_effect = lambda *args, **kwargs: FakeMlflowClient(*args, **kwargs)

    from alphaTrade.config import Settings
    from alphaTrade.health import HealthState
    from alphaTrade.api.app import create_app

    settings = Settings(auth_mode="jwt", pact_verification_mode=True)
    settings.redis.enabled = False

    from alphaTrade.store.db import run_migrations
    tmp_dir = tempfile.mkdtemp()
    db_url = f"sqlite:///{tmp_dir}/pact_verify.db"
    run_migrations(db_url)
    engine = create_engine(db_url)

    app = create_app(engine, HealthState(), settings=settings)

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 10
    while not getattr(server, "started", False) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn server did not start in time"

    port = server.servers[0].sockets[0].getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            r = httpx.get(
                f"{base_url}/api/v1/kill-switch",
                headers={"Authorization": f"Bearer {token}"},
                timeout=1,
            )
            if r.status_code < 500:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    else:
        pytest.fail("alphaTrade server did not become healthy in time")

    yield base_url, token

    server.should_exit = True
    thread.join(timeout=5)
    jwks_patch.stop()
    mlflow_patch.stop()


def test_alphalink_contract_verifies_against_live_alphatrade(
    live_alphatrade_server: tuple[str, str], tmp_path
):
    base_url, real_token = live_alphatrade_server
    assert _PACT_FILE.exists(), (
        f"pact file not found at {_PACT_FILE} — copy alphaLink/pacts/alphaLink-alphaTrade.json here"
    )

    pact_text = _PACT_FILE.read_text().replace("test-token-placeholder", real_token)
    rewritten_pact_path = tmp_path / "alphaLink-alphaTrade-rewritten.json"
    rewritten_pact_path.write_text(pact_text)

    # Pact recorded paths without the /api/v1 prefix (because the consumer test
    # sets ALPHATRADE_API_URL to the mock server root, not including /api/v1,
    # while the real default is http://localhost:8081/api/v1). Pass the prefix
    # as part of the transport URL so Pact hits the correct endpoints.
    verifier = (
        Verifier("alphaTrade", host="127.0.0.1")
        .add_transport(url=f"{base_url}/api/v1")
        .state_handler(f"{base_url}/internal/pact-state", body=True)
        .add_source(str(rewritten_pact_path))
    )
    verifier.verify()
