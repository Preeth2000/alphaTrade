"""Verify reconcile_positions() is async and runs get_positions() off event loop."""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import SQLModel, Session, create_engine

from alphalink.broker.t212_client import T212Client
from alphalink.config import Settings
from alphalink.main import reconcile_positions
from alphalink.store.repos import (
    EquityCurve, InstrumentCache, Order, Position, PositionRepo, Signal,
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        t212_api_key="test-key",
        state_db_path=tmp_path / "state.db",
        models_dir=tmp_path / "models",
        overrides_path=tmp_path / "overrides.yaml",
    )


class TestReconcilePositionsIsAsync:
    def test_is_coroutine_function(self):
        """reconcile_positions must be async def to not block the event loop."""
        assert inspect.iscoroutinefunction(reconcile_positions)


class TestGetPositionsUsesToThread:
    async def test_get_positions_runs_via_asyncio_to_thread(self, tmp_path):
        """get_positions() must be dispatched via asyncio.to_thread."""
        t212 = MagicMock(spec=T212Client)
        t212.get_positions.return_value = []

        with patch("alphalink.main.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=[])
            await reconcile_positions(t212, _settings(tmp_path))

        mock_asyncio.to_thread.assert_called_once_with(t212.get_positions)

    async def test_reconcile_completes_without_raising(self, tmp_path):
        """reconcile_positions completes normally with empty T212 positions."""
        t212 = MagicMock(spec=T212Client)
        t212.get_positions.return_value = []
        # Should not raise
        await reconcile_positions(t212, _settings(tmp_path))

    async def test_reconcile_handles_get_positions_exception(self, tmp_path):
        """reconcile_positions logs warning and returns on T212 failure."""
        t212 = MagicMock(spec=T212Client)
        t212.get_positions.side_effect = RuntimeError("T212 down")
        # Should not raise — logs warning and returns
        await reconcile_positions(t212, _settings(tmp_path))


class TestReconcileDivergenceNotify:
    def _engine(self, tmp_path: Path):
        from alphalink.store.db import get_engine
        return get_engine(tmp_path / "state.db")

    async def test_notify_fires_for_stale_local_position(self, tmp_path):
        """wh.notify called with category='reconcile-divergence' when local pos not in T212."""
        eng = self._engine(tmp_path)
        settings = _settings(tmp_path)

        # Seed local DB with a position T212 no longer holds
        with Session(eng) as session:
            repo = PositionRepo(session)
            repo.upsert(Position(
                t212_ticker="AAPL_US_EQ",
                quantity=10.0,
                avg_entry=150.0,
                last_signal_ts=None,
                cooldown_until_ts=None,
            ))

        # T212 returns empty — no AAPL position held
        t212 = MagicMock(spec=T212Client)
        t212.get_positions.return_value = []

        with patch("alphalink.main.wh.notify") as mock_notify:
            await reconcile_positions(t212, settings)

        divergence_calls = [
            c for c in mock_notify.call_args_list
            if c.kwargs.get("category") == "reconcile-divergence"
        ]
        assert len(divergence_calls) == 1
        assert divergence_calls[0].args[0] == "WARNING"
        assert "AAPL_US_EQ" in divergence_calls[0].args[1]

    async def test_no_notify_when_positions_match(self, tmp_path):
        """wh.notify NOT called when T212 and local state agree."""
        settings = _settings(tmp_path)
        t212 = MagicMock(spec=T212Client)
        # T212 holds AAPL, local DB is empty — no stale removal, no divergence
        t212.get_positions.return_value = [
            {"ticker": "AAPL_US_EQ", "quantity": 10.0, "averagePricePaid": 150.0}
        ]

        with patch("alphalink.main.wh.notify") as mock_notify:
            await reconcile_positions(t212, settings)

        divergence_calls = [
            c for c in mock_notify.call_args_list
            if c.kwargs.get("category") == "reconcile-divergence"
        ]
        assert len(divergence_calls) == 0
