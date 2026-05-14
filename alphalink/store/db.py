"""SQLite engine and schema. Migrations via Alembic."""
from __future__ import annotations

import importlib.resources as pkg_resources
import os
from pathlib import Path

from sqlmodel import create_engine, Session

_engines: dict[str, object] = {}
_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _alembic_ini_path() -> str:
    """Locate alembic.ini: env var override, then bundled package copy."""
    env = os.environ.get("ALEMBIC_INI_PATH")
    if env:
        return env
    with pkg_resources.as_file(
        pkg_resources.files("alphalink.store").joinpath("alembic.ini")
    ) as p:
        return str(p)


def run_migrations(db_path: str | Path) -> None:
    """Run alembic upgrade head against db_path. Idempotent."""
    from alembic.config import Config
    from alembic import command

    cfg = Config(_alembic_ini_path())
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    command.upgrade(cfg, "head")


def get_engine(db_path: str | Path = "state.db"):
    key = str(Path(db_path).resolve())
    if key not in _engines:
        run_migrations(db_path)
        _engines[key] = create_engine(f"sqlite:///{key}", echo=False)
    return _engines[key]


def get_session(db_path: str | Path = "state.db") -> Session:
    return Session(get_engine(db_path))
