"""Tests for Alembic migration: upgrade head on fresh DB creates correct schema."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


class TestAlembicMigrations:
    def test_upgrade_head_creates_all_tables(self, tmp_path):
        """alembic upgrade head on empty DB produces all expected tables."""
        from alphaTrade.store.db import run_migrations
        db_path = tmp_path / "test.db"
        run_migrations(db_path)

        conn = sqlite3.connect(db_path)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()

        assert "signal" in tables
        assert "order" in tables
        assert "position" in tables
        assert "equitycurve" in tables
        assert "instrumentcache" in tables

    def test_upgrade_idempotent(self, tmp_path):
        """Running upgrade head twice does not error."""
        from alphaTrade.store.db import run_migrations
        db_path = tmp_path / "test.db"
        run_migrations(db_path)
        run_migrations(db_path)  # second call must not raise

    def test_order_table_has_client_order_id(self, tmp_path):
        """client_order_id column present after migration."""
        from alphaTrade.store.db import run_migrations
        db_path = tmp_path / "test.db"
        run_migrations(db_path)

        conn = sqlite3.connect(db_path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info('order')").fetchall()}
        conn.close()

        assert "client_order_id" in cols

    def test_get_engine_uses_migrations_not_create_all(self, tmp_path, monkeypatch):
        """get_engine calls run_migrations, not SQLModel.create_all."""
        from sqlmodel import SQLModel
        calls = []
        monkeypatch.setattr(SQLModel.metadata, "create_all", lambda *a, **kw: calls.append(1))

        from alphaTrade.store import db as db_mod
        monkeypatch.setattr(db_mod, "_engine", None)

        from alphaTrade.store.db import get_engine
        get_engine(f"sqlite:///{tmp_path / 'test.db'}")

        assert calls == [], "create_all must not be called when Alembic is used"

    def test_model_override_has_retirement_and_backtest_cols(self, tmp_path):
        """model_override table has retirement and backtest config columns after migration."""
        from alphaTrade.store.db import run_migrations
        db_path = tmp_path / "test.db"
        run_migrations(db_path)
        conn = sqlite3.connect(db_path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(model_override)").fetchall()}
        conn.close()
        expected = {
            "retirement_enabled", "retirement_lookback_trades", "retirement_min_win_rate",
            "retirement_min_rolling_pnl", "retirement_min_trades_before_evaluation",
            "retirement_min_evaluation_period", "backtest_disabled", "backtest_cron",
            "backtest_lookback_days",
        }
        assert expected <= cols

    def test_botsettings_has_risk_and_backtest_cols(self, tmp_path):
        """botsettings table has risk sizing and backtest config columns after migration."""
        from alphaTrade.store.db import run_migrations
        db_path = tmp_path / "test.db"
        run_migrations(db_path)
        conn = sqlite3.connect(db_path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(botsettings)").fetchall()}
        conn.close()
        expected = {
            "sizing_mode", "portfolio_mode", "order_stale_window_multiplier",
            "order_queue_max_depth", "balanced_max_sector_pct",
            "unbalanced_max_per_sector", "unbalanced_sector_overrides",
            "atr_risk_pct", "atr_multiplier",
            "vix_base_size_pct", "vix_scalar", "vix_max_size_pct",
            "backtest_slippage_bps", "backtest_commission_per_trade",
            "backtest_initial_equity", "backtest_default_size_pct",
            "backtest_sl_pct", "backtest_tp_pct", "backtest_schedule_enabled",
            "backtest_cron", "backtest_lookback_days", "backtest_simulate_oco_lag",
            "backtest_oco_stop_gap_secs", "backtest_oco_limit_gap_secs",
        }
        assert expected <= cols
