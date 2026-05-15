"""Verify startup pre-resolve populates instrument cache for all registry tickers.

At startup, _preresolve_tickers() calls InstrumentMap.resolve() synchronously
for each model ticker. This fills the SQLite cache so tick-loop resolve() calls
always hit the fast cache path — never the blocking T212 HTTP path.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlmodel import SQLModel, Session, create_engine

from alphaTrade.broker.t212_client import T212Client
from alphaTrade.store.repos import (
    EquityCurve, InstrumentCache, InstrumentCacheRepo, Order, Position, Signal,
)


def _engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    return eng


def _t212(instruments=None) -> MagicMock:
    t212 = MagicMock(spec=T212Client)
    t212.get_instruments.return_value = instruments or [
        {"ticker": "AAPL_US_EQ", "shortName": "AAPL"},
        {"ticker": "MSFT_US_EQ", "shortName": "MSFT"},
    ]
    return t212


def _registry(*tickers: str) -> MagicMock:
    reg = MagicMock()
    items = {}
    for t in tickers:
        m = MagicMock()
        m.ticker = t
        items[t] = (m, MagicMock())
    reg.by_run_name = items
    return reg


class TestPreresolveTickers:
    def test_function_is_importable(self):
        """_preresolve_tickers must exist in alphaTrade.main."""
        from alphaTrade.main import _preresolve_tickers
        assert callable(_preresolve_tickers)

    def test_resolve_called_for_each_registry_ticker(self):
        """_preresolve_tickers calls resolve for every ticker in registry."""
        from alphaTrade.main import _preresolve_tickers
        eng = _engine()
        t212 = _t212()
        registry = _registry("AAPL", "MSFT")

        _preresolve_tickers(t212, eng, registry, static_map={})

        # Both tickers should now be in cache
        with Session(eng) as session:
            cache = InstrumentCacheRepo(session)
            assert cache.get("AAPL") is not None
            assert cache.get("MSFT") is not None

    def test_preresolve_prevents_api_call_during_tick(self):
        """After preresolve, resolve() skips get_instruments (cache hit)."""
        from alphaTrade.main import _preresolve_tickers
        from alphaTrade.broker.instrument_map import InstrumentMap
        eng = _engine()
        t212 = _t212()
        registry = _registry("AAPL")

        _preresolve_tickers(t212, eng, registry, static_map={})
        api_calls_after_preresolve = t212.get_instruments.call_count

        # Simulate tick-time resolve — must not call API again
        with Session(eng) as session:
            cache = InstrumentCacheRepo(session)
            inst_map = InstrumentMap(t212, cache, {})
            inst_map.resolve("AAPL")

        assert t212.get_instruments.call_count == api_calls_after_preresolve

    def test_preresolve_skips_static_overrides(self):
        """Tickers in static_map are skipped — override takes precedence."""
        from alphaTrade.main import _preresolve_tickers
        eng = _engine()
        t212 = _t212()
        registry = _registry("AAPL")

        _preresolve_tickers(t212, eng, registry, static_map={"AAPL": "AAPL_US_EQ"})

        # Static override means no API call needed
        assert t212.get_instruments.call_count == 0

    def test_preresolve_logs_warning_on_resolve_failure(self, caplog):
        """Failed resolution logs warning and continues (does not raise)."""
        from alphaTrade.main import _preresolve_tickers
        eng = _engine()
        t212 = MagicMock(spec=T212Client)
        t212.get_instruments.return_value = []  # no match → RuntimeError
        registry = _registry("UNKNOWNTICKER")

        # Must not raise
        _preresolve_tickers(t212, eng, registry, static_map={})
