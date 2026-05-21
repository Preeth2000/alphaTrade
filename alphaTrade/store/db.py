"""Database engine and schema. Migrations via Alembic."""
from __future__ import annotations

import importlib.resources as pkg_resources
import os
from pathlib import Path

from sqlmodel import create_engine, Session

_engine = None
_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _alembic_ini_path() -> str:
    """Locate alembic.ini: env var override, then bundled package copy."""
    env = os.environ.get("ALEMBIC_INI_PATH")
    if env:
        return env
    with pkg_resources.as_file(
        pkg_resources.files("alphaTrade.store").joinpath("alembic.ini")
    ) as p:
        return str(p)


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    # Fallback: SQLite for local dev / tests
    db_path = os.environ.get("STATE_DB_PATH", "state.db")
    return f"sqlite:///{Path(db_path).resolve()}"


def url_from_settings(settings) -> str:
    """Resolve effective DB URL from a Settings object."""
    if settings.database_url:
        return settings.database_url
    return f"sqlite:///{Path(settings.state_db_path).resolve()}"


def run_migrations(database_url: str | Path | None = None) -> None:
    """Run alembic upgrade head. Idempotent."""
    from alembic.config import Config
    from alembic import command

    if isinstance(database_url, Path):
        database_url = f"sqlite:///{database_url.resolve()}"
    url = database_url or _database_url()
    cfg = Config(_alembic_ini_path())
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    command.upgrade(cfg, "head")


def get_engine(database_url: str | Path | None = None):
    global _engine
    if _engine is None:
        if isinstance(database_url, Path):
            database_url = f"sqlite:///{database_url.resolve()}"
        url = database_url or _database_url()
        run_migrations(url)
        _engine = create_engine(url, echo=False)
    return _engine


def get_session(database_url: str | None = None) -> Session:
    return Session(get_engine(database_url))
