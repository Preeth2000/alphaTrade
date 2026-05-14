# REST API Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a FastAPI REST API on :8081 that serves bot data (positions, orders, signals, P&L, models, backtests) and exposes read/write settings backed by a new `BotSettings` SQLite table with hot-reload on each tick.

**Architecture:** FastAPI app runs in the same asyncio event loop as the bot via `asyncio.create_task(server.serve())`. All routes open a short-lived SQLModel session per request against the existing SQLite DB. Settings written via `PUT /api/v1/settings` are picked up by the bot at the top of the next tick without restart.

**Tech Stack:** FastAPI ≥0.111, uvicorn[standard] ≥0.29, SQLModel (existing), pytest + FastAPI TestClient

---

## File Map

**Create:**
- `alphalink/api/__init__.py` — empty package marker
- `alphalink/api/auth.py` — `make_api_key_dep(engine)` factory
- `alphalink/api/deps.py` — `make_session_dep(engine)` factory
- `alphalink/api/app.py` — `create_app(engine, health_state)` + `start_api_server()`
- `alphalink/api/routers/__init__.py` — empty
- `alphalink/api/routers/positions.py`
- `alphalink/api/routers/orders.py`
- `alphalink/api/routers/signals.py`
- `alphalink/api/routers/pnl.py`
- `alphalink/api/routers/models.py`
- `alphalink/api/routers/backtest.py`
- `alphalink/api/routers/health.py`
- `alphalink/api/routers/settings.py`
- `alphalink/store/migrations/versions/0003_bot_settings.py`
- `tests/unit/test_api_auth.py`
- `tests/unit/test_api_routers.py`
- `tests/unit/test_bot_settings_repo.py`
- `tests/unit/test_api_hot_reload.py`

**Modify:**
- `pyproject.toml` — add fastapi, uvicorn deps
- `alphalink/config.py` — add `api_port: int = 8081`
- `alphalink/store/repos.py` — add `BotSettings` model + `BotSettingsRepo` + query methods on existing repos
- `alphalink/main.py` — wire API server + hot-reload in tick
- `tests/unit/test_migrations.py` — assert `botsettings` table exists

---

## Task 1: Add Dependencies and `api_port` Setting

**Files:**
- Modify: `pyproject.toml`
- Modify: `alphalink/config.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_api_port_setting.py
def test_settings_has_api_port(monkeypatch):
    monkeypatch.setenv("T212_API_KEY", "k")
    from alphalink.config import Settings
    s = Settings()
    assert s.api_port == 8081

def test_settings_api_port_overridable(monkeypatch):
    monkeypatch.setenv("T212_API_KEY", "k")
    monkeypatch.setenv("API_PORT", "9000")
    import importlib, alphalink.config as m
    importlib.reload(m)
    s = m.Settings()
    assert s.api_port == 9000
```

- [ ] **Step 2: Run test to confirm failure**

```bash
pytest tests/unit/test_api_port_setting.py -v
```
Expected: `AttributeError: 'Settings' object has no attribute 'api_port'`

- [ ] **Step 3: Add `api_port` to Settings and deps to pyproject.toml**

In `alphalink/config.py`, add to the `Settings` class after `log_file`:
```python
    api_port: int = 8081
```

In `pyproject.toml`, add to `dependencies`:
```toml
    "fastapi>=0.111",
    "uvicorn[standard]>=0.29",
```

- [ ] **Step 4: Install new deps**

```bash
pip install -e ".[dev]"
```
Expected: installs fastapi and uvicorn without errors.

- [ ] **Step 5: Run test to confirm pass**

```bash
pytest tests/unit/test_api_port_setting.py -v
```
Expected: PASSED (2 tests)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml alphalink/config.py tests/unit/test_api_port_setting.py
git commit -m "feat(api): add fastapi/uvicorn deps + api_port setting"
```

---

## Task 2: BotSettings ORM + BotSettingsRepo

**Files:**
- Modify: `alphalink/store/repos.py` — append `BotSettings` SQLModel + `BotSettingsRepo`
- Create: `tests/unit/test_bot_settings_repo.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_bot_settings_repo.py
import pytest
from sqlmodel import create_engine
from alphalink.store.db import run_migrations


@pytest.fixture()
def engine(tmp_path):
    db = tmp_path / "test.db"
    run_migrations(db)
    from sqlmodel import create_engine as ce
    return ce(f"sqlite:///{db}")


def test_get_returns_none_when_empty(engine):
    from sqlmodel import Session
    from alphalink.store.repos import BotSettingsRepo
    with Session(engine) as s:
        assert BotSettingsRepo(s).get() is None


def test_upsert_creates_row(engine):
    from sqlmodel import Session
    from alphalink.store.repos import BotSettings, BotSettingsRepo
    with Session(engine) as s:
        repo = BotSettingsRepo(s)
        repo.upsert(BotSettings(id=1, t212_env="live", max_positions=10))
        row = repo.get()
    assert row is not None
    assert row.t212_env == "live"
    assert row.max_positions == 10


def test_upsert_is_idempotent(engine):
    from sqlmodel import Session
    from alphalink.store.repos import BotSettings, BotSettingsRepo
    with Session(engine) as s:
        repo = BotSettingsRepo(s)
        repo.upsert(BotSettings(id=1, max_positions=3))
        repo.upsert(BotSettings(id=1, max_positions=7))
        row = repo.get()
    assert row.max_positions == 7


