"""Shared in-memory fake MLflow registry for Pact provider verification.

The Pact state-setup endpoint (alphaTrade/api/routers/pact_state.py) writes
model alias state here; alphaTrade's real route handlers, with MlflowClient
patched to FakeMlflowClient for the duration of verification, read it back.
Both run in the same process (uvicorn in a background thread), so a plain
module-level dict is sufficient — no real MLflow server involved.
"""
from __future__ import annotations

from typing import Optional

from mlflow.exceptions import MlflowException

_REGISTRY: dict[str, dict[str, str]] = {}


class FakeModelVersion:
    def __init__(self, version: str, tags: Optional[dict[str, str]] = None) -> None:
        self.version = version
        self.tags = tags or {}


class FakeMlflowClient:
    """Drop-in replacement for mlflow.MlflowClient, backed by _REGISTRY.

    Only implements the methods alphaTrade's promote/demote/ownership-check
    code paths actually call.
    """

    def __init__(self, tracking_uri: Optional[str] = None) -> None:
        self._tracking_uri = tracking_uri

    def get_model_version_by_alias(self, model_name: str, alias: str) -> FakeModelVersion:
        aliases = _REGISTRY.get(model_name, {})
        if alias not in aliases:
            raise MlflowException(f"No alias {alias!r} for model {model_name!r}")
        return FakeModelVersion(aliases[alias])

    def set_registered_model_alias(self, model_name: str, alias: str, version: str) -> None:
        _REGISTRY.setdefault(model_name, {})[alias] = version

    def delete_registered_model_alias(self, model_name: str, alias: str) -> None:
        _REGISTRY.get(model_name, {}).pop(alias, None)


def reset_registry() -> None:
    _REGISTRY.clear()


def seed_alias(model_name: str, alias: str, version: str) -> None:
    _REGISTRY.setdefault(model_name, {})[alias] = version
