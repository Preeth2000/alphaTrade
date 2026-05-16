from __future__ import annotations
import sqlite3
from alphaTrade.store.db import run_migrations


def test_backtestrun_has_status_column(tmp_path):
    db_path = tmp_path / "test.db"
    run_migrations(db_path)
    conn = sqlite3.connect(db_path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(backtestrun)").fetchall()}
    conn.close()
    assert "status" in cols


def test_status_defaults_to_done(tmp_path):
    """Rows inserted without status get default 'done'."""
    db_path = tmp_path / "test.db"
    run_migrations(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO backtestrun (ts, start_date, end_date, config_json) VALUES (datetime('now'), '2025-01-01', '2025-01-31', '{}')"
    )
    conn.commit()
    row = conn.execute("SELECT status FROM backtestrun").fetchone()
    conn.close()
    assert row[0] == "done"
