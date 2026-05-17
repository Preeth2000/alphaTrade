"""Verify T212Client calls inside tick() run off the event loop thread.

Blocking sync HTTP calls (httpx + time.sleep retries) on the event loop starve
other coroutines and can cause missed bar-close windows under T212 latency.
"""
from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest
from sqlmodel import SQLModel, create_engine

from alphaTrade.broker.t212_client import T212Client
from alphaTrade.config import Settings
from alphaTrade.health import HealthState
from alphaTrade.main import make_tick
from alphaTrade.store.repos import EquityCurve, InstrumentCache, Order, Position, Signal

INTERVAL = "1d"

_HOLD_DF = pd.DataFrame({
    "Open": [150.0], "High": [155.0], "Low": [148.0],
    "Close": [152.0], "Volume": [1_000_000],
})


def _engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    return eng


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        t212_demo_api_key="test-key",
        state_db_path=tmp_path / "state.db",
        models_dir=tmp_path / "models",
        overrides_path=tmp_path / "overrides.yaml",
    )


def _manifest():
    m = MagicMock()
    m.ticker = "AAPL"
    m.run_name = "test_run"
    m.interval = INTERVAL
    m.window = 20
    m.feature_names = ["Close"]
    return m


def _registry(manifest):
    model = MagicMock()
    model.run.return_value = np.array([-1.0, -1.0, 2.0])  # → HOLD, no order path

    reg = MagicMock()
    reg.refresh = AsyncMock()
    reg.snapshot_by_interval.return_value = {INTERVAL: [(manifest, model)]}
    reg.by_run_name = {"test_run": (manifest, model)}
    return reg


async def test_get_total_equity_runs_in_worker_thread(tmp_path):
    """get_total_equity() must run in a thread pool, not on the event loop thread."""
    event_loop_thread = threading.current_thread()
    call_thread: threading.Thread | None = None

    def slow_equity():
        nonlocal call_thread
        call_thread = threading.current_thread()
        time.sleep(0.02)
        return 1_000.0

    manifest = _manifest()
    t212 = MagicMock(spec=T212Client)
    t212.get_total_equity.side_effect = slow_equity

    provider = MagicMock()
    provider.fetch_ohlcv.return_value = _HOLD_DF

    concurrent_ran = False

    async def concurrent():
        nonlocal concurrent_ran
        concurrent_ran = True

    tick = make_tick(
        INTERVAL,
        registry=_registry(manifest),
        settings=_settings(tmp_path),
        engine=_engine(),
        t212_holder=[t212],
        provider_holder=[provider],
        health_state=HealthState(),
        oco_tasks=set(),
        static_map={"AAPL": "AAPL_US_EQ"},
    )

    with (
        patch("alphaTrade.main.compute_features", return_value=_HOLD_DF),
        patch("alphaTrade.main.normalize", return_value=_HOLD_DF),
        patch("alphaTrade.main.build_input", return_value=np.zeros((1, 1))),
        patch("alphaTrade.main.consensus_by_ticker", return_value={"AAPL": "HOLD"}),
    ):
        bg = asyncio.create_task(concurrent())
        await tick()
        await bg

    assert call_thread is not None, "get_total_equity was never called"
    assert call_thread != event_loop_thread, (
        "get_total_equity ran on the event loop thread — it blocks the loop during T212 latency"
    )
    assert concurrent_ran
