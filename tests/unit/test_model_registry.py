"""Tests for ModelRegistry hot-reload: add/remove models without restart."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from alphalink.model_registry import ModelRegistry


def _make_manifest(run_name: str, interval: str = "1d", ticker: str = "AAPL") -> MagicMock:
    m = MagicMock()
    m.run_name = run_name
    m.interval = interval
    m.ticker = ticker
    return m


def _make_model() -> MagicMock:
    return MagicMock()


class TestModelRegistry:
    @pytest.mark.asyncio
    async def test_initial_load(self, tmp_path):
        """refresh() with new models loads them into registry."""
        manifest = _make_manifest("aapl_mlp")
        model = _make_model()

        registry = ModelRegistry()
        with patch("alphalink.model_registry.scan_models", return_value=[(manifest, model)]):
            await registry.refresh(tmp_path, overrides={})

        assert "aapl_mlp" in registry.by_run_name

    @pytest.mark.asyncio
    async def test_new_model_hot_added(self, tmp_path):
        """Model added to disk appears after next refresh."""
        registry = ModelRegistry()
        with patch("alphalink.model_registry.scan_models", return_value=[]):
            await registry.refresh(tmp_path, overrides={})
        assert len(registry.by_run_name) == 0

        manifest = _make_manifest("msft_mlp")
        model = _make_model()
        with patch("alphalink.model_registry.scan_models", return_value=[(manifest, model)]):
            await registry.refresh(tmp_path, overrides={})

        assert "msft_mlp" in registry.by_run_name

    @pytest.mark.asyncio
    async def test_removed_model_hot_dropped(self, tmp_path):
        """Model removed from disk is gone after next refresh."""
        manifest = _make_manifest("aapl_mlp")
        model = _make_model()

        registry = ModelRegistry()
        with patch("alphalink.model_registry.scan_models", return_value=[(manifest, model)]):
            await registry.refresh(tmp_path, overrides={})
        assert "aapl_mlp" in registry.by_run_name

        with patch("alphalink.model_registry.scan_models", return_value=[]):
            await registry.refresh(tmp_path, overrides={})

        assert "aapl_mlp" not in registry.by_run_name

    @pytest.mark.asyncio
    async def test_disabled_override_excluded(self, tmp_path):
        """Model with enabled=False in overrides excluded from active set."""
        manifest = _make_manifest("aapl_mlp")
        model = _make_model()

        override = MagicMock()
        override.enabled = False

        registry = ModelRegistry()
        with patch("alphalink.model_registry.scan_models", return_value=[(manifest, model)]):
            await registry.refresh(tmp_path, overrides={"aapl_mlp": override})

        assert "aapl_mlp" not in registry.by_run_name

    @pytest.mark.asyncio
    async def test_enabled_override_included(self, tmp_path):
        """Model with enabled=True in overrides stays in active set."""
        manifest = _make_manifest("aapl_mlp")
        model = _make_model()

        override = MagicMock()
        override.enabled = True

        registry = ModelRegistry()
        with patch("alphalink.model_registry.scan_models", return_value=[(manifest, model)]):
            await registry.refresh(tmp_path, overrides={"aapl_mlp": override})

        assert "aapl_mlp" in registry.by_run_name

    @pytest.mark.asyncio
    async def test_snapshot_by_interval(self, tmp_path):
        """snapshot_by_interval groups active models correctly."""
        m1 = _make_manifest("aapl_1d", interval="1d")
        m2 = _make_manifest("msft_1h", interval="1h")
        mod = _make_model()

        registry = ModelRegistry()
        with patch("alphalink.model_registry.scan_models", return_value=[(m1, mod), (m2, mod)]):
            await registry.refresh(tmp_path, overrides={})

        by_interval = registry.snapshot_by_interval()
        assert "1d" in by_interval
        assert "1h" in by_interval
        assert len(by_interval["1d"]) == 1
        assert len(by_interval["1h"]) == 1

    @pytest.mark.asyncio
    async def test_scan_error_preserves_existing(self, tmp_path):
        """If scan_models raises, existing registry unchanged."""
        manifest = _make_manifest("aapl_mlp")
        model = _make_model()

        registry = ModelRegistry()
        with patch("alphalink.model_registry.scan_models", return_value=[(manifest, model)]):
            await registry.refresh(tmp_path, overrides={})

        with patch("alphalink.model_registry.scan_models", side_effect=Exception("disk error")):
            await registry.refresh(tmp_path, overrides={})

        assert "aapl_mlp" in registry.by_run_name
