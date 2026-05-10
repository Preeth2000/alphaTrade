"""Tests for order idempotency via deterministic client_order_id."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import SQLModel, Session, create_engine

from alphalink.broker.orders import make_client_order_id, submit_order
from alphalink.store.repos import Order, OrderRepo


@pytest.fixture
def engine():
    e = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(e)
    return e


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


# ---------------------------------------------------------------------------
# make_client_order_id
# ---------------------------------------------------------------------------

class TestMakeClientOrderId:
    def test_deterministic(self):
        a = make_client_order_id("aapl_mlp", "AAPL_US_EQ", "2026-05-09T16:00:00Z", "BUY")
        b = make_client_order_id("aapl_mlp", "AAPL_US_EQ", "2026-05-09T16:00:00Z", "BUY")
        assert a == b

    def test_16_chars(self):
        cid = make_client_order_id("run", "TICK", "2026-01-01T00:00:00Z", "SELL")
        assert len(cid) == 16

    def test_hex_only(self):
        cid = make_client_order_id("run", "TICK", "2026-01-01T00:00:00Z", "BUY")
        assert all(c in "0123456789abcdef" for c in cid)

    def test_different_side_different_id(self):
        buy = make_client_order_id("run", "TICK", "2026-01-01T00:00:00Z", "BUY")
        sell = make_client_order_id("run", "TICK", "2026-01-01T00:00:00Z", "SELL")
        assert buy != sell

    def test_different_bar_different_id(self):
        a = make_client_order_id("run", "TICK", "2026-01-01T00:00:00Z", "BUY")
        b = make_client_order_id("run", "TICK", "2026-01-02T00:00:00Z", "BUY")
        assert a != b


# ---------------------------------------------------------------------------
# submit_order idempotency
# ---------------------------------------------------------------------------

class TestSubmitOrderIdempotency:
    def test_skips_duplicate_client_order_id(self, session):
        """Second call with same client_order_id returns existing order, no HTTP."""
        t212 = MagicMock()
        t212.place_market_order.return_value = {"id": "t212-001", "fillPrice": 175.0, "status": "FILLED"}

        repo = OrderRepo(session)
        cid = "abcdef0123456789"

        # First call
        result1 = submit_order(
            t212=t212,
            instrument_ticker="AAPL_US_EQ",
            side="BUY",
            quantity=1.0,
            order_repo=repo,
            client_order_id=cid,
        )
        assert t212.place_market_order.call_count == 1

        # Second call — same cid
        result2 = submit_order(
            t212=t212,
            instrument_ticker="AAPL_US_EQ",
            side="BUY",
            quantity=1.0,
            order_repo=repo,
            client_order_id=cid,
        )
        assert t212.place_market_order.call_count == 1  # not called again
        assert result2.get("skipped_duplicate") is True

    def test_different_cid_submits(self, session):
        """Different client_order_ids each submit independently."""
        t212 = MagicMock()
        t212.place_market_order.return_value = {"id": "t212-002", "fillPrice": 175.0, "status": "FILLED"}

        repo = OrderRepo(session)

        submit_order(t212, "AAPL_US_EQ", "BUY", 1.0, order_repo=repo, client_order_id="aaaa0000aaaa0000")
        submit_order(t212, "AAPL_US_EQ", "BUY", 1.0, order_repo=repo, client_order_id="bbbb1111bbbb1111")

        assert t212.place_market_order.call_count == 2

    def test_no_repo_no_idempotency_check(self):
        """Backward compat: no order_repo → no idempotency, always submits."""
        t212 = MagicMock()
        t212.place_market_order.return_value = {"id": "x", "status": "FILLED"}

        submit_order(t212, "AAPL_US_EQ", "BUY", 1.0)
        submit_order(t212, "AAPL_US_EQ", "BUY", 1.0)

        assert t212.place_market_order.call_count == 2
