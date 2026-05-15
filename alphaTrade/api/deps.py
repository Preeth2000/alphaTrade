from __future__ import annotations
from collections.abc import Generator
from sqlmodel import Session
from sqlalchemy.engine import Engine


def make_session_dep(engine: Engine):
    def get_session() -> Generator[Session, None, None]:
        with Session(engine) as s:
            yield s
    return get_session
