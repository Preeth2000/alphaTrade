"""POST /internal/pact-state — Pact provider-state setup, test-only.

Only mounted when settings.pact_verification_mode is true (see
alphaTrade/api/app.py). Never reachable in a real deployment.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from alphaTrade.kill_switch import SENTINEL_FILE
from tests.support.fake_mlflow import seed_alias

router = APIRouter(prefix="/internal", tags=["internal"])


class PactStateRequest(BaseModel):
    state: str
    action: Literal["setup", "teardown"]
    params: Optional[dict[str, Any]] = None


@router.post("/pact-state")
def pact_state(body: PactStateRequest) -> dict[str, Any]:
    if body.action == "teardown":
        return {}

    params = body.params or {}

    if body.state == "the kill switch is active":
        Path(SENTINEL_FILE).touch()
        return {}

    if body.state == "the kill switch is not active":
        p = Path(SENTINEL_FILE)
        if p.exists():
            p.unlink()
        return {}

    if body.state == "a model exists in staging":
        seed_alias(params["modelName"], "staging", params["version"])
        return {}

    if body.state == "a model exists in production":
        seed_alias(params["modelName"], "production", params["version"])
        return {}

    if body.state in (
        "no staging alias exists for the model",
        "no production alias exists for the model",
    ):
        return {}

    return {}
