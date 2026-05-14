"""Tests for ATR and VIX volatility-based position sizing."""
import pytest
from unittest.mock import patch

from alphalink.risk.sizing import compute_quantity


def test_fixed_mode_unchanged():
    qty = compute_quantity(equity=10000.0, current_price=100.0, size_pct=0.10, mode="fixed")
    assert qty == pytest.approx(10.0, rel=1e-4)


def test_atr_mode_basic():
    # risk = 10000 * 0.01 = $100; stop = 5 * 2 = $10 from entry
    # cash = 100 / 10 * 100 = $1000; qty = 1000/100 = 10
    qty = compute_quantity(
        equity=10000.0, current_price=100.0, size_pct=0.10,
        mode="atr", atr=5.0, atr_risk_pct=0.01, atr_multiplier=2.0,
    )
    assert qty == pytest.approx(10.0, rel=1e-4)


def test_atr_mode_larger_atr_gives_smaller_qty():
    qty_small = compute_quantity(equity=10000.0, current_price=100.0, size_pct=0.10,
                                  mode="atr", atr=2.0, atr_risk_pct=0.01, atr_multiplier=2.0)
    qty_large = compute_quantity(equity=10000.0, current_price=100.0, size_pct=0.10,
                                  mode="atr", atr=10.0, atr_risk_pct=0.01, atr_multiplier=2.0)
    assert qty_large < qty_small


def test_atr_mode_falls_back_to_fixed_when_atr_zero():
    qty = compute_quantity(equity=10000.0, current_price=100.0, size_pct=0.10,
                           mode="atr", atr=0.0, atr_risk_pct=0.01, atr_multiplier=2.0)
    assert qty == pytest.approx(10.0, rel=1e-4)


def test_vix_mode_at_scalar_gives_base_size():
    # VIX == vix_scalar → multiplier = 1.0 → base_size_pct
    qty = compute_quantity(
        equity=10000.0, current_price=100.0, size_pct=0.05,
        mode="vix", current_vix=20.0, vix_scalar=20.0,
        vix_base_size_pct=0.05, vix_max_size_pct=0.15,
    )
    assert qty == pytest.approx(5.0, rel=1e-4)


def test_vix_mode_high_vix_reduces_size():
    qty_low = compute_quantity(equity=10000.0, current_price=100.0, size_pct=0.05,
                               mode="vix", current_vix=10.0, vix_scalar=20.0,
                               vix_base_size_pct=0.05, vix_max_size_pct=0.15)
    qty_high = compute_quantity(equity=10000.0, current_price=100.0, size_pct=0.05,
                                mode="vix", current_vix=40.0, vix_scalar=20.0,
                                vix_base_size_pct=0.05, vix_max_size_pct=0.15)
    assert qty_high < qty_low


def test_vix_mode_capped_by_max_size_pct():
    # VIX=1, scalar=20 → multiplier=20 → way above cap; capped at 0.10
    qty = compute_quantity(
        equity=10000.0, current_price=100.0, size_pct=0.05,
        mode="vix", current_vix=1.0, vix_scalar=20.0,
        vix_base_size_pct=0.05, vix_max_size_pct=0.10,
    )
    assert qty == pytest.approx(10.0, rel=1e-4)


def test_invalid_price_raises():
    with pytest.raises(ValueError):
        compute_quantity(equity=10000.0, current_price=0.0, size_pct=0.10)
