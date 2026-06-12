from __future__ import annotations
from typing import Any, Literal
from collections.abc import Callable

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from alphaTrade.health import HealthState
from alphaTrade.data.provider_verify import (
    verify_provider_credentials,
    _verify_polygon_key,
    _verify_yfinance,
    _POLYGON_PROBE_URL,
    _TIMEOUT,
)

_T212_BASE = {
    "demo": "https://demo.trading212.com/api/v0",
    "live": "https://live.trading212.com/api/v0",
}
_T212_ENV_MAP = {
    "demo": "demo",
    "invest": "live",
    "isa": "live",
}


class T212VerifyRequest(BaseModel):
    account: Literal["demo", "invest", "isa"]
    api_key: str
    secret_key: str = ""


class PolygonVerifyRequest(BaseModel):
    api_key: str


def make_router(api_key_dep: Callable, health_state: HealthState | None = None, settings=None) -> APIRouter:
    router = APIRouter()

    @router.post("/verify/t212")
    def verify_t212(
        body: T212VerifyRequest,
        _: None = Depends(api_key_dep),
    ) -> dict[str, Any]:
        env = _T212_ENV_MAP[body.account]
        base = _T212_BASE[env]
        url = f"{base}/equity/account/summary"
        try:
            if body.secret_key:
                auth = httpx.BasicAuth(body.api_key, body.secret_key)
                headers: dict[str, str] = {}
            else:
                auth = None
                headers = {"Authorization": body.api_key}

            r = httpx.get(
                url,
                auth=auth,
                headers=headers,
                timeout=_TIMEOUT,
            )
            if r.status_code == 200:
                return {"valid": True, "account": body.account}
            return {"valid": False, "account": body.account, "error": f"{r.status_code} {r.reason_phrase}"}
        except httpx.HTTPError as exc:
            return {"valid": False, "account": body.account, "error": str(exc)}

    @router.post("/verify/polygon")
    def verify_polygon(
        body: PolygonVerifyRequest,
        _: None = Depends(api_key_dep),
    ) -> dict[str, Any]:
        try:
            r = httpx.get(
                _POLYGON_PROBE_URL,
                headers={"Authorization": f"Bearer {body.api_key}"},
                timeout=_TIMEOUT,
            )
            if r.status_code == 200:
                try:
                    body_json = r.json()
                except Exception:
                    body_json = []
                exchanges = body_json if isinstance(body_json, list) else []
                rate_limit = r.headers.get("X-RateLimit-Limit")
                return {
                    "valid": True,
                    "details": {
                        "exchanges_count": len(exchanges),
                        "rate_limit": rate_limit,
                    },
                }
            return {"valid": False, "error": f"{r.status_code} {r.reason_phrase}"}
        except httpx.HTTPError as exc:
            return {"valid": False, "error": str(exc)}

    @router.post("/verify/alphatrade-key")
    def verify_alphatrade_key(
        _: None = Depends(api_key_dep),
    ) -> dict[str, Any]:
        if health_state is not None:
            health_state.alphatrade_ok = True
        return {"valid": True}

    @router.post("/verify/provider")
    def verify_provider(
        _: None = Depends(api_key_dep),
    ) -> dict[str, Any]:
        if settings is None:
            return {"valid": False, "error": "settings not available"}

        provider = settings.data_provider
        ok, result = verify_provider_credentials(settings)
        result["provider"] = provider

        if health_state is not None:
            health_state.provider_ok = ok
            health_state.provider_name = provider

        return result

    return router
