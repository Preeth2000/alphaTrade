"""Tests for GET /models (ModelSummary) and /models/{run_name}/overrides endpoints."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from alphaTrade.api.app import create_app
from alphaTrade.health import HealthState
from alphaTrade.store.db import run_migrations
from alphaTrade.store.repos import (
    InstrumentCache,
    ModelOverrideRecord,
    ModelOverrideRepo,
    ModelPerformance,
    ModelPerformanceRepo,
    Signal,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine(tmp_path):
    db = tmp_path / "test.db"
    run_migrations(db)
    return create_engine(f"sqlite:///{db}")


def _make_manifest(run_name="aapl_v1", ticker="AAPL", interval="1d", arch="mlp", n_features=14):
    m = MagicMock()
    m.run_name = run_name
    m.ticker = ticker
    m.interval = interval
    m.model_arch = arch
    m.n_features = n_features
    return m


def _make_registry(*manifests):
    registry = MagicMock()
    registry.by_run_name = {m.run_name: (m, MagicMock()) for m in manifests}
    return registry


def _client(engine, registry=None):
    app = create_app(engine, HealthState(), registry=registry)
    return TestClient(app)


# ---------------------------------------------------------------------------
# GET /models — no registry
# ---------------------------------------------------------------------------

class TestListModelsNoRegistry:
    def test_empty_when_no_registry_no_perf(self, tmp_path):
        engine = _make_engine(tmp_path)
        resp = _client(engine).get("/api/v1/models")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_shows_db_only_models_when_no_registry(self, tmp_path):
        engine = _make_engine(tmp_path)
        with Session(engine) as s:
            ModelPerformanceRepo(s).get_or_create("old_model")
        resp = _client(engine).get("/api/v1/models")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["run_name"] == "old_model"
        assert data[0]["active"] is False


# ---------------------------------------------------------------------------
# GET /models — with registry
# ---------------------------------------------------------------------------

class TestListModelsWithRegistry:
    def test_active_model_from_registry(self, tmp_path):
        engine = _make_engine(tmp_path)
        manifest = _make_manifest("aapl_v1", ticker="AAPL", interval="1d")
        registry = _make_registry(manifest)

        resp = _client(engine, registry).get("/api/v1/models")
        data = resp.json()

        assert len(data) == 1
        row = data[0]
        assert row["run_name"] == "aapl_v1"
        assert row["ticker"] == "AAPL"
        assert row["interval"] == "1d"
        assert row["model_arch"] == "mlp"
        assert row["n_features"] == 14
        assert row["active"] is True

    def test_perf_stats_merged_for_active_model(self, tmp_path):
        engine = _make_engine(tmp_path)
        with Session(engine) as s:
            perf = ModelPerformanceRepo(s).get_or_create("aapl_v1")
            perf.trade_count = 10
            perf.win_count = 6
            perf.rolling_pnl = 250.0
            ModelPerformanceRepo(s).update(perf)

        manifest = _make_manifest("aapl_v1")
        registry = _make_registry(manifest)
        resp = _client(engine, registry).get("/api/v1/models")
        row = resp.json()[0]

        assert row["trade_count"] == 10
        assert row["win_count"] == 6
        assert row["rolling_pnl"] == pytest.approx(250.0)

    def test_zero_perf_for_model_with_no_trades(self, tmp_path):
        engine = _make_engine(tmp_path)
        manifest = _make_manifest("fresh_model")
        registry = _make_registry(manifest)

        resp = _client(engine, registry).get("/api/v1/models")
        row = resp.json()[0]

        assert row["trade_count"] == 0
        assert row["active"] is True

    def test_db_only_model_marked_inactive(self, tmp_path):
        engine = _make_engine(tmp_path)
        with Session(engine) as s:
            ModelPerformanceRepo(s).get_or_create("retired_model")

        # registry only has aapl_v1, not retired_model
        registry = _make_registry(_make_manifest("aapl_v1"))
        resp = _client(engine, registry).get("/api/v1/models")
        data = resp.json()

        names = {r["run_name"]: r for r in data}
        assert names["aapl_v1"]["active"] is True
        assert names["retired_model"]["active"] is False

    def test_resolved_ticker_null_when_no_cache(self, tmp_path):
        engine = _make_engine(tmp_path)
        registry = _make_registry(_make_manifest("aapl_v1"))
        resp = _client(engine, registry).get("/api/v1/models")
        assert resp.json()[0]["resolved_ticker"] is None

    def test_resolved_ticker_from_instrument_cache(self, tmp_path):
        engine = _make_engine(tmp_path)
        with Session(engine) as s:
            s.add(Signal(run_name="aapl_v1", ticker="AAPL", signal="BUY", model_count=1))
            s.add(InstrumentCache(yf_ticker="AAPL", t212_ticker="AAPL_US_EQ",
                                  resolved_at=datetime.utcnow()))
            s.commit()

        registry = _make_registry(_make_manifest("aapl_v1"))
        resp = _client(engine, registry).get("/api/v1/models")
        assert resp.json()[0]["resolved_ticker"] == "AAPL_US_EQ"

    def test_resolved_ticker_from_db_override_takes_precedence(self, tmp_path):
        engine = _make_engine(tmp_path)
        with Session(engine) as s:
            s.add(Signal(run_name="aapl_v1", ticker="AAPL", signal="BUY", model_count=1))
            s.add(InstrumentCache(yf_ticker="AAPL", t212_ticker="AAPL_US_EQ",
                                  resolved_at=datetime.utcnow()))
            ModelOverrideRepo(s).upsert(ModelOverrideRecord(
                run_name="aapl_v1", broker_ticker="AAPL_CUSTOM"
            ))

        registry = _make_registry(_make_manifest("aapl_v1"))
        resp = _client(engine, registry).get("/api/v1/models")
        # explicit override beats cached value
        assert resp.json()[0]["resolved_ticker"] == "AAPL_CUSTOM"


# ---------------------------------------------------------------------------
# GET /models/overrides (bulk)
# ---------------------------------------------------------------------------

class TestGetAllOverrides:
    def test_empty_when_no_overrides(self, tmp_path):
        engine = _make_engine(tmp_path)
        resp = _client(engine).get("/api/v1/models/overrides")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_only_models_with_override_rows(self, tmp_path):
        engine = _make_engine(tmp_path)
        with Session(engine) as s:
            ModelOverrideRepo(s).upsert(ModelOverrideRecord(run_name="aapl_v1", size_pct=0.05))
            ModelOverrideRepo(s).upsert(ModelOverrideRecord(run_name="tsla_v1", enabled=False))

        resp = _client(engine).get("/api/v1/models/overrides")
        data = resp.json()
        assert len(data) == 2
        names = {r["run_name"] for r in data}
        assert names == {"aapl_v1", "tsla_v1"}

    def test_fields_present_on_each_row(self, tmp_path):
        engine = _make_engine(tmp_path)
        with Session(engine) as s:
            ModelOverrideRepo(s).upsert(ModelOverrideRecord(
                run_name="aapl_v1", broker_ticker="AAPL_US_EQ", size_pct=0.05
            ))

        resp = _client(engine).get("/api/v1/models/overrides")
        row = resp.json()[0]
        assert row["run_name"] == "aapl_v1"
        assert row["broker_ticker"] == "AAPL_US_EQ"
        assert row["size_pct"] == pytest.approx(0.05)

    def test_not_shadowed_by_parameterised_route(self, tmp_path):
        """GET /models/overrides must not be caught by /models/{run_name}/overrides."""
        engine = _make_engine(tmp_path)
        resp = _client(engine).get("/api/v1/models/overrides")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# GET /models/{run_name}/overrides
# ---------------------------------------------------------------------------

class TestGetOverrides:
    def test_returns_empty_record_when_no_override(self, tmp_path):
        engine = _make_engine(tmp_path)
        resp = _client(engine).get("/api/v1/models/aapl_v1/overrides")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_name"] == "aapl_v1"
        assert data["broker_ticker"] is None
        assert data["size_pct"] is None
        assert data["enabled"] is None

    def test_returns_stored_override(self, tmp_path):
        engine = _make_engine(tmp_path)
        with Session(engine) as s:
            ModelOverrideRepo(s).upsert(ModelOverrideRecord(
                run_name="aapl_v1",
                enabled=False,
                broker_ticker="AAPL_US_EQ",
                size_pct=0.05,
                stop_loss_pct=0.03,
                take_profit_pct=0.07,
                cooldown_bars=5,
            ))

        resp = _client(engine).get("/api/v1/models/aapl_v1/overrides")
        data = resp.json()

        assert data["enabled"] is False
        assert data["broker_ticker"] == "AAPL_US_EQ"
        assert data["size_pct"] == pytest.approx(0.05)
        assert data["stop_loss_pct"] == pytest.approx(0.03)
        assert data["take_profit_pct"] == pytest.approx(0.07)
        assert data["cooldown_bars"] == 5

    def test_resolved_ticker_from_broker_ticker_override(self, tmp_path):
        engine = _make_engine(tmp_path)
        with Session(engine) as s:
            ModelOverrideRepo(s).upsert(ModelOverrideRecord(
                run_name="aapl_v1", broker_ticker="AAPL_US_EQ"
            ))

        resp = _client(engine).get("/api/v1/models/aapl_v1/overrides")
        assert resp.json()["resolved_ticker"] == "AAPL_US_EQ"

    def test_resolved_ticker_from_cache_when_no_broker_ticker(self, tmp_path):
        engine = _make_engine(tmp_path)
        with Session(engine) as s:
            s.add(Signal(run_name="aapl_v1", ticker="AAPL", signal="BUY", model_count=1))
            s.add(InstrumentCache(yf_ticker="AAPL", t212_ticker="AAPL_US_EQ",
                                  resolved_at=datetime.utcnow()))
            s.commit()

        resp = _client(engine).get("/api/v1/models/aapl_v1/overrides")
        assert resp.json()["resolved_ticker"] == "AAPL_US_EQ"

    def test_resolved_ticker_null_when_never_resolved(self, tmp_path):
        engine = _make_engine(tmp_path)
        resp = _client(engine).get("/api/v1/models/aapl_v1/overrides")
        assert resp.json()["resolved_ticker"] is None


# ---------------------------------------------------------------------------
# PUT /models/{run_name}/overrides
# ---------------------------------------------------------------------------

class TestPutOverrides:
    def test_creates_override(self, tmp_path):
        engine = _make_engine(tmp_path)
        resp = _client(engine).put(
            "/api/v1/models/aapl_v1/overrides",
            json={"broker_ticker": "AAPL_US_EQ", "size_pct": 0.05},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["broker_ticker"] == "AAPL_US_EQ"
        assert data["size_pct"] == pytest.approx(0.05)

    def test_partial_update_preserves_existing_fields(self, tmp_path):
        engine = _make_engine(tmp_path)
        with Session(engine) as s:
            ModelOverrideRepo(s).upsert(ModelOverrideRecord(
                run_name="aapl_v1", broker_ticker="AAPL_US_EQ", size_pct=0.05
            ))

        # only update size_pct — broker_ticker should be unchanged
        resp = _client(engine).put(
            "/api/v1/models/aapl_v1/overrides",
            json={"size_pct": 0.10},
        )
        data = resp.json()
        assert data["broker_ticker"] == "AAPL_US_EQ"
        assert data["size_pct"] == pytest.approx(0.10)

    def test_null_field_clears_override(self, tmp_path):
        engine = _make_engine(tmp_path)
        with Session(engine) as s:
            ModelOverrideRepo(s).upsert(ModelOverrideRecord(
                run_name="aapl_v1", broker_ticker="AAPL_US_EQ"
            ))

        resp = _client(engine).put(
            "/api/v1/models/aapl_v1/overrides",
            json={"broker_ticker": None},
        )
        assert resp.json()["broker_ticker"] is None

    def test_disable_model(self, tmp_path):
        engine = _make_engine(tmp_path)
        resp = _client(engine).put(
            "/api/v1/models/aapl_v1/overrides",
            json={"enabled": False},
        )
        assert resp.json()["enabled"] is False

    def test_persisted_to_db(self, tmp_path):
        engine = _make_engine(tmp_path)
        _client(engine).put(
            "/api/v1/models/aapl_v1/overrides",
            json={"size_pct": 0.07},
        )
        with Session(engine) as s:
            record = ModelOverrideRepo(s).get("aapl_v1")
        assert record is not None
        assert record.size_pct == pytest.approx(0.07)

    def test_empty_body_is_noop(self, tmp_path):
        engine = _make_engine(tmp_path)
        with Session(engine) as s:
            ModelOverrideRepo(s).upsert(ModelOverrideRecord(
                run_name="aapl_v1", size_pct=0.05
            ))

        resp = _client(engine).put("/api/v1/models/aapl_v1/overrides", json={})
        assert resp.json()["size_pct"] == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# DELETE /models/{run_name}/overrides
# ---------------------------------------------------------------------------

class TestDeleteOverrides:
    def test_delete_existing_returns_200(self, tmp_path):
        engine = _make_engine(tmp_path)
        with Session(engine) as s:
            ModelOverrideRepo(s).upsert(ModelOverrideRecord(run_name="aapl_v1", size_pct=0.05))

        resp = _client(engine).delete("/api/v1/models/aapl_v1/overrides")
        assert resp.status_code == 200
        assert resp.json() == {"deleted": True, "run_name": "aapl_v1"}

    def test_delete_removes_from_db(self, tmp_path):
        engine = _make_engine(tmp_path)
        with Session(engine) as s:
            ModelOverrideRepo(s).upsert(ModelOverrideRecord(run_name="aapl_v1", size_pct=0.05))

        _client(engine).delete("/api/v1/models/aapl_v1/overrides")

        with Session(engine) as s:
            assert ModelOverrideRepo(s).get("aapl_v1") is None

    def test_delete_nonexistent_returns_404(self, tmp_path):
        engine = _make_engine(tmp_path)
        resp = _client(engine).delete("/api/v1/models/nonexistent/overrides")
        assert resp.status_code == 404

    def test_get_after_delete_returns_empty_record(self, tmp_path):
        engine = _make_engine(tmp_path)
        with Session(engine) as s:
            ModelOverrideRepo(s).upsert(ModelOverrideRecord(
                run_name="aapl_v1", broker_ticker="AAPL_US_EQ", size_pct=0.05
            ))

        _client(engine).delete("/api/v1/models/aapl_v1/overrides")
        resp = _client(engine).get("/api/v1/models/aapl_v1/overrides")
        data = resp.json()
        assert data["broker_ticker"] is None
        assert data["size_pct"] is None
