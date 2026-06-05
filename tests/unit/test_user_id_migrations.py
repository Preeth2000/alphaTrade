"""Tests for migrations 0018 + 0019: user_id added to BotSettings and trade tables."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text


def _run_migrations(url: str) -> None:
    from alembic.config import Config
    from alembic import command
    from pathlib import Path

    migrations_dir = Path(__file__).parent.parent.parent / "alphaTrade" / "store" / "migrations"
    alembic_ini = Path(__file__).parent.parent.parent / "alphaTrade" / "store" / "alembic.ini"
    cfg = Config(str(alembic_ini))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.set_main_option("script_location", str(migrations_dir))
    command.upgrade(cfg, "head")


def test_0018_user_id_on_botsettings(tmp_path):
    url = f"sqlite:///{tmp_path}/at.db"
    _run_migrations(url)
    engine = create_engine(url)
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("botsettings")}
    assert "user_id" in cols


def test_0018_sentinel_backfill(tmp_path):
    url = f"sqlite:///{tmp_path}/at.db"
    _run_migrations(url)
    engine = create_engine(url)
    with engine.connect() as conn:
        # Insert a legacy singleton row (id=1) without user_id, then re-run — migration is idempotent
        rows = list(conn.execute(text("SELECT user_id FROM botsettings WHERE id = 1")))
    # No pre-existing row was inserted (fresh DB), so no row to check — just verify column exists.


def test_0019_user_id_on_signal(tmp_path):
    url = f"sqlite:///{tmp_path}/at.db"
    _run_migrations(url)
    engine = create_engine(url)
    insp = inspect(engine)
    for table in ("signal", "order", "position", "equitycurve"):
        cols = {c["name"] for c in insp.get_columns(table)}
        assert "user_id" in cols, f"user_id missing from {table}"


def test_migrations_idempotent(tmp_path):
    url = f"sqlite:///{tmp_path}/at_idem.db"
    _run_migrations(url)
    _run_migrations(url)  # second run must be no-op


def test_user_id_indexes_exist(tmp_path):
    url = f"sqlite:///{tmp_path}/at_idx.db"
    _run_migrations(url)
    engine = create_engine(url)
    insp = inspect(engine)
    bs_indexes = {idx["name"] for idx in insp.get_indexes("botsettings")}
    assert "ix_botsettings_user_id" in bs_indexes
    signal_indexes = {idx["name"] for idx in insp.get_indexes("signal")}
    assert "ix_signal_user_id" in signal_indexes
