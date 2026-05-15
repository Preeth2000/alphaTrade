from __future__ import annotations
from collections.abc import Callable
from pathlib import Path
from fastapi import APIRouter, Depends
from alphalink.kill_switch import SENTINEL_FILE, is_halted


def make_router(api_key_dep: Callable) -> APIRouter:
    router = APIRouter()

    def _status() -> dict:
        return {"halted": is_halted(), "sentinel_file": SENTINEL_FILE}

    @router.get("/kill-switch")
    def get_status(_: None = Depends(api_key_dep)):
        return _status()

    @router.post("/halt")
    def halt(_: None = Depends(api_key_dep)):
        Path(SENTINEL_FILE).touch()
        return _status()

    @router.post("/resume")
    def resume(_: None = Depends(api_key_dep)):
        p = Path(SENTINEL_FILE)
        if p.exists():
            p.unlink()
        return _status()

    return router
