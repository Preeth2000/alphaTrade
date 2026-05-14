import pytest
from unittest.mock import MagicMock
from sqlmodel import create_engine, Session
from alphalink.store.db import run_migrations
from alphalink.store.repos import BotSettings, BotSettingsRepo


@pytest.fixture()
def engine(tmp_path):
    db = tmp_path / "test.db"
    run_migrations(db)
    return create_engine(f"sqlite:///{db}")


def test_hot_reload_updates_t212_client(engine):
    from alphalink.main import apply_bot_settings
    from alphalink.broker.t212_client import T212Client

    original = T212Client(api_key="old-key", env="demo")
    t212_holder = [original]

    with Session(engine) as s:
        BotSettingsRepo(s).upsert(BotSettings(id=1, t212_api_key="new-key", t212_env="live"))
        db_s = BotSettingsRepo(s).get()

    settings = MagicMock()
    apply_bot_settings(db_s, settings, t212_holder)

    assert t212_holder[0] is not original
    assert t212_holder[0]._headers == {"Authorization": "new-key"}


def test_hot_reload_no_change_keeps_client(engine):
    from alphalink.main import apply_bot_settings
    from alphalink.broker.t212_client import T212Client

    client = T212Client(api_key="same-key", env="demo")
    t212_holder = [client]

    with Session(engine) as s:
        BotSettingsRepo(s).upsert(BotSettings(id=1, t212_api_key="same-key", t212_env="demo"))
        db_s = BotSettingsRepo(s).get()

    settings = MagicMock()
    apply_bot_settings(db_s, settings, t212_holder)

    assert t212_holder[0] is client


def test_hot_reload_overlays_risk_settings(engine):
    from alphalink.main import apply_bot_settings

    with Session(engine) as s:
        BotSettingsRepo(s).upsert(BotSettings(id=1, max_positions=12, daily_loss_halt_pct=0.08))
        db_s = BotSettingsRepo(s).get()

    settings = MagicMock()
    t212_holder = [MagicMock()]
    t212_holder[0]._headers = {}

    apply_bot_settings(db_s, settings, t212_holder)

    assert settings.risk.max_positions == 12
    assert settings.risk.daily_loss_halt_pct == pytest.approx(0.08)