def test_default_fields(engine):
    from sqlmodel import Session
    from alphalink.store.repos import BotSettings, BotSettingsRepo
    with Session(engine) as s:
        repo = BotSettingsRepo(s)
        repo.upsert(BotSettings(id=1))
        row = repo.get()
    assert row.t212_env == "demo"
    assert row.size_pct == pytest.approx(0.10)
    assert row.alphalink_api_key == ""
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/unit/test_bot_settings_repo.py -v
```
Expected: `ImportError` or `sqlalchemy.exc.OperationalError` (table doesn't exist yet)

- [ ] **Step 3: Add BotSettings + BotSettingsRepo to repos.py**

Append to `alphalink/store/repos.py` after the last existing class:

```python
class BotSettings(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    # T212
    t212_api_key: str = Field(default="")
    t212_env: str = Field(default="demo")
    t212_account_type: str = Field(default="invest")
    # Data
    data_provider: str = Field(default="yfinance")
    polygon_api_key: str = Field(default="")
    # Slack
    slack_enabled: bool = Field(default=False)
    slack_webhook_url: str = Field(default="")
    slack_min_level: str = Field(default="WARNING")
    # Email
    email_enabled: bool = Field(default=False)
    email_smtp_host: str = Field(default="")
    email_smtp_port: int = Field(default=587)
    email_smtp_user: str = Field(default="")
    email_smtp_password: str = Field(default="")
    email_from_addr: str = Field(default="")
    email_to_addrs: str = Field(default="")  # comma-separated
    email_min_level: str = Field(default="WARNING")
    # Trading defaults
    size_pct: float = Field(default=0.10)
    stop_loss_pct: float = Field(default=0.02)
    take_profit_pct: float = Field(default=0.05)
    cooldown_bars: int = Field(default=3)
    extended_hours: bool = Field(default=False)
    # Risk
    max_positions: int = Field(default=5)
    daily_loss_halt_pct: float = Field(default=0.05)
    # API auth
    alphalink_api_key: str = Field(default="")


class BotSettingsRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def get(self) -> BotSettings | None:
        return self._s.get(BotSettings, 1)

    def upsert(self, settings: BotSettings) -> None:
        settings.id = 1
        existing = self._s.get(BotSettings, 1)
        if existing:
            for key, val in settings.model_dump(exclude={"id"}).items():
                setattr(existing, key, val)
        else:
            self._s.add(settings)
        self._s.commit()
```

- [ ] **Step 4: Run tests — expect failure due to missing migration**

```bash
pytest tests/unit/test_bot_settings_repo.py -v
```
Expected: `sqlalchemy.exc.OperationalError: no such table: botsettings`

(The migration in Task 3 will fix this.)

- [ ] **Step 5: Commit the model (migration comes next task)**

```bash
git add alphalink/store/repos.py tests/unit/test_bot_settings_repo.py
git commit -m "feat(store): BotSettings ORM model + BotSettingsRepo"
```

---

## Task 3: Alembic Migration 0003

**Files:**
- Create: `alphalink/store/migrations/versions/0003_bot_settings.py`
- Modify: `tests/unit/test_migrations.py`

- [ ] **Step 1: Add migration test**

In `tests/unit/test_migrations.py`, add to `TestAlembicMigrations`:

```python
    def test_upgrade_creates_botsettings_table(self, tmp_path):
        from alphalink.store.db import run_migrations
        db_path = tmp_path / "test.db"
        run_migrations(db_path)

        import sqlite3
        conn = sqlite3.connect(db_path)
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        cols = {row[1] for row in conn.execute(
            "PRAGMA table_info('botsettings')"
        ).fetchall()}
        conn.close()

        assert "botsettings" in tables
        assert "t212_api_key" in cols
        assert "alphalink_api_key" in cols
        assert "daily_loss_halt_pct" in cols
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/unit/test_migrations.py::TestAlembicMigrations::test_upgrade_creates_botsettings_table -v
```
Expected: `AssertionError: assert 'botsettings' in ...`

- [ ] **Step 3: Create migration file**

```python
# alphalink/store/migrations/versions/0003_bot_settings.py
"""bot settings table

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-14
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "botsettings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("t212_api_key", sa.String(), nullable=False, server_default=""),
        sa.Column("t212_env", sa.String(), nullable=False, server_default="demo"),
        sa.Column("t212_account_type", sa.String(), nullable=False, server_default="invest"),
        sa.Column("data_provider", sa.String(), nullable=False, server_default="yfinance"),
        sa.Column("polygon_api_key", sa.String(), nullable=False, server_default=""),
        sa.Column("slack_enabled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("slack_webhook_url", sa.String(), nullable=False, server_default=""),
        sa.Column("slack_min_level", sa.String(), nullable=False, server_default="WARNING"),
        sa.Column("email_enabled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("email_smtp_host", sa.String(), nullable=False, server_default=""),
        sa.Column("email_smtp_port", sa.Integer(), nullable=False, server_default="587"),
        sa.Column("email_smtp_user", sa.String(), nullable=False, server_default=""),
        sa.Column("email_smtp_password", sa.String(), nullable=False, server_default=""),
        sa.Column("email_from_addr", sa.String(), nullable=False, server_default=""),
        sa.Column("email_to_addrs", sa.String(), nullable=False, server_default=""),
        sa.Column("email_min_level", sa.String(), nullable=False, server_default="WARNING"),
        sa.Column("size_pct", sa.Float(), nullable=False, server_default="0.1"),
        sa.Column("stop_loss_pct", sa.Float(), nullable=False, server_default="0.02"),
        sa.Column("take_profit_pct", sa.Float(), nullable=False, server_default="0.05"),
        sa.Column("cooldown_bars", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("extended_hours", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("max_positions", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("daily_loss_halt_pct", sa.Float(), nullable=False, server_default="0.05"),
        sa.Column("alphalink_api_key", sa.String(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_table("botsettings")
```

- [ ] **Step 4: Run all migration tests**

```bash
pytest tests/unit/test_migrations.py -v
```
Expected: all PASSED

- [ ] **Step 5: Verify BotSettingsRepo tests now pass**

```bash
pytest tests/unit/test_bot_settings_repo.py -v
```
Expected: all PASSED

- [ ] **Step 6: Commit**

```bash
git add alphalink/store/migrations/versions/0003_bot_settings.py tests/unit/test_migrations.py
git commit -m "feat(store): alembic migration 0003 — botsettings table"
```

---

## Task 4: Repo Query Additions

**Files:**
- Modify: `alphalink/store/repos.py` — add `since`/`limit` to `OrderRepo` and `SignalRepo`; `list_runs` to `BacktestRepo`; `all()` to `ModelPerformanceRepo`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_repo_query_additions.py
import pytest
from datetime import datetime, timedelta
from sqlmodel import create_engine, Session
from alphalink.store.db import run_migrations


@pytest.fixture()
def session(tmp_path):
    db = tmp_path / "test.db"
    run_migrations(db)
    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as s:
        yield s


def test_order_repo_since(session):
    from alphalink.store.repos import Order, OrderRepo
    repo = OrderRepo(session)
    old = Order(ts=datetime(2020, 1, 1), t212_ticker="X", side="BUY", quantity=1.0, status="filled")
    recent = Order(ts=datetime(2026, 1, 1), t212_ticker="Y", side="SELL", quantity=2.0, status="filled")
    session.add(old); session.add(recent); session.commit()
    results = repo.since(datetime(2025, 1, 1), limit=10)
    assert len(results) == 1
    assert results[0].t212_ticker == "Y"


def test_order_repo_since_limit(session):
    from alphalink.store.repos import Order, OrderRepo
    repo = OrderRepo(session)
    for i in range(5):
        session.add(Order(ts=datetime(2026, 1, i+1), t212_ticker=f"T{i}", side="BUY", quantity=1.0, status="filled"))
    session.commit()
    results = repo.since(datetime(2025, 1, 1), limit=3)
    assert len(results) == 3


def test_signal_repo_since(session):
    from alphalink.store.repos import Signal, SignalRepo
    repo = SignalRepo(session)
    session.add(Signal(ts=datetime(2020, 1, 1), run_name="r", ticker="A", signal="BUY"))
    session.add(Signal(ts=datetime(2026, 1, 1), run_name="r", ticker="B", signal="SELL"))
    session.commit()
    results = repo.since(datetime(2025, 1, 1), limit=10)
    assert len(results) == 1
    assert results[0].ticker == "B"


def test_backtest_repo_list_runs(session):
    from alphalink.store.repos import BacktestRepo
    repo = BacktestRepo(session)
    repo.create_run("2025-01-01", "2025-12-31")
    repo.create_run("2026-01-01", "2026-12-31")
    runs = repo.list_runs()
    assert len(runs) == 2


def test_model_performance_repo_all(session):
    from alphalink.store.repos import ModelPerformanceRepo
    repo = ModelPerformanceRepo(session)
    repo.get_or_create("model_a")
    repo.get_or_create("model_b")
    all_models = repo.all()
    assert len(all_models) == 2
    model_ids = {m.model_id for m in all_models}
    assert "model_a" in model_ids
    assert "model_b" in model_ids
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/unit/test_repo_query_additions.py -v
```
Expected: `AttributeError: 'OrderRepo' object has no attribute 'since'`

- [ ] **Step 3: Add query methods to repos.py**

In `OrderRepo`, add after `update_fill`:
```python
    def since(self, since: datetime, limit: int = 100) -> list[Order]:
        return list(self._s.exec(
            select(Order).where(Order.ts >= since).order_by(Order.ts.desc()).limit(limit)
        ).all())
```

In `SignalRepo`, add after `save`:
```python
    def since(self, since: datetime, limit: int = 100) -> list[Signal]:
        return list(self._s.exec(
            select(Signal).where(Signal.ts >= since).order_by(Signal.ts.desc()).limit(limit)
        ).all())
```

In `BacktestRepo`, add after `trades_for_run`:
```python
    def list_runs(self) -> list[BacktestRun]:
        return list(self._s.exec(
            select(BacktestRun).order_by(BacktestRun.ts.desc())
        ).all())
```

In `ModelPerformanceRepo`, add after `is_retired`:
```python
    def all(self) -> list[ModelPerformance]:
        return list(self._s.exec(select(ModelPerformance)).all())
```

- [ ] **Step 4: Run tests to confirm pass**

```bash
pytest tests/unit/test_repo_query_additions.py -v
```
Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add alphalink/store/repos.py tests/unit/test_repo_query_additions.py
git commit -m "feat(store): add since/limit query methods to repos"
```

---

## Task 5: API Foundation — auth.py and deps.py

**Files:**
- Create: `alphalink/api/__init__.py`
- Create: `alphalink/api/auth.py`
- Create: `alphalink/api/deps.py`
- Create: `alphalink/api/routers/__init__.py`
- Create: `tests/unit/test_api_auth.py`

- [ ] **Step 1: Write failing auth tests**

```python
# tests/unit/test_api_auth.py
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import create_engine
from alphalink.store.db import run_migrations


def _make_app(tmp_path, api_key_env: str = ""):
    db = tmp_path / "test.db"
    run_migrations(db)
    engine = create_engine(f"sqlite:///{db}")
    from alphalink.api.auth import make_api_key_dep
    from alphalink.api.deps import make_session_dep
    app = FastAPI()
    dep = make_api_key_dep(engine)

    @app.get("/test")
    def _route(_: None = __import__("fastapi").Depends(dep)):
        return {"ok": True}

    return TestClient(app), engine


def test_no_key_configured_allows_all(tmp_path, monkeypatch):
    monkeypatch.delenv("ALPHALINK_API_KEY", raising=False)
    client, _ = _make_app(tmp_path)
    resp = client.get("/test")
    assert resp.status_code == 200


def test_wrong_key_returns_403(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHALINK_API_KEY", "secret")
    client, _ = _make_app(tmp_path)
    resp = client.get("/test", headers={"X-API-Key": "wrong"})
    assert resp.status_code == 403


def test_correct_key_returns_200(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHALINK_API_KEY", "secret")
    client, _ = _make_app(tmp_path)
    resp = client.get("/test", headers={"X-API-Key": "secret"})
    assert resp.status_code == 200


def test_db_key_overrides_env(tmp_path, monkeypatch):
    monkeypatch.delenv("ALPHALINK_API_KEY", raising=False)
    client, engine = _make_app(tmp_path)
    # Set key via DB
    from sqlmodel import Session
    from alphalink.store.repos import BotSettings, BotSettingsRepo
    with Session(engine) as s:
        BotSettingsRepo(s).upsert(BotSettings(id=1, alphalink_api_key="db-secret"))
    resp = client.get("/test", headers={"X-API-Key": "db-secret"})
    assert resp.status_code == 200
    resp_wrong = client.get("/test", headers={"X-API-Key": "wrong"})
    assert resp_wrong.status_code == 403
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/unit/test_api_auth.py -v
```
Expected: `ModuleNotFoundError: No module named 'alphalink.api'`

- [ ] **Step 3: Create foundation files**

```python
# alphalink/api/__init__.py
```

```python
# alphalink/api/routers/__init__.py
```

```python
# alphalink/api/auth.py
from __future__ import annotations
import os
from fastapi import Header, HTTPException
from sqlmodel import Session
from sqlalchemy.engine import Engine


def make_api_key_dep(engine: Engine):
    def require_api_key(x_api_key: str = Header(default="")) -> None:
        from alphalink.store.repos import BotSettingsRepo
        with Session(engine) as s:
            db_s = BotSettingsRepo(s).get()
        active_key = (db_s.alphalink_api_key if db_s else "") or os.environ.get("ALPHALINK_API_KEY", "")
        if not active_key:
            return
        if x_api_key != active_key:
            raise HTTPException(status_code=403, detail="Invalid API key")
    return require_api_key
```

```python
# alphalink/api/deps.py
from __future__ import annotations
from collections.abc import Generator
from sqlmodel import Session
from sqlalchemy.engine import Engine


def make_session_dep(engine: Engine):
    def get_session() -> Generator[Session, None, None]:
        with Session(engine) as s:
            yield s
    return get_session
```

- [ ] **Step 4: Run auth tests to confirm pass**

```bash
pytest tests/unit/test_api_auth.py -v
```
Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add alphalink/api/__init__.py alphalink/api/auth.py alphalink/api/deps.py alphalink/api/routers/__init__.py tests/unit/test_api_auth.py
git commit -m "feat(api): auth + session deps"
```

---

## Task 6: Read Routers — Positions and Health

**Files:**
- Create: `alphalink/api/routers/positions.py`
- Create: `alphalink/api/routers/health.py`

Tests go in `tests/unit/test_api_routers.py` (create this file; Tasks 7-10 will append to it).

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_api_routers.py
import pytest
from fastapi.testclient import TestClient
from sqlmodel import create_engine, Session
from alphalink.store.db import run_migrations
from alphalink.health import HealthState


def _engine(tmp_path):
    db = tmp_path / "test.db"
    run_migrations(db)
    return create_engine(f"sqlite:///{db}")


def _client(engine, health_state=None):
    from alphalink.api.app import create_app
    return TestClient(create_app(engine, health_state or HealthState()))


# --- Positions ---

def test_positions_empty(tmp_path):
    client = _client(_engine(tmp_path))
    resp = client.get("/api/v1/positions")
    assert resp.status_code == 200
    assert resp.json() == []


def test_positions_returns_rows(tmp_path):
    engine = _engine(tmp_path)
    from alphalink.store.repos import Position, PositionRepo
    from datetime import datetime
    with Session(engine) as s:
        PositionRepo(s).upsert(Position(
            t212_ticker="AAPL_US_EQ", quantity=10.0, avg_entry=150.0,
            opened_at=datetime(2026, 1, 1),
        ))
    resp = _client(engine).get("/api/v1/positions")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["t212_ticker"] == "AAPL_US_EQ"


# --- Health ---

def test_health_returns_state(tmp_path):
    state = HealthState()
    state.t212_ok = True
    state.models_loaded = True
    resp = _client(_engine(tmp_path), state).get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["t212_ok"] is True
    assert body["models_loaded"] is True
    assert body["last_tick_at"] is None
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/unit/test_api_routers.py -v
```
Expected: `ModuleNotFoundError: No module named 'alphalink.api.app'`

- [ ] **Step 3: Create positions router**

```python
# alphalink/api/routers/positions.py
from __future__ import annotations
from collections.abc import Callable
from fastapi import APIRouter, Depends
from sqlmodel import Session
from alphalink.store.repos import Position, PositionRepo


def make_router(session_dep: Callable, api_key_dep: Callable) -> APIRouter:
    router = APIRouter()

    @router.get("/positions", response_model=list[Position])
    def list_positions(
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        return PositionRepo(session).all()

    return router
```

- [ ] **Step 4: Create health router**

```python
# alphalink/api/routers/health.py
from __future__ import annotations
from collections.abc import Callable
from fastapi import APIRouter, Depends
from alphalink.health import HealthState


def make_router(health_state: HealthState, api_key_dep: Callable) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    def get_health(_: None = Depends(api_key_dep)):
        return {
            "last_tick_at": (
                health_state.last_tick_at.isoformat()
                if health_state.last_tick_at else None
            ),
            "t212_ok": health_state.t212_ok,
            "models_loaded": health_state.models_loaded,
            "longest_interval_seconds": health_state.longest_interval_seconds,
        }

    return router
```

- [ ] **Step 5: Create minimal app.py (will be expanded in Task 10)**

```python
# alphalink/api/app.py
from __future__ import annotations
import logging
from sqlalchemy.engine import Engine
from fastapi import FastAPI
from alphalink.api.auth import make_api_key_dep
from alphalink.api.deps import make_session_dep
from alphalink.health import HealthState

log = logging.getLogger(__name__)


def create_app(engine: Engine, health_state: HealthState) -> FastAPI:
    from alphalink.api.routers import positions, health

    app = FastAPI(title="alphaLink API", version="1.0")
    session_dep = make_session_dep(engine)
    api_key_dep = make_api_key_dep(engine)

    app.include_router(positions.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(health.make_router(health_state, api_key_dep), prefix="/api/v1")

    return app
```

- [ ] **Step 6: Run tests to confirm pass**

```bash
pytest tests/unit/test_api_routers.py -v
```
Expected: all PASSED

- [ ] **Step 7: Commit**

```bash
git add alphalink/api/routers/positions.py alphalink/api/routers/health.py alphalink/api/app.py tests/unit/test_api_routers.py
git commit -m "feat(api): positions + health routers"
```

---

## Task 7: Read Routers — Orders and Signals

**Files:**
- Create: `alphalink/api/routers/orders.py`
- Create: `alphalink/api/routers/signals.py`
- Modify: `alphalink/api/app.py` — register routers
- Modify: `tests/unit/test_api_routers.py` — append tests

- [ ] **Step 1: Append tests to test_api_routers.py**

```python
# Append to tests/unit/test_api_routers.py

from datetime import datetime, timedelta


def test_orders_empty_defaults_24h(tmp_path):
    resp = _client(_engine(tmp_path)).get("/api/v1/orders")
    assert resp.status_code == 200
    assert resp.json() == []


def test_orders_since_filters(tmp_path):
    engine = _engine(tmp_path)
    from alphalink.store.repos import Order
    with Session(engine) as s:
        s.add(Order(ts=datetime(2020, 1, 1), t212_ticker="OLD", side="BUY", quantity=1.0, status="filled"))
        s.add(Order(ts=datetime(2026, 1, 1), t212_ticker="NEW", side="BUY", quantity=1.0, status="filled"))
        s.commit()
    resp = _client(engine).get("/api/v1/orders?since=2025-01-01T00:00:00&limit=50")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["t212_ticker"] == "NEW"


def test_signals_since_filters(tmp_path):
    engine = _engine(tmp_path)
    from alphalink.store.repos import Signal
    with Session(engine) as s:
        s.add(Signal(ts=datetime(2020, 1, 1), run_name="r", ticker="OLD", signal="BUY"))
        s.add(Signal(ts=datetime(2026, 1, 1), run_name="r", ticker="NEW", signal="SELL"))
        s.commit()
    resp = _client(engine).get("/api/v1/signals?since=2025-01-01T00:00:00")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["ticker"] == "NEW"
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/unit/test_api_routers.py::test_orders_since_filters -v
```
Expected: `404 Not Found` (route not registered yet)

- [ ] **Step 3: Create orders router**

```python
# alphalink/api/routers/orders.py
from __future__ import annotations
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlmodel import Session
from alphalink.store.repos import Order, OrderRepo


def make_router(session_dep: Callable, api_key_dep: Callable) -> APIRouter:
    router = APIRouter()

    @router.get("/orders", response_model=list[Order])
    def list_orders(
        since: Optional[datetime] = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        cutoff = since if since is not None else datetime.utcnow() - timedelta(hours=24)
        return OrderRepo(session).since(cutoff, limit)

    return router
```

- [ ] **Step 4: Create signals router**

```python
# alphalink/api/routers/signals.py
from __future__ import annotations
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlmodel import Session
from alphalink.store.repos import Signal, SignalRepo


def make_router(session_dep: Callable, api_key_dep: Callable) -> APIRouter:
    router = APIRouter()

    @router.get("/signals", response_model=list[Signal])
    def list_signals(
        since: Optional[datetime] = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        cutoff = since if since is not None else datetime.utcnow() - timedelta(hours=24)
        return SignalRepo(session).since(cutoff, limit)

    return router
```

- [ ] **Step 5: Register routers in app.py**

Replace `create_app` in `alphalink/api/app.py`:

```python
def create_app(engine: Engine, health_state: HealthState) -> FastAPI:
    from alphalink.api.routers import positions, orders, signals, health

    app = FastAPI(title="alphaLink API", version="1.0")
    session_dep = make_session_dep(engine)
    api_key_dep = make_api_key_dep(engine)

    app.include_router(positions.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(orders.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(signals.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(health.make_router(health_state, api_key_dep), prefix="/api/v1")

    return app
```

- [ ] **Step 6: Run tests**

```bash
pytest tests/unit/test_api_routers.py -v
```
Expected: all PASSED

- [ ] **Step 7: Commit**

```bash
git add alphalink/api/routers/orders.py alphalink/api/routers/signals.py alphalink/api/app.py tests/unit/test_api_routers.py
git commit -m "feat(api): orders + signals routers with since/limit"
```

---

## Task 8: Read Routers — PnL, Models, Backtest

**Files:**
- Create: `alphalink/api/routers/pnl.py`
- Create: `alphalink/api/routers/models.py`
- Create: `alphalink/api/routers/backtest.py`
- Modify: `alphalink/api/app.py`
- Modify: `tests/unit/test_api_routers.py`

- [ ] **Step 1: Append tests**

```python
# Append to tests/unit/test_api_routers.py

def test_pnl_since_filters(tmp_path):
    engine = _engine(tmp_path)
    from alphalink.store.repos import PnlSnapshot, PnlSnapshotRepo
    with Session(engine) as s:
        repo = PnlSnapshotRepo(s)
        repo.upsert(PnlSnapshot(date="2020-01-01", total_equity=10000, day_pnl=0, day_pnl_pct=0, realized_pnl=0, unrealized_pnl=0))
        repo.upsert(PnlSnapshot(date="2026-01-01", total_equity=12000, day_pnl=200, day_pnl_pct=1.7, realized_pnl=200, unrealized_pnl=0))
    resp = _client(engine).get("/api/v1/pnl?since=2025-01-01")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["date"] == "2026-01-01"


def test_models_empty(tmp_path):
    resp = _client(_engine(tmp_path)).get("/api/v1/models")
    assert resp.status_code == 200
    assert resp.json() == []


def test_models_returns_rows(tmp_path):
    engine = _engine(tmp_path)
    from alphalink.store.repos import ModelPerformanceRepo
    with Session(engine) as s:
        ModelPerformanceRepo(s).get_or_create("my_model")
    resp = _client(engine).get("/api/v1/models")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["model_id"] == "my_model"


def test_backtest_runs_empty(tmp_path):
    resp = _client(_engine(tmp_path)).get("/api/v1/backtest/runs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_backtest_trades_404_unknown_run(tmp_path):
    resp = _client(_engine(tmp_path)).get("/api/v1/backtest/runs/999/trades")
    assert resp.status_code == 404


def test_backtest_trades_returns_rows(tmp_path):
    engine = _engine(tmp_path)
    from alphalink.store.repos import BacktestRepo
    with Session(engine) as s:
        run_id = BacktestRepo(s).create_run("2025-01-01", "2025-12-31")
        BacktestRepo(s).record_trade(
            run_id=run_id, model_id="m", side="BUY",
            entry_time=datetime(2025, 1, 1), exit_time=datetime(2025, 2, 1),
            entry_price=100.0, exit_price=110.0, quantity=1.0,
            exit_reason="OCO_TP", realized_pnl=10.0,
        )
    resp = _client(engine).get(f"/api/v1/backtest/runs/{run_id}/trades")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/unit/test_api_routers.py::test_pnl_since_filters -v
```
Expected: `404 Not Found`

- [ ] **Step 3: Create pnl router**

```python
# alphalink/api/routers/pnl.py
from __future__ import annotations
from collections.abc import Callable
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlmodel import Session
from alphalink.store.repos import PnlSnapshot, PnlSnapshotRepo


def make_router(session_dep: Callable, api_key_dep: Callable) -> APIRouter:
    router = APIRouter()

    @router.get("/pnl", response_model=list[PnlSnapshot])
    def list_pnl(
        since: Optional[str] = Query(default=None, description="YYYY-MM-DD"),
        limit: int = Query(default=100, ge=1, le=1000),
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        rows = PnlSnapshotRepo(session).since(since or "0000-01-01")
        return rows[:limit]

    return router
```

- [ ] **Step 4: Create models router**

```python
# alphalink/api/routers/models.py
from __future__ import annotations
from collections.abc import Callable
from fastapi import APIRouter, Depends
from sqlmodel import Session
from alphalink.store.repos import ModelPerformance, ModelPerformanceRepo


def make_router(session_dep: Callable, api_key_dep: Callable) -> APIRouter:
    router = APIRouter()

    @router.get("/models", response_model=list[ModelPerformance])
    def list_models(
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        return ModelPerformanceRepo(session).all()

    return router
```

- [ ] **Step 5: Create backtest router**

```python
# alphalink/api/routers/backtest.py
from __future__ import annotations
from collections.abc import Callable
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session
from alphalink.store.repos import BacktestRun, BacktestTrade, BacktestRepo


def make_router(session_dep: Callable, api_key_dep: Callable) -> APIRouter:
    router = APIRouter()

    @router.get("/backtest/runs", response_model=list[BacktestRun])
    def list_runs(
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        return BacktestRepo(session).list_runs()

    @router.get("/backtest/runs/{run_id}/trades", response_model=list[BacktestTrade])
    def list_trades(
        run_id: int,
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        if session.get(BacktestRun, run_id) is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return BacktestRepo(session).trades_for_run(run_id)

    return router
```

- [ ] **Step 6: Update app.py to register all routers**

Replace `create_app` in `alphalink/api/app.py`:

```python
def create_app(engine: Engine, health_state: HealthState) -> FastAPI:
    from alphalink.api.routers import (
        positions, orders, signals, pnl, models, backtest, health,
    )

    app = FastAPI(title="alphaLink API", version="1.0")
    session_dep = make_session_dep(engine)
    api_key_dep = make_api_key_dep(engine)

    app.include_router(positions.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(orders.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(signals.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(pnl.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(models.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(backtest.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(health.make_router(health_state, api_key_dep), prefix="/api/v1")

    return app
```

- [ ] **Step 7: Run all router tests**

```bash
pytest tests/unit/test_api_routers.py -v
```
Expected: all PASSED

- [ ] **Step 8: Commit**

```bash
git add alphalink/api/routers/pnl.py alphalink/api/routers/models.py alphalink/api/routers/backtest.py alphalink/api/app.py tests/unit/test_api_routers.py
git commit -m "feat(api): pnl, models, backtest routers"
```

---

## Task 9: Settings Router

**Files:**
- Create: `alphalink/api/routers/settings.py`
- Modify: `alphalink/api/app.py`
- Modify: `tests/unit/test_api_routers.py`

- [ ] **Step 1: Append tests**

```python
# Append to tests/unit/test_api_routers.py

def test_settings_get_returns_defaults(tmp_path):
    resp = _client(_engine(tmp_path)).get("/api/v1/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["t212_env"] == "demo"
    assert body["max_positions"] == 5


def test_settings_sensitive_fields_masked(tmp_path):
    engine = _engine(tmp_path)
    from alphalink.store.repos import BotSettings, BotSettingsRepo
    with Session(engine) as s:
        BotSettingsRepo(s).upsert(BotSettings(id=1, t212_api_key="real-key", alphalink_api_key="api-key"))
    resp = _client(engine).get("/api/v1/settings")
    body = resp.json()
    assert body["t212_api_key"] == "***"
    assert body["alphalink_api_key"] == "***"


def test_settings_put_partial_update(tmp_path):
    engine = _engine(tmp_path)
    client = _client(engine)
    resp = client.put("/api/v1/settings", json={"max_positions": 10, "t212_env": "live"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["max_positions"] == 10
    assert body["t212_env"] == "live"
    assert body["size_pct"] == pytest.approx(0.10)


def test_settings_put_sensitive_masked_in_response(tmp_path):
    resp = _client(_engine(tmp_path)).put("/api/v1/settings", json={"t212_api_key": "new-key"})
    assert resp.json()["t212_api_key"] == "***"
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/unit/test_api_routers.py::test_settings_get_returns_defaults -v
```
Expected: `404 Not Found`

- [ ] **Step 3: Create settings router**

```python
# alphalink/api/routers/settings.py
from __future__ import annotations
from collections.abc import Callable
from typing import Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session
from alphalink.store.repos import BotSettings, BotSettingsRepo

_SENSITIVE = frozenset({
    "t212_api_key", "polygon_api_key", "email_smtp_password",
    "slack_webhook_url", "alphalink_api_key",
})


class BotSettingsUpdate(BaseModel):
    t212_api_key: Optional[str] = None
    t212_env: Optional[str] = None
    t212_account_type: Optional[str] = None
    data_provider: Optional[str] = None
    polygon_api_key: Optional[str] = None
    slack_enabled: Optional[bool] = None
    slack_webhook_url: Optional[str] = None
    slack_min_level: Optional[str] = None
    email_enabled: Optional[bool] = None
    email_smtp_host: Optional[str] = None
    email_smtp_port: Optional[int] = None
    email_smtp_user: Optional[str] = None
    email_smtp_password: Optional[str] = None
    email_from_addr: Optional[str] = None
    email_to_addrs: Optional[str] = None
    email_min_level: Optional[str] = None
    size_pct: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    cooldown_bars: Optional[int] = None
    extended_hours: Optional[bool] = None
    max_positions: Optional[int] = None
    daily_loss_halt_pct: Optional[float] = None
    alphalink_api_key: Optional[str] = None


def _mask(s: BotSettings) -> dict:
    d = s.model_dump()
    for key in _SENSITIVE:
        if d.get(key):
            d[key] = "***"
    return d


def make_router(session_dep: Callable, api_key_dep: Callable) -> APIRouter:
    router = APIRouter()

    @router.get("/settings")
    def get_settings(
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ) -> dict:
        s = BotSettingsRepo(session).get() or BotSettings(id=1)
        return _mask(s)

    @router.put("/settings")
    def update_settings(
        update: BotSettingsUpdate,
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ) -> dict:
        repo = BotSettingsRepo(session)
        s = repo.get() or BotSettings(id=1)
        for field, value in update.model_dump(exclude_none=True).items():
            setattr(s, field, value)
        repo.upsert(s)
        return _mask(s)

    return router
```

- [ ] **Step 4: Register settings router in app.py**

Replace `create_app` in `alphalink/api/app.py`:

```python
def create_app(engine: Engine, health_state: HealthState) -> FastAPI:
    from alphalink.api.routers import (
        positions, orders, signals, pnl, models, backtest, health, settings,
    )

    app = FastAPI(title="alphaLink API", version="1.0")
    session_dep = make_session_dep(engine)
    api_key_dep = make_api_key_dep(engine)

    app.include_router(positions.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(orders.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(signals.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(pnl.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(models.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(backtest.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(health.make_router(health_state, api_key_dep), prefix="/api/v1")
    app.include_router(settings.make_router(session_dep, api_key_dep), prefix="/api/v1")

    return app
```

- [ ] **Step 5: Run all router tests**

```bash
pytest tests/unit/test_api_routers.py -v
```
Expected: all PASSED

- [ ] **Step 6: Commit**

```bash
git add alphalink/api/routers/settings.py alphalink/api/app.py tests/unit/test_api_routers.py
git commit -m "feat(api): settings GET/PUT router with field masking"
```

---

## Task 10: Add `start_api_server` to app.py

**Files:**
- Modify: `alphalink/api/app.py` — add `start_api_server` coroutine

- [ ] **Step 1: Write test**

```python
# Append to tests/unit/test_api_routers.py

def test_create_app_has_all_routes(tmp_path):
    engine = _engine(tmp_path)
    state = HealthState()
    from alphalink.api.app import create_app
    app = create_app(engine, state)
    paths = {route.path for route in app.routes}
    assert "/api/v1/positions" in paths
    assert "/api/v1/orders" in paths
    assert "/api/v1/signals" in paths
    assert "/api/v1/pnl" in paths
    assert "/api/v1/models" in paths
    assert "/api/v1/backtest/runs" in paths
    assert "/api/v1/backtest/runs/{run_id}/trades" in paths
    assert "/api/v1/health" in paths
    assert "/api/v1/settings" in paths
```

- [ ] **Step 2: Run to confirm pass (create_app already done)**

```bash
pytest tests/unit/test_api_routers.py::test_create_app_has_all_routes -v
```
Expected: PASSED

- [ ] **Step 3: Add `start_api_server` to app.py**

Append to `alphalink/api/app.py`:

```python
import asyncio
import uvicorn


async def start_api_server(
    engine: Engine,
    health_state: HealthState,
    port: int = 8081,
) -> uvicorn.Server:
    app = create_app(engine, health_state)
    config = uvicorn.Config(app, host="0.0.0.0", port=port, loop="none", log_level="warning")
    server = uvicorn.Server(config)
    asyncio.create_task(server.serve())
    log.info("API server listening on :%d", port)
    return server
```

- [ ] **Step 4: Run full test suite to verify no regressions**

```bash
pytest tests/unit/test_api_routers.py tests/unit/test_api_auth.py -v
```
Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add alphalink/api/app.py tests/unit/test_api_routers.py
git commit -m "feat(api): start_api_server coroutine"
```

---

## Task 11: main.py Integration — Wire API + Hot-Reload

**Files:**
- Modify: `alphalink/main.py`
- Create: `tests/unit/test_api_hot_reload.py`

### Overview of changes

1. Import `start_api_server` and `BotSettingsRepo`/`BotSettings`
2. Change `make_tick` signature: `t212: T212Client` → `t212_holder: list`
3. At top of `tick()`: read `BotSettings` from DB and overlay onto runtime `settings`; reinitialise T212Client if credentials changed
4. In `run()`: wrap `t212` in `t212_holder = [t212]`, call `start_api_server`, shut it down on exit

- [ ] **Step 1: Write hot-reload test**

```python
# tests/unit/test_api_hot_reload.py
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from sqlmodel import create_engine, Session
from alphalink.store.db import run_migrations
from alphalink.store.repos import BotSettings, BotSettingsRepo


@pytest.fixture()
def engine(tmp_path):
    db = tmp_path / "test.db"
    run_migrations(db)
    return create_engine(f"sqlite:///{db}")


def test_hot_reload_updates_t212_client(engine):
    """apply_bot_settings reinitialises T212Client when credentials change."""
    from alphalink.main import apply_bot_settings
    from alphalink.broker.t212_client import T212Client

    original = T212Client(api_key="old-key", env="demo")
    t212_holder = [original]

    with Session(engine) as s:
        BotSettingsRepo(s).upsert(BotSettings(id=1, t212_api_key="new-key", t212_env="live"))
        db_s = BotSettingsRepo(s).get()

    settings = MagicMock()
    settings.defaults = MagicMock()
    settings.risk = MagicMock()
    settings.alerts = MagicMock()
    settings.alerts.slack = MagicMock()
    settings.alerts.email = MagicMock()

    apply_bot_settings(db_s, settings, t212_holder)

    assert t212_holder[0] is not original
    assert t212_holder[0]._headers == {"Authorization": "new-key"}


def test_hot_reload_no_change_keeps_client(engine):
    """apply_bot_settings does not replace client when credentials unchanged."""
    from alphalink.main import apply_bot_settings
    from alphalink.broker.t212_client import T212Client

    client = T212Client(api_key="same-key", env="demo")
    t212_holder = [client]

    with Session(engine) as s:
        BotSettingsRepo(s).upsert(BotSettings(id=1, t212_api_key="same-key", t212_env="demo"))
        db_s = BotSettingsRepo(s).get()

    settings = MagicMock()
    settings.defaults = MagicMock()
    settings.risk = MagicMock()
    settings.alerts = MagicMock()
    settings.alerts.slack = MagicMock()
    settings.alerts.email = MagicMock()

    apply_bot_settings(db_s, settings, t212_holder)

    assert t212_holder[0] is client


def test_hot_reload_overlays_risk_settings(engine):
    """apply_bot_settings updates risk.max_positions from DB."""
    from alphalink.main import apply_bot_settings

    with Session(engine) as s:
        BotSettingsRepo(s).upsert(BotSettings(id=1, max_positions=12, daily_loss_halt_pct=0.08))
        db_s = BotSettingsRepo(s).get()

    settings = MagicMock()
    settings.risk = MagicMock()
    settings.defaults = MagicMock()
    settings.alerts = MagicMock()
    settings.alerts.slack = MagicMock()
    settings.alerts.email = MagicMock()
    t212_holder = [MagicMock()]

    apply_bot_settings(db_s, settings, t212_holder)

    assert settings.risk.max_positions == 12
    assert settings.risk.daily_loss_halt_pct == pytest.approx(0.08)
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/unit/test_api_hot_reload.py -v
```
Expected: `ImportError: cannot import name 'apply_bot_settings' from 'alphalink.main'`

- [ ] **Step 3: Add `apply_bot_settings` to main.py and update imports**

At the top of `alphalink/main.py`, add to the existing imports:
```python
from alphalink.store.repos import (
    ...  # existing imports
    BotSettings,
    BotSettingsRepo,
)
```

After the `_INTERVAL_SECONDS` dict in `main.py`, add:

```python
def apply_bot_settings(
    db_s: "BotSettings",
    settings: "Settings",
    t212_holder: list,
) -> None:
    from alphalink.broker.t212_client import T212Client
    new_key = db_s.t212_api_key or ""
    new_env = db_s.t212_env or "demo"
    current_headers = getattr(t212_holder[0], "_headers", {})
    if new_key and current_headers.get("Authorization") != new_key:
        t212_holder[0] = T212Client(api_key=new_key, env=new_env)
        log.info("Hot-reload: T212Client reinitialised (env=%s)", new_env)
    if db_s.data_provider:
        settings.data_provider = db_s.data_provider
    if db_s.size_pct:
        settings.defaults.size_pct = db_s.size_pct
    if db_s.stop_loss_pct:
        settings.defaults.stop_loss_pct = db_s.stop_loss_pct
    if db_s.take_profit_pct:
        settings.defaults.take_profit_pct = db_s.take_profit_pct
    if db_s.cooldown_bars:
        settings.defaults.cooldown_bars = db_s.cooldown_bars
    settings.defaults.extended_hours = db_s.extended_hours
    if db_s.max_positions:
        settings.risk.max_positions = db_s.max_positions
    if db_s.daily_loss_halt_pct:
        settings.risk.daily_loss_halt_pct = db_s.daily_loss_halt_pct
    settings.alerts.slack.enabled = db_s.slack_enabled
    if db_s.slack_webhook_url:
        settings.alerts.slack.webhook_url = db_s.slack_webhook_url
    settings.alerts.email.enabled = db_s.email_enabled
    if db_s.email_to_addrs:
        settings.alerts.email.to_addrs = [
            a.strip() for a in db_s.email_to_addrs.split(",") if a.strip()
        ]
```

- [ ] **Step 4: Update `make_tick` signature — replace `t212: T212Client` with `t212_holder: list`**

In `make_tick`, change the signature from:
```python
def make_tick(
    interval: str,
    *,
    ...
    t212: T212Client,
    ...
):
```
to:
```python
def make_tick(
    interval: str,
    *,
    ...
    t212_holder: list,
    ...
):
```

At the very start of the `tick()` body (before `bar_close_iso = ...`), add:
```python
        # Hot-reload DB settings
        with Session(engine) as _hs:
            _db_s = BotSettingsRepo(_hs).get()
        if _db_s is not None:
            apply_bot_settings(_db_s, settings, t212_holder)
        t212 = t212_holder[0]
```

Remove the `_prev_halt: bool = False` and `_last_t212_key`/`_last_t212_env` nonlocals (hot-reload now handled by `apply_bot_settings`; `_prev_halt` stays as it was).

- [ ] **Step 5: Update `run()` to use `t212_holder` and wire API server**

In `run()`, after `t212 = T212Client(...)` is constructed, wrap it:
```python
    t212_holder: list = [t212]
```

Replace all `make_tick(... t212=t212, ...)` calls with `make_tick(... t212_holder=t212_holder, ...)`.

After `health_runner = await start_health_server(...)`, add:
```python
    api_server = None
    try:
        from alphalink.api.app import start_api_server
        api_server = await start_api_server(engine, health_state, port=settings.api_port)
    except Exception as exc:
        log.error("API server failed to start on :%d: %s", settings.api_port, exc)
```

In the shutdown block (after `await asyncio.gather(*tasks)`), before `alert_manager.shutdown`, add:
```python
    if api_server is not None:
        api_server.should_exit = True
```

Also update `_preresolve_tickers` call to pass `t212_holder[0]` instead of `t212` (it's a one-time call at startup so it still uses the initial client):
```python
    _preresolve_tickers(t212_holder[0], engine, registry, static_map)
```

And update `reconcile_positions` call:
```python
    await reconcile_positions(t212_holder[0], settings)
```

- [ ] **Step 6: Run hot-reload tests**

```bash
pytest tests/unit/test_api_hot_reload.py -v
```
Expected: all PASSED

- [ ] **Step 7: Run full test suite**

```bash
pytest tests/ -v --tb=short 2>&1 | tail -30
```
Expected: no new failures

- [ ] **Step 8: Commit**

```bash
git add alphalink/main.py tests/unit/test_api_hot_reload.py
git commit -m "feat(main): wire API server + BotSettings hot-reload in tick"
```

---

## Task 12: Final Verification

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/ -v --tb=short
```
Expected: all tests PASS, no errors

- [ ] **Step 2: Verify API module imports cleanly**

```bash
python -c "from alphalink.api.app import create_app, start_api_server; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Smoke test API starts**

```bash
python -c "
import asyncio, os
os.environ['T212_API_KEY'] = 'test'
from alphalink.store.db import get_engine
from alphalink.health import HealthState
from alphalink.api.app import create_app
from fastapi.testclient import TestClient
import tempfile, pathlib

with tempfile.TemporaryDirectory() as d:
    engine = get_engine(pathlib.Path(d) / 'test.db')
    client = TestClient(create_app(engine, HealthState()))
    r = client.get('/api/v1/positions')
    assert r.status_code == 200
    r2 = client.get('/api/v1/settings')
    assert r2.status_code == 200
    print('smoke test OK')
"
```
Expected: `smoke test OK`

- [ ] **Step 4: Commit if any minor fixes were needed, otherwise final commit**

```bash
git add -p  # stage only what changed
git commit -m "test(api): final verification pass"
```

- [ ] **Step 5: Close beads issue**

```bash
bd close alphaLink-bmw --reason="FastAPI layer complete: 8 read endpoints + settings CRUD + BotSettings hot-reload"
```
