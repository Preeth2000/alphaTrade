"""Tests for retirement config API endpoints."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from alphaTrade.api.app import create_app
from alphaTrade.config import ModelRetirementConfig, ModelRetirementOverride, ModelOverride, Settings
from alphaTrade.health import HealthState
from alphaTrade.store.db import run_migrations
from alphaTrade.store.repos import BotSettings, BotSettingsRepo, ModelPerformance, ModelPerformanceRepo


def _make_engine(tmp_path):
    db = tmp_path / "test.db"
    run_migrations(db)
    return create_engine(f"sqlite:///{db}")


def _make_settings(tmp_path) -> Settings:
    overrides = tmp_path / "overrides.yaml"
    overrides.write_text("")
    import os
    os.environ.setdefault("ALPHATRADE_API_KEY", "test-key")
    return Settings(
        overrides_path=overrides,
        state_db_path=tmp_path / "state.db",
    )


def _make_client(engine, settings):
    app = create_app(engine, HealthState(), settings=settings)
    return TestClient(app, headers={"X-API-Key": "test-key"})


class TestGlobalRetirementConfig:
    def test_get_returns_yaml_defaults(self, tmp_path):
        engine = _make_engine(tmp_path)
        settings = _make_settings(tmp_path)
        client = _make_client(engine, settings)
        r = client.get("/api/v1/retirement/config")
        assert r.status_code == 200
        data = r.json()
        assert data["enabled"] is False
        assert data["lookback_trades"] == 20
        assert data["min_win_rate"] == 0.4
        assert data["min_rolling_pnl"] == -500.0
        assert data["min_trades_before_evaluation"] == 5
        assert data["min_evaluation_period"] == "30d"

    def test_patch_updates_in_memory_and_db(self, tmp_path):
        engine = _make_engine(tmp_path)
        settings = _make_settings(tmp_path)
        client = _make_client(engine, settings)
        r = client.patch("/api/v1/retirement/config", json={"enabled": True, "min_win_rate": 0.55})
        assert r.status_code == 200
        data = r.json()
        assert data["enabled"] is True
        assert data["min_win_rate"] == 0.55
        # in-memory mutated immediately
        assert settings.risk.model_retirement.enabled is True
        assert settings.risk.model_retirement.min_win_rate == 0.55
        # persisted to DB
        with Session(engine) as s:
            bs = BotSettingsRepo(s).get()
            assert bs.retirement_enabled is True
            assert bs.retirement_min_win_rate == 0.55

    def test_patch_invalid_period_returns_422(self, tmp_path):
        engine = _make_engine(tmp_path)
        settings = _make_settings(tmp_path)
        client = _make_client(engine, settings)
        r = client.patch("/api/v1/retirement/config", json={"min_evaluation_period": "1month"})
        assert r.status_code == 422


class TestPerModelRetirementConfig:
    def test_get_returns_null_overrides_and_effective_globals(self, tmp_path):
        engine = _make_engine(tmp_path)
        settings = _make_settings(tmp_path)
        client = _make_client(engine, settings)
        r = client.get("/api/v1/models/my_model/retirement")
        assert r.status_code == 200
        data = r.json()
        assert data["run_name"] == "my_model"
        assert data["enabled"] is None
        assert data["effective_enabled"] is False  # global default
        assert data["effective_min_win_rate"] == 0.4

    def test_patch_sets_override_and_persists_to_db(self, tmp_path):
        engine = _make_engine(tmp_path)
        settings = _make_settings(tmp_path)
        client = _make_client(engine, settings)
        r = client.patch("/api/v1/models/my_model/retirement", json={"enabled": False, "min_win_rate": 0.3})
        assert r.status_code == 200
        data = r.json()
        assert data["enabled"] is False
        assert data["min_win_rate"] == 0.3
        assert data["effective_enabled"] is False
        assert data["effective_min_win_rate"] == 0.3
        # persisted to DB
        with Session(engine) as s:
            from alphaTrade.store.repos import ModelOverrideRepo
            rec = ModelOverrideRepo(s).get("my_model")
            assert rec is not None
            assert rec.retirement_enabled is False
            assert rec.retirement_min_win_rate == 0.3
        # YAML not touched
        raw = settings.overrides_path.read_text()
        assert "my_model" not in raw

    def test_delete_clears_retirement_fields_in_db(self, tmp_path):
        engine = _make_engine(tmp_path)
        settings = _make_settings(tmp_path)
        client = _make_client(engine, settings)
        client.patch("/api/v1/models/my_model/retirement", json={"enabled": False})
        r = client.delete("/api/v1/models/my_model/retirement")
        assert r.status_code == 200
        with Session(engine) as s:
            from alphaTrade.store.repos import ModelOverrideRepo
            rec = ModelOverrideRepo(s).get("my_model")
            assert rec is not None  # row still exists, just fields nulled
            assert rec.retirement_enabled is None
            assert rec.retirement_lookback_trades is None
            assert rec.retirement_min_win_rate is None
            assert rec.retirement_min_rolling_pnl is None
            assert rec.retirement_min_trades_before_evaluation is None
            assert rec.retirement_min_evaluation_period is None


class TestUnretire:
    def test_unretire_clears_retired_flag_and_resets_stats(self, tmp_path):
        engine = _make_engine(tmp_path)
        settings = _make_settings(tmp_path)
        client = _make_client(engine, settings)
        # seed a retired model
        with Session(engine) as s:
            perf = ModelPerformanceRepo(s).get_or_create("old_model")
            perf.retired = True
            perf.retired_at = datetime.utcnow()
            perf.trade_count = 10
            perf.win_count = 3
            perf.rolling_pnl = -300.0
            perf.rolling_trades_json = [-50, -100]
            perf.first_trade_at = datetime.utcnow()
            s.add(perf)
            s.commit()
        r = client.post("/api/v1/models/old_model/unretire")
        assert r.status_code == 200
        with Session(engine) as s:
            perf = ModelPerformanceRepo(s).get_or_create("old_model")
            assert perf.retired is False
            assert perf.retired_at is None
            assert perf.trade_count == 0
            assert perf.win_count == 0
            assert perf.rolling_pnl == 0.0
            assert perf.rolling_trades_json == []
            assert perf.first_trade_at is None

    def test_unretire_nonexistent_model_returns_404(self, tmp_path):
        engine = _make_engine(tmp_path)
        settings = _make_settings(tmp_path)
        client = _make_client(engine, settings)
        r = client.post("/api/v1/models/ghost_model/unretire")
        assert r.status_code == 404
