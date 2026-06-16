"""Unit tests for the shared fake MLflow registry used by Pact provider
verification (tests.support.fake_mlflow)."""
from __future__ import annotations

import pytest
from mlflow.exceptions import MlflowException

from tests.support.fake_mlflow import FakeMlflowClient, reset_registry, seed_alias


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry()
    yield
    reset_registry()


def test_get_model_version_by_alias_returns_seeded_version():
    seed_alias("my-model", "staging", "3")
    client = FakeMlflowClient(tracking_uri="unused")
    mv = client.get_model_version_by_alias("my-model", "staging")
    assert mv.version == "3"
    assert mv.tags == {}


def test_get_model_version_by_alias_raises_when_alias_missing():
    client = FakeMlflowClient(tracking_uri="unused")
    with pytest.raises(MlflowException):
        client.get_model_version_by_alias("nonexistent-model", "staging")


def test_set_registered_model_alias_writes_to_registry():
    client = FakeMlflowClient(tracking_uri="unused")
    client.set_registered_model_alias("my-model", "production", "7")
    mv = client.get_model_version_by_alias("my-model", "production")
    assert mv.version == "7"


def test_delete_registered_model_alias_removes_it():
    seed_alias("my-model", "staging", "3")
    client = FakeMlflowClient(tracking_uri="unused")
    client.delete_registered_model_alias("my-model", "staging")
    with pytest.raises(MlflowException):
        client.get_model_version_by_alias("my-model", "staging")
