"""Shared test fixtures."""
import os
import pytest


@pytest.fixture(autouse=True)
def _mock_env(monkeypatch):
    """Provide minimal env so Settings doesn't fail in unit tests."""
    monkeypatch.setenv("T212_API_KEY", "test-key")
    monkeypatch.setenv("T212_ENV", "demo")
    monkeypatch.setenv("DATA_PROVIDER", "yfinance")
