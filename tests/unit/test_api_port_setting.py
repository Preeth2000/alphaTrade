"""Test api_port setting in Settings."""
import pytest


def test_settings_has_api_port(monkeypatch):
    monkeypatch.setenv("T212_API_KEY", "k")
    from alphalink.config import Settings
    s = Settings()
    assert s.api_port == 8081


def test_settings_api_port_overridable(monkeypatch):
    monkeypatch.setenv("T212_API_KEY", "k")
    monkeypatch.setenv("API_PORT", "9000")
    import importlib
    import alphalink.config as m
    importlib.reload(m)
    s = m.Settings()
    assert s.api_port == 9000
