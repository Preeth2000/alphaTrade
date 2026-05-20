# tests/unit/test_throttle.py
"""Tests for EndpointThrottle per-endpoint async rate limiter."""
from __future__ import annotations
import time
import pytest
from alphaTrade.broker.throttle import EndpointThrottle
from alphaTrade.config import T212ThrottleConfig


@pytest.mark.asyncio
async def test_acquire_returns_immediately_when_no_prior_call():
    throttle = EndpointThrottle({"orders_stop": 2.0})
    t0 = time.monotonic()
    wait = await throttle.acquire("orders_stop")
    elapsed = time.monotonic() - t0
    assert elapsed < 0.1
    assert wait == pytest.approx(0.0, abs=0.05)


@pytest.mark.asyncio
async def test_acquire_waits_min_gap_on_second_call():
    throttle = EndpointThrottle({"orders_stop": 0.1})
    await throttle.acquire("orders_stop")
    t0 = time.monotonic()
    await throttle.acquire("orders_stop")
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.08  # at least 80% of gap


@pytest.mark.asyncio
async def test_acquire_unknown_endpoint_does_not_block():
    throttle = EndpointThrottle({"orders_stop": 2.0})
    t0 = time.monotonic()
    wait = await throttle.acquire("unknown_endpoint")
    elapsed = time.monotonic() - t0
    assert elapsed < 0.1
    assert wait == 0.0


@pytest.mark.asyncio
async def test_from_t212_config_maps_fields():
    cfg = T212ThrottleConfig(
        orders_market_min_gap_secs=1.1,
        orders_stop_min_gap_secs=3.0,
        orders_limit_min_gap_secs=2.2,
        orders_cancel_min_gap_secs=1.3,
        account_cash_min_gap_secs=6.0,
        portfolio_min_gap_secs=0.9,
        orders_status_min_gap_secs=0.8,
    )
    throttle = EndpointThrottle.from_t212_config(cfg)
    assert throttle._min_gap == {
        "orders_market": 1.1,
        "orders_stop": 3.0,
        "orders_limit": 2.2,
        "orders_cancel": 1.3,
        "account_cash": 6.0,
        "portfolio": 0.9,
        "orders_status": 0.8,
    }


@pytest.mark.asyncio
async def test_different_endpoints_do_not_block_each_other():
    throttle = EndpointThrottle({"orders_stop": 0.2, "account_cash": 0.2})
    await throttle.acquire("orders_stop")
    t0 = time.monotonic()
    await throttle.acquire("account_cash")  # different endpoint — no wait
    elapsed = time.monotonic() - t0
    assert elapsed < 0.05
