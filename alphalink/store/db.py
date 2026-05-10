"""SQLite engine and schema. Migrations via Alembic."""
from __future__ import annotations

from pathlib import Path

from sqlmodel import create_engine, Session

_engine = None
_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def run_migrations(db_path: str | Path) -> None:
    """Run alembic upgrade head against db_path. Idempotent."""
    from alembic.config import Config
    from alembic import command

    ini = Path(__file__).parent.parent.parent / "alembic.ini"
    cfg = Config(str(ini))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    command.upgrade(cfg, "head")


def get_engine(db_path: str | Path = "state.db"):
    global _engine
    if _engine is None:
        run_migrations(db_path)
        _engine = create_engine(f"sqlite:///{db_path}", echo=False)
    return _engine


def get_session(db_path: str | Path = "state.db") -> Session:
    return Session(get_engine(db_path))
