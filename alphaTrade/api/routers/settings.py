from __future__ import annotations
from collections.abc import Callable
from typing import Literal, Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session
from alphaTrade.store.repos import BotSettings, BotSettingsRepo

_SENSITIVE = frozenset({
    "t212_demo_api_key", "t212_demo_secret_key",
    "t212_invest_api_key", "t212_invest_secret_key",
    "t212_isa_api_key", "t212_isa_secret_key",
    "polygon_api_key", "email_smtp_password",
    "slack_webhook_url", "alphaTrade_api_key",
})


class BotSettingsUpdate(BaseModel):
    t212_active_account: Optional[Literal["demo", "invest", "isa"]] = None
    t212_demo_api_key: Optional[str] = None
    t212_demo_secret_key: Optional[str] = None
    t212_invest_api_key: Optional[str] = None
    t212_invest_secret_key: Optional[str] = None
    t212_isa_api_key: Optional[str] = None
    t212_isa_secret_key: Optional[str] = None
    data_provider: Optional[str] = None
    polygon_api_key: Optional[str] = None
    slack_enabled: Optional[bool] = None
    slack_webhook_url: Optional[str] = None
    slack_min_level: Optional[str] = None
    email_enabled: Optional[bool] = None
    email_smtp_host: Optional[str] = None
    email_smtp_port: Optional[int] = None
    email_smtp_user: Optional[str] = None
    email_smtp_password: Optional[str] = None
    email_from_addr: Optional[str] = None
    email_to_addrs: Optional[str] = None
    email_min_level: Optional[str] = None
    size_pct: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    cooldown_bars: Optional[int] = None
    extended_hours: Optional[bool] = None
    max_positions: Optional[int] = None
    daily_loss_halt_pct: Optional[float] = None
    alphaTrade_api_key: Optional[str] = None
    safe_mode: Optional[bool] = None
    dangerously_allow_pyramid: Optional[bool] = None


def _mask(s: BotSettings) -> dict:
    d = s.model_dump()
    for key in _SENSITIVE:
        if d.get(key):
            d[key] = "***"
    return d


def make_router(
    session_dep: Callable,
    api_key_dep: Callable,
    settings=None,
    t212_holder: list | None = None,
    provider_holder: list | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/settings")
    def get_settings(
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ) -> dict:
        s = BotSettingsRepo(session).get() or BotSettings(id=1)
        return _mask(s)

    @router.put("/settings")
    def update_settings(
        update: BotSettingsUpdate,
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ) -> dict:
        repo = BotSettingsRepo(session)
        s = repo.get() or BotSettings(id=1)
        for field, value in update.model_dump(exclude_none=True).items():
            setattr(s, field, value)
        repo.upsert(s)
        saved = repo.get() or BotSettings(id=1)
        if settings is not None and t212_holder is not None and provider_holder is not None:
            from alphaTrade.main import apply_bot_settings
            apply_bot_settings(saved, settings, t212_holder, provider_holder)
        return _mask(saved)

    return router
