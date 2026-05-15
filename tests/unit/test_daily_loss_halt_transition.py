"""Daily-loss-halt alert must fire on False→True transition only, not every halted tick."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest
from sqlmodel import SQLModel, create_engine

from alphaTrade.config import Settings
from alphaTrade.health import HealthState
from alphaTrade.main import make_tick

INTERVAL = "1d"

_DF = pd.DataFrame({
    "Open": [150.0], "High": [155.0], "Low": [148.0],
    "Close": [152.0], "Volume": [1_000_000],
})


def _engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    return eng


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        t212_api_key="test-key",
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
    model.run.return_value = np.array([-1.0, -1.0, 2.0])  # → HOLD
    reg = MagicMock()
    reg.refresh = AsyncMock()
    reg.snapshot_by_interval.return_value = {INTERVAL: [(manifest, model)]}
    reg.by_run_name = {"test_run": (manifest, model)}
    return reg


@pytest.mark.asyncio
async def test_notify_fires_once_for_consecutive_halted_ticks(tmp_path):
    """Two halted ticks in a row → notify called exactly once (on first halted tick)."""
    manifest = _manifest()
    # tick1: equity=10_000 → today_open recorded, 0% loss, not halted
    # tick2: equity=9_000 → 10% loss (> 5% threshold) → halted, notify fires
    # tick3: equity=9_000 → still halted → notify must NOT fire again
    equities = [10_000.0, 9_000.0, 9_000.0]

    tick = make_tick(
        INTERVAL,
        registry=_registry(manifest),
        settings=_settings(tmp_path),
        engine=_engine(),
        t212_holder=[MagicMock(get_total_equity=MagicMock(side_effect=equities))],
        provider=MagicMock(fetch_ohlcv=MagicMock(return_value=_DF)),
        health_state=HealthState(),
        oco_tasks=set(),
        static_map={"AAPL": "AAPL_US_EQ"},
    )

    with (
        patch("alphaTrade.main.compute_features", return_value=_DF),
        patch("alphaTrade.main.normalize", return_value=_DF),
        patch("alphaTrade.main.build_input", return_value=np.zeros((1, 1))),
        patch("alphaTrade.main.consensus_by_ticker", return_value={"AAPL": "HOLD"}),
        patch("alphaTrade.main.wh") as mock_wh,
    ):
        await tick()  # tick 1: not halted
        await tick()  # tick 2: halted → notify fires
        await tick()  # tick 3: still halted → notify must NOT fire

        notify_calls = [
            c for c in mock_wh.notify.call_args_list
            if len(c.args) > 1 and "Daily loss halt" in c.args[1]
        ]
        assert len(notify_calls) == 1, (
            f"Expected 1 halt notify call, got {len(notify_calls)}: {mock_wh.notify.call_args_list}"
        )


@pytest.mark.asyncio
async def test_notify_fires_on_transition_tick_not_before(tmp_path):
    """First tick not halted, second tick halted → notify fires only on second tick."""
    manifest = _manifest()
    equities = [10_000.0, 9_000.0]

    tick = make_tick(
        INTERVAL,
        registry=_registry(manifest),
        settings=_settings(tmp_path),
        engine=_engine(),
        t212_holder=[MagicMock(get_total_equity=MagicMock(side_effect=equities))],
        provider=MagicMock(fetch_ohlcv=MagicMock(return_value=_DF)),
        health_state=HealthState(),
        oco_tasks=set(),
        static_map={"AAPL": "AAPL_US_EQ"},
    )

    with patch("alphaTrade.main.wh") as mock_wh:
        with (
            patch("alphaTrade.main.compute_features", return_value=_DF),
            patch("alphaTrade.main.normalize", return_value=_DF),
            patch("alphaTrade.main.build_input", return_value=np.zeros((1, 1))),
            patch("alphaTrade.main.consensus_by_ticker", return_value={"AAPL": "HOLD"}),
        ):
            await tick()  # equity=10_000, not halted → no notify
            halt_notify_count_after_tick1 = sum(
                1 for c in mock_wh.notify.call_args_list
                if len(c.args) > 1 and "Daily loss halt" in c.args[1]
            )
            assert halt_notify_count_after_tick1 == 0

            await tick()  # equity=9_000, halted → notify fires
            halt_notify_count_after_tick2 = sum(
                1 for c in mock_wh.notify.call_args_list
                if len(c.args) > 1 and "Daily loss halt" in c.args[1]
            )
            assert halt_notify_count_after_tick2 == 1


@pytest.mark.asyncio
async def test_notify_fires_again_on_re_entry(tmp_path):
    """Halt clears then triggers again → notify fires on each re-entry."""
    manifest = _manifest()
    # tick1: 10_000 (base), tick2: 9_000 (halted), tick3: 10_000 (recovered), tick4: 9_000 (halted again)
    equities = [10_000.0, 9_000.0, 10_000.0, 9_000.0]

    tick = make_tick(
        INTERVAL,
        registry=_registry(manifest),
        settings=_settings(tmp_path),
        engine=_engine(),
        t212_holder=[MagicMock(get_total_equity=MagicMock(side_effect=equities))],
        provider=MagicMock(fetch_ohlcv=MagicMock(return_value=_DF)),
        health_state=HealthState(),
        oco_tasks=set(),
        static_map={"AAPL": "AAPL_US_EQ"},
    )

    with patch("alphaTrade.main.wh") as mock_wh:
        with (
            patch("alphaTrade.main.compute_features", return_value=_DF),
            patch("alphaTrade.main.normalize", return_value=_DF),
            patch("alphaTrade.main.build_input", return_value=np.zeros((1, 1))),
            patch("alphaTrade.main.consensus_by_ticker", return_value={"AAPL": "HOLD"}),
        ):
            await tick()  # not halted
            await tick()  # halted → notify #1
            await tick()  # recovered
            await tick()  # halted again → notify #2

        halt_notify_count = sum(
            1 for c in mock_wh.notify.call_args_list
            if len(c.args) > 1 and "Daily loss halt" in c.args[1]
        )
        assert halt_notify_count == 2, (
            f"Expected 2 halt notify calls (one per halt entry), got {halt_notify_count}"
        )
