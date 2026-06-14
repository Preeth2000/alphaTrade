"""Bar-close scheduler. Uses exchange_calendars for daily/weekly market-aware scheduling."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Awaitable

log = logging.getLogger(__name__)

_SAFETY_MARGIN: dict[str, int] = {
    "1m":  5,
    "5m":  5,
    "15m": 5,
    "1h":  10,
    "1d":  30,
    "1wk": 30,
}

_INTERVAL_SECONDS: dict[str, int] = {
    "1m":   60,
    "5m":   300,
    "15m":  900,
    "1h":   3600,
    "1d":   86400,
    "1wk":  604800,
}

# Intervals that need market-calendar awareness (skip weekends/holidays)
_CALENDAR_INTERVALS = {"1d", "1wk"}
# Intraday intervals subject to market-hours gating
_INTRADAY_INTERVALS = {"1m", "5m", "15m", "1h"}
_EXCHANGE = "XNYS"  # NYSE — covers most US equities


def _next_intraday_bar_close(interval: str, now: datetime) -> datetime:
    """Next bar close via modular arithmetic (intraday intervals)."""
    period = _INTERVAL_SECONDS[interval]
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    elapsed = (now - epoch).total_seconds()
    bars_elapsed = int(elapsed // period)
    next_close = epoch + timedelta(seconds=(bars_elapsed + 1) * period)
    return next_close + timedelta(seconds=_SAFETY_MARGIN.get(interval, 10))


def _next_daily_bar_close(now: datetime) -> datetime:
    """Next NYSE trading day close (4pm ET = 21:00 UTC, approx) after current time."""
    try:
        import exchange_calendars as xcals
        cal = xcals.get_calendar(_EXCHANGE)
        today_utc = now.date()

        # Find next session close on or after now
        # Check today's close first, then walk forward
        for offset in range(10):
            check_date = today_utc + timedelta(days=offset)
            check_str = check_date.isoformat()
            if not cal.is_session(check_str):
                continue
            close_ts = cal.session_close(check_str)
            # close_ts is a pandas Timestamp — convert to UTC datetime
            close_utc = close_ts.to_pydatetime().astimezone(timezone.utc)
            margin = timedelta(seconds=_SAFETY_MARGIN["1d"])
            if close_utc + margin > now:
                return close_utc + margin

    except Exception as exc:
        log.error(
            "exchange_calendars daily scheduling failed — falling back to UTC-midnight arithmetic "
            "(daily/weekly bars will fire at wrong time). Error: %s", exc,
        )

    # Fallback: simple daily arithmetic if exchange_calendars unavailable
    return _next_intraday_bar_close("1d", now)


def _next_weekly_bar_close(now: datetime) -> datetime:
    """Next NYSE weekly close (Friday 4pm ET)."""
    try:
        import exchange_calendars as xcals
        cal = xcals.get_calendar(_EXCHANGE)
        today_utc = now.date()

        # Find next Friday that is a trading session
        for offset in range(14):
            check_date = today_utc + timedelta(days=offset)
            if check_date.weekday() != 4:  # 4 = Friday
                continue
            check_str = check_date.isoformat()
            if not cal.is_session(check_str):
                continue
            close_ts = cal.session_close(check_str)
            close_utc = close_ts.to_pydatetime().astimezone(timezone.utc)
            margin = timedelta(seconds=_SAFETY_MARGIN["1wk"])
            if close_utc + margin > now:
                return close_utc + margin

    except Exception as exc:
        log.error(
            "exchange_calendars weekly scheduling failed — falling back to UTC-midnight arithmetic "
            "(weekly bars will fire at wrong time). Error: %s", exc,
        )

    return _next_intraday_bar_close("1wk", now)


def is_market_open(now: datetime | None = None) -> bool:
    """Return True if NYSE is currently within regular trading hours.

    Falls back to True if exchange_calendars is unavailable so ticks are never
    silently suppressed when the dependency is missing.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    try:
        import pandas as pd
        import exchange_calendars as xcals
        cal = xcals.get_calendar(_EXCHANGE)
        ts = pd.Timestamp(now) if now.tzinfo is not None else pd.Timestamp(now, tz="UTC")
        return bool(cal.is_open_on_minute(ts))
    except Exception:
        return True  # fail open


def next_bar_close(interval: str, now: datetime | None = None) -> datetime:
    """Return UTC datetime of next bar close, market-calendar-aware for daily/weekly."""
    if now is None:
        now = datetime.now(timezone.utc)
    if interval not in _INTERVAL_SECONDS:
        raise ValueError(f"Unsupported interval: {interval!r}")
    if interval == "1d":
        return _next_daily_bar_close(now)
    if interval == "1wk":
        return _next_weekly_bar_close(now)
    return _next_intraday_bar_close(interval, now)


async def schedule_bar_close(
    interval: str,
    callback: Callable[[], Awaitable[None]],
    stop_event: asyncio.Event | None = None,
    extended_hours: bool = False,
) -> None:
    """Loop forever, calling callback each bar close for the given interval.

    If stop_event is set, exits cleanly after finishing the current tick.
    Intraday ticks (1m/5m/15m/1h) are skipped outside NYSE regular hours
    unless extended_hours=True.
    """
    while True:
        if stop_event is not None and stop_event.is_set():
            return
        target = next_bar_close(interval)
        now = datetime.now(timezone.utc)
        wait = (target - now).total_seconds()
        if wait > 0:
            if stop_event is not None:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=wait)
                    return  # stop signalled during sleep
                except asyncio.TimeoutError:
                    pass
            else:
                await asyncio.sleep(wait)
        if interval in _INTRADAY_INTERVALS and not extended_hours:
            if not is_market_open():
                log.debug("Market closed — skipping %s tick", interval)
                await asyncio.sleep(0)  # yield so event loop can service other tasks
                continue
        await callback()  # finish current tick before checking stop again
