"""PACT_VERIFICATION_MODE gate on the /internal/pact-state endpoint."""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlmodel import create_engine

from alphaTrade.config import Settings
from alphaTrade.health import HealthState
from tests.support.fake_mlflow import reset_registry


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry()
    yield
    reset_registry()


def _make_app(pact_verification_mode: bool, tmp_path):
    os.environ["PACT_VERIFICATION_MODE"] = "true" if pact_verification_mode else "false"
    from alphaTrade.api.app import create_app

    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    settings = Settings(pact_verification_mode=pact_verification_mode, auth_mode="legacy")
    os.environ["ALPHATRADE_INSECURE_NO_AUTH"] = "true"
    return create_app(engine, HealthState(), settings=settings)


def test_pact_state_endpoint_absent_when_mode_disabled(tmp_path):
    app = _make_app(pact_verification_mode=False, tmp_path=tmp_path)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/internal/pact-state", json={"state": "x", "action": "setup", "params": {}})
    assert resp.status_code == 404


def test_pact_state_endpoint_present_when_mode_enabled(tmp_path):
    app = _make_app(pact_verification_mode=True, tmp_path=tmp_path)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/internal/pact-state",
        json={"state": "no staging alias exists for the model", "action": "setup", "params": {"modelName": "x"}},
    )
    assert resp.status_code == 200
    assert resp.json() == {}


def test_pact_state_seeds_staging_alias(tmp_path):
    app = _make_app(pact_verification_mode=True, tmp_path=tmp_path)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/internal/pact-state",
        json={
            "state": "a model exists in staging",
            "action": "setup",
            "params": {"modelName": "pact-test-model", "version": "3"},
        },
    )
    assert resp.status_code == 200

    from tests.support.fake_mlflow import FakeMlflowClient

    mv = FakeMlflowClient().get_model_version_by_alias("pact-test-model", "staging")
    assert mv.version == "3"


def test_pact_state_kill_switch_states(tmp_path):
    from pathlib import Path

    from alphaTrade.kill_switch import SENTINEL_FILE

    sentinel = Path(SENTINEL_FILE)
    if sentinel.exists():
        sentinel.unlink()

    app = _make_app(pact_verification_mode=True, tmp_path=tmp_path)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post("/internal/pact-state", json={"state": "the kill switch is active", "action": "setup", "params": None})
    assert resp.status_code == 200
    assert sentinel.exists()

    resp = client.post("/internal/pact-state", json={"state": "the kill switch is not active", "action": "setup", "params": None})
    assert resp.status_code == 200
    assert not sentinel.exists()
