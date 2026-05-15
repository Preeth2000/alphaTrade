"""Tests for market-hours gating in schedule_bar_close."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from alphalink.scheduler.bar_close import is_market_open, schedule_bar_close


# ---------------------------------------------------------------------------
# is_market_open
# ---------------------------------------------------------------------------

class TestIsMarketOpen:
    def _mock_cal(self, open_: bool):
        cal = MagicMock()
        cal.is_open_on_minute.return_value = open_
        return cal

    def test_returns_true_when_open(self):
        with patch("exchange_calendars.get_calendar", return_value=self._mock_cal(True)):
            assert is_market_open() is True

    def test_returns_false_when_closed(self):
        with patch("exchange_calendars.get_calendar", return_value=self._mock_cal(False)):
            assert is_market_open() is False

    def test_fails_open_when_exchange_calendars_unavailable(self):
        with patch("exchange_calendars.get_calendar", side_effect=Exception("no lib")):
            assert is_market_open() is True

    def test_accepts_explicit_now(self):
        now = datetime(2026, 5, 9, 14, 30, tzinfo=timezone.utc)  # Saturday
        with patch("exchange_calendars.get_calendar", return_value=self._mock_cal(False)):
            assert is_market_open(now) is False


# ---------------------------------------------------------------------------
# schedule_bar_close market-hours gating
# ---------------------------------------------------------------------------

class TestScheduleBarCloseMarketGating:
    """Verify tick is skipped when market closed (intraday) and fired when open."""

    def _stop_after(self, n_loops: int):
        """Return a stop_event that fires after n_loops iterations."""
        event = asyncio.Event()
        count = 0

        async def _tick():
            nonlocal count
            count += 1
            if count >= n_loops:
                event.set()

        return event, _tick, lambda: count

    @pytest.mark.asyncio
    async def test_skips_tick_when_market_closed(self):
        fired = []
        stop = asyncio.Event()

        async def callback():
            fired.append(1)

        # After one yield (sleep(0) in skip path) the call_soon fires stop_event
        asyncio.get_event_loop().call_soon(stop.set)

        with (
            patch("alphalink.scheduler.bar_close.next_bar_close",
                  side_effect=lambda *a, **kw: datetime.now(timezone.utc)),
            patch("alphalink.scheduler.bar_close.is_market_open", return_value=False),
        ):
            await schedule_bar_close("1h", callback, stop_event=stop, extended_hours=False)

        assert fired == [], "tick must not fire when market is closed"

    @pytest.mark.asyncio
    async def test_fires_tick_when_market_open(self):
        fired = []
        stop = asyncio.Event()

        async def callback():
            fired.append(1)
            stop.set()

        with (
            patch("alphalink.scheduler.bar_close.next_bar_close",
                  side_effect=lambda *a, **kw: datetime.now(timezone.utc)),
            patch("alphalink.scheduler.bar_close.is_market_open", return_value=True),
        ):
            await schedule_bar_close("1h", callback, stop_event=stop, extended_hours=False)

        assert fired == [1], "tick must fire when market is open"

    @pytest.mark.asyncio
    async def test_extended_hours_bypasses_gate(self):
        fired = []
        stop = asyncio.Event()

        async def callback():
            fired.append(1)
            stop.set()

        with (
            patch("alphalink.scheduler.bar_close.next_bar_close",
                  side_effect=lambda *a, **kw: datetime.now(timezone.utc)),
            patch("alphalink.scheduler.bar_close.is_market_open", return_value=False),
        ):
            await schedule_bar_close("1h", callback, stop_event=stop, extended_hours=True)

        assert fired == [1], "tick must fire regardless of market hours when extended_hours=True"

    @pytest.mark.asyncio
    async def test_daily_interval_not_gated(self):
        """1d interval skips is_market_open check entirely."""
        fired = []
        stop = asyncio.Event()

        async def callback():
            fired.append(1)
            stop.set()

        with (
            patch("alphalink.scheduler.bar_close.next_bar_close",
                  side_effect=lambda *a, **kw: datetime.now(timezone.utc)),
            patch("alphalink.scheduler.bar_close.is_market_open", return_value=False),
        ):
            await schedule_bar_close("1d", callback, stop_event=stop, extended_hours=False)

        assert fired == [1], "daily interval must not be blocked by market-hours gate"
