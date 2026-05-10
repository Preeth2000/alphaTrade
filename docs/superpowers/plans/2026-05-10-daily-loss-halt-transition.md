# Daily-Loss-Halt Transition Alert Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fire `wh.notify()` for daily-loss-halt exactly once per halt event (on `False → True` transition), not every tick while halted.

**Architecture:** Add a `_prev_halt: list[bool] = [False]` closure variable in `make_tick()`. Each tick, only call `wh.notify()` when transitioning into halt. Update `_prev_halt[0]` unconditionally. Uses `list[bool]` (not bare `bool`) so the inner `tick()` coroutine can mutate it without `nonlocal`.

**Tech Stack:** Python stdlib only — no new dependencies.

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `alphalink/main.py` | Modify | Add `_prev_halt` closure var; guard `wh.notify` + `log.warning` behind transition check |
| `tests/unit/test_daily_loss_halt_transition.py` | Create | Three tests covering: repeated halt ticks, first-tick transition, halt re-entry |

---

### Task 1: Implement transition-only halt alert (TDD)

**Files:**
- Create: `tests/unit/test_daily_loss_halt_transition.py`
- Modify: `alphalink/main.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_daily_loss_halt_transition.py`:

```python
"""Daily-loss-halt alert must fire on False→True transition only, not every halted tick."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest
from sqlmodel import SQLModel, create_engine

from alphalink.config import Settings
from alphalink.health import HealthState
from alphalink.main import make_tick

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


def _make_t212(equity: float) -> MagicMock:
    t212 = MagicMock()
    t212.get_total_equity.return_value = equity
    return t212


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
        t212=MagicMock(get_total_equity=MagicMock(side_effect=equities)),
        provider=MagicMock(fetch_ohlcv=MagicMock(return_value=_DF)),
        health_state=HealthState(),
        oco_tasks=set(),
        static_map={"AAPL": "AAPL_US_EQ"},
    )

    with (
        patch("alphalink.main.compute_features", return_value=_DF),
        patch("alphalink.main.normalize", return_value=_DF),
        patch("alphalink.main.build_input", return_value=np.zeros((1, 1))),
        patch("alphalink.main.consensus_by_ticker", return_value={"AAPL": "HOLD"}),
        patch("alphalink.main.wh") as mock_wh,
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
        t212=MagicMock(get_total_equity=MagicMock(side_effect=equities)),
        provider=MagicMock(fetch_ohlcv=MagicMock(return_value=_DF)),
        health_state=HealthState(),
        oco_tasks=set(),
        static_map={"AAPL": "AAPL_US_EQ"},
    )

    with patch("alphalink.main.wh") as mock_wh:
        with (
            patch("alphalink.main.compute_features", return_value=_DF),
            patch("alphalink.main.normalize", return_value=_DF),
            patch("alphalink.main.build_input", return_value=np.zeros((1, 1))),
            patch("alphalink.main.consensus_by_ticker", return_value={"AAPL": "HOLD"}),
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
        t212=MagicMock(get_total_equity=MagicMock(side_effect=equities)),
        provider=MagicMock(fetch_ohlcv=MagicMock(return_value=_DF)),
        health_state=HealthState(),
        oco_tasks=set(),
        static_map={"AAPL": "AAPL_US_EQ"},
    )

    with patch("alphalink.main.wh") as mock_wh:
        with (
            patch("alphalink.main.compute_features", return_value=_DF),
            patch("alphalink.main.normalize", return_value=_DF),
            patch("alphalink.main.build_input", return_value=np.zeros((1, 1))),
            patch("alphalink.main.consensus_by_ticker", return_value={"AAPL": "HOLD"}),
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_daily_loss_halt_transition.py -v
```

Expected: all 3 `FAILED` — notify fires every tick, not just on transition.

- [ ] **Step 3: Add `_prev_halt` closure variable to make_tick()**

In `alphalink/main.py`, find the line `async def tick() -> None:` inside `make_tick()`. Add one line immediately before it:

```python
    _prev_halt: list[bool] = [False]
    async def tick() -> None:
```

- [ ] **Step 4: Replace the daily-loss-halt notify block in tick()**

Find this block in `tick()` (currently around line 220):

```python
            if daily_loss_halted:
                log.warning("Daily loss halt active (%.2f%%). No new orders.", daily_loss_pct * 100)
                wh.notify(
                    "WARNING",
                    f"Daily loss halt active ({daily_loss_pct:.2%}). No new orders.",
                    category="daily-loss-halt",
                )
```

Replace with:

```python
            if daily_loss_halted and not _prev_halt[0]:
                log.warning("Daily loss halt active (%.2f%%). No new orders.", daily_loss_pct * 100)
                wh.notify(
                    "WARNING",
                    f"Daily loss halt active ({daily_loss_pct:.2%}). No new orders.",
                    category="daily-loss-halt",
                )
            _prev_halt[0] = daily_loss_halted
```

- [ ] **Step 5: Run the new tests to verify they pass**

```bash
pytest tests/unit/test_daily_loss_halt_transition.py -v
```

Expected: all 3 `PASSED`.

- [ ] **Step 6: Run full unit suite to check for regressions**

```bash
pytest tests/unit/ -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add alphalink/main.py tests/unit/test_daily_loss_halt_transition.py
git commit -m "fix: daily-loss-halt notify on transition only, not every tick (alphaLink-a73)"
```

- [ ] **Step 8: Close issue**

```bash
bd close alphaLink-a73
```
