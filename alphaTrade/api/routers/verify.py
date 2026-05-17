from __future__ import annotations
from typing import Any, Literal
from collections.abc import Callable

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel

_T212_BASE = {
    "demo": "https://demo.trading212.com/api/v0",
    "live": "https://live.trading212.com/api/v0",
}
_T212_ENV_MAP = {
    "demo": "demo",
    "invest": "live",
    "isa": "live",
}
_POLYGON_EXCHANGES_URL = "https://api.polygon.io/v1/meta/exchanges"
_TIMEOUT = 10.0


class T212VerifyRequest(BaseModel):
    account: Literal["demo", "invest", "isa"]
    api_key: str
    secret_key: str = ""


class PolygonVerifyRequest(BaseModel):
    api_key: str


def make_router(api_key_dep: Callable) -> APIRouter:
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
                try:
                    details = r.json()
                except Exception:
                    details = {}
                return {"valid": True, "account": body.account, "details": details}
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
                _POLYGON_EXCHANGES_URL,
                params={"apiKey": body.api_key},
                timeout=_TIMEOUT,
            )
            if r.status_code == 200:
                body_json = r.json()
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

    return router
