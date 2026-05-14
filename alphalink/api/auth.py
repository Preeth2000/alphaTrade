from __future__ import annotations
import os
from fastapi import Header, HTTPException
from sqlmodel import Session
from sqlalchemy.engine import Engine


def make_api_key_dep(engine: Engine):
    def require_api_key(x_api_key: str = Header(default="")) -> None:
        from alphalink.store.repos import BotSettingsRepo
        with Session(engine) as s:
            db_s = BotSettingsRepo(s).get()
        active_key = (db_s.alphalink_api_key if db_s else "") or os.environ.get("ALPHALINK_API_KEY", "")
        if not active_key:
            return
        if x_api_key != active_key:
            raise HTTPException(status_code=403, detail="Invalid API key")
    return require_api_key
