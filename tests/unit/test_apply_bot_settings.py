"""Unit tests for _t212_credentials account selection."""
from __future__ import annotations

from alphaTrade.store.repos import BotSettings
from alphaTrade.main import _t212_credentials


def _make_db_settings(**kwargs) -> BotSettings:
    defaults = dict(
        t212_active_account="demo",
        t212_demo_api_key="demo-key",
        t212_demo_secret_key="demo-secret",
        t212_invest_api_key="invest-key",
        t212_invest_secret_key="invest-secret",
        t212_isa_api_key="isa-key",
        t212_isa_secret_key="isa-secret",
    )
    defaults.update(kwargs)
    return BotSettings(id=1, **defaults)


def test_demo_account_returns_demo_key():
    db_s = _make_db_settings(t212_active_account="demo")
    key, secret, env = _t212_credentials(db_s)
    assert key == "demo-key"
    assert secret == "demo-secret"
    assert env == "demo"


def test_invest_account_returns_invest_key():
    db_s = _make_db_settings(t212_active_account="invest")
    key, secret, env = _t212_credentials(db_s)
    assert key == "invest-key"
    assert secret == "invest-secret"
    assert env == "live"


def test_isa_account_returns_isa_key():
    db_s = _make_db_settings(t212_active_account="isa")
    key, secret, env = _t212_credentials(db_s)
    assert key == "isa-key"
    assert secret == "isa-secret"
    assert env == "live"


def test_empty_active_account_defaults_to_demo():
    db_s = _make_db_settings(t212_active_account="")
    key, secret, env = _t212_credentials(db_s)
    assert env == "demo"
    assert key == "demo-key"


def test_none_active_account_defaults_to_demo():
    db_s = _make_db_settings(t212_active_account=None)
    key, secret, env = _t212_credentials(db_s)
    assert env == "demo"
