"""Tests for Alembic migration: upgrade head on fresh DB creates correct schema."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


class TestAlembicMigrations:
    def test_upgrade_head_creates_all_tables(self, tmp_path):
        """alembic upgrade head on empty DB produces all expected tables."""
        from alphalink.store.db import run_migrations
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
        from alphalink.store.db import run_migrations
        db_path = tmp_path / "test.db"
        run_migrations(db_path)
        run_migrations(db_path)  # second call must not raise

    def test_order_table_has_client_order_id(self, tmp_path):
        """client_order_id column present after migration."""
        from alphalink.store.db import run_migrations
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

        from alphalink.store import db as db_mod
        monkeypatch.setattr(db_mod, "_engine", None)

        from alphalink.store.db import get_engine
        get_engine(tmp_path / "test.db")

        assert calls == [], "create_all must not be called when Alembic is used"
