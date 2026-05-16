# Backtest Scheduling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `POST /backtest/trigger` API endpoint, APScheduler-based nightly scheduled backtests, and runtime schedule management endpoints with per-model cron/disable overrides.

**Architecture:** `BacktestScheduler` wraps APScheduler `AsyncIOScheduler` with one job per model, started in `main.py` alongside `schedule_bar_close`. Trigger endpoint pre-creates a `BacktestRun` (status=`queued`) then fires `asyncio.create_task`. Schedule management endpoints mutate APScheduler jobs in-memory and persist changes to `overrides.yaml`.

**Tech Stack:** APScheduler 3.10+ (already in deps), FastAPI, SQLModel, PyYAML

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `alphaTrade/store/repos.py` | Modify | Add `status` field to `BacktestRun`; add `create_run(status=)` + `update_status()` to `BacktestRepo` |
| `alphaTrade/store/migrations/versions/0007_backtest_status.py` | Create | Add `status` column to `backtest_run` table |
| `alphaTrade/config.py` | Modify | Add `BacktestScheduleOverride`; extend `ModelOverride` + `BacktestConfig`; parse model-level `backtest:` key |
| `alphaTrade/backtest/engine.py` | Modify | Add `run_id` + `model_filter` params to `run_backtest` |
| `alphaTrade/scheduler/backtest_scheduler.py` | Create | `BacktestScheduler` class: APScheduler wrapper, trigger, update_global, update_model, persist |
| `alphaTrade/api/routers/backtest.py` | Modify | Add `POST /backtest/trigger`, `GET/PATCH /backtest/schedule`, `GET/PATCH /backtest/schedule/{model_id}` |
| `alphaTrade/api/app.py` | Modify | Pass `backtest_scheduler` to `backtest.make_router` |
| `alphaTrade/main.py` | Modify | Create + start `BacktestScheduler`; wire into `start_api_server` |
| `tests/unit/test_backtest_status_migration.py` | Create | Migration adds status column |
| `tests/unit/test_config_backtest_schedule.py` | Create | Config parses schedule fields from yaml |
| `tests/unit/test_backtest_engine_filter.py` | Create | `run_backtest` respects `run_id` + `model_filter` |
| `tests/unit/test_backtest_scheduler.py` | Create | Scheduler creates jobs, update_global, update_model, disabled skip |
| `tests/unit/test_api_backtest_trigger.py` | Create | Trigger endpoint returns run_id + queued status |
| `tests/unit/test_api_backtest_schedule.py` | Create | Schedule GET/PATCH endpoints |

---

## Task 1: DB Migration — Add `status` to `backtest_run`

**Files:**
- Create: `alphaTrade/store/migrations/versions/0007_backtest_status.py`
- Modify: `alphaTrade/store/repos.py`
- Create: `tests/unit/test_backtest_status_migration.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_backtest_status_migration.py
from __future__ import annotations
import sqlite3
from alphaTrade.store.db import run_migrations


def test_backtest_run_has_status_column(tmp_path):
    db_path = tmp_path / "test.db"
    run_migrations(db_path)
    conn = sqlite3.connect(db_path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(backtest_run)").fetchall()}
    conn.close()
    assert "status" in cols


def test_status_defaults_to_done(tmp_path):
    """Rows inserted without status get default 'done'."""
    db_path = tmp_path / "test.db"
    run_migrations(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO backtest_run (ts, start_date, end_date, config_json) VALUES (datetime('now'), '2025-01-01', '2025-01-31', '{}')"
    )
    conn.commit()
    row = conn.execute("SELECT status FROM backtest_run").fetchone()
    conn.close()
    assert row[0] == "done"
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/unit/test_backtest_status_migration.py -v
```
Expected: FAIL — `status` column missing

- [ ] **Step 3: Create migration**

```python
# alphaTrade/store/migrations/versions/0007_backtest_status.py
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "backtest_run",
        sa.Column("status", sa.String(), nullable=False, server_default="done"),
    )


def downgrade() -> None:
    op.drop_column("backtest_run", "status")
```

- [ ] **Step 4: Update `BacktestRun` and `BacktestRepo` in `repos.py`**

In `repos.py`, find the `BacktestRun` class and add the field:

```python
class BacktestRun(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=datetime.utcnow)
    start_date: str
    end_date: str
    config_json: str = "{}"
    status: str = "done"
```

Update `BacktestRepo.create_run` to accept and persist `status`:

```python
class BacktestRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def create_run(self, start: str, end: str, config_json: str = "{}", status: str = "done") -> int:
        run = BacktestRun(start_date=start, end_date=end, config_json=config_json, status=status)
        self._s.add(run)
        self._s.commit()
        self._s.refresh(run)
        return run.id

    def update_status(self, run_id: int, status: str) -> None:
        run = self._s.get(BacktestRun, run_id)
        if run:
            run.status = status
            self._s.commit()

    def record_trade(self, run_id: int, **kwargs) -> None:
        trade = BacktestTrade(run_id=run_id, **kwargs)
        self._s.add(trade)
        self._s.commit()

    def trades_for_run(self, run_id: int) -> list[BacktestTrade]:
        return list(self._s.exec(
            select(BacktestTrade).where(BacktestTrade.run_id == run_id)
        ).all())

    def list_runs(self, limit: int = 50) -> list[BacktestRun]:
        return list(self._s.exec(
            select(BacktestRun).order_by(BacktestRun.ts.desc()).limit(limit)
        ).all())
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/unit/test_backtest_status_migration.py -v
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add alphaTrade/store/migrations/versions/0007_backtest_status.py alphaTrade/store/repos.py tests/unit/test_backtest_status_migration.py
git commit -m "feat(store): add status column to backtest_run"
```

---

## Task 2: Config — `BacktestScheduleOverride` + Extended `BacktestConfig`

**Files:**
- Modify: `alphaTrade/config.py`
- Create: `tests/unit/test_config_backtest_schedule.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_config_backtest_schedule.py
from __future__ import annotations
import textwrap
from pathlib import Path
from alphaTrade.config import Settings


def _write_overrides(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "overrides.yaml"
    p.write_text(textwrap.dedent(content))
    return p


def test_backtest_schedule_defaults():
    from alphaTrade.config import BacktestConfig
    cfg = BacktestConfig()
    assert cfg.schedule_enabled is True
    assert cfg.cron == "0 2 * * *"
    assert cfg.lookback_days == 30


def test_backtest_schedule_from_yaml(tmp_path):
    _write_overrides(tmp_path, """
        backtest:
          schedule_enabled: false
          cron: "0 6 * * *"
          lookback_days: 60
    """)
    s = Settings(overrides_path=tmp_path / "overrides.yaml", state_db_path=tmp_path / "s.db")
    assert s.backtest.schedule_enabled is False
    assert s.backtest.cron == "0 6 * * *"
    assert s.backtest.lookback_days == 60


def test_model_backtest_override_disabled(tmp_path):
    _write_overrides(tmp_path, """
        models:
          MSFT_v1:
            backtest:
              disabled: true
    """)
    s = Settings(overrides_path=tmp_path / "overrides.yaml", state_db_path=tmp_path / "s.db")
    assert s.model_overrides["MSFT_v1"].backtest.disabled is True


def test_model_backtest_override_cron(tmp_path):
    _write_overrides(tmp_path, """
        models:
          AAPL_v1:
            backtest:
              cron: "0 4 * * *"
              lookback_days: 14
    """)
    s = Settings(overrides_path=tmp_path / "overrides.yaml", state_db_path=tmp_path / "s.db")
    ov = s.model_overrides["AAPL_v1"].backtest
    assert ov.cron == "0 4 * * *"
    assert ov.lookback_days == 14


def test_model_backtest_defaults_not_disabled(tmp_path):
    _write_overrides(tmp_path, """
        models:
          AAPL_v1:
            enabled: true
    """)
    s = Settings(overrides_path=tmp_path / "overrides.yaml", state_db_path=tmp_path / "s.db")
    assert s.model_overrides["AAPL_v1"].backtest.disabled is False
    assert s.model_overrides["AAPL_v1"].backtest.cron is None
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/unit/test_config_backtest_schedule.py -v
```
Expected: FAIL

- [ ] **Step 3: Update `config.py`**

Add `BacktestScheduleOverride` class (place it just before `ModelOverride`):

```python
class BacktestScheduleOverride(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    disabled: bool = False
    cron: Optional[str] = None
    lookback_days: Optional[int] = None
```

Update `ModelOverride` to include it:

```python
class ModelOverride(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    enabled: bool = True
    t212_ticker: Optional[str] = None
    size_pct: Optional[float] = None
    backtest: BacktestScheduleOverride = BacktestScheduleOverride()
```

Extend `BacktestConfig`:

```python
class BacktestConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    slippage_bps: int = 5
    commission_per_trade: float = 0.0
    initial_equity: float = 10000.0
    default_size_pct: float = 0.10
    sl_pct: Optional[float] = None
    tp_pct: Optional[float] = None
    schedule_enabled: bool = True
    cron: str = "0 2 * * *"
    lookback_days: int = 30
```

Update `_load_overrides` in `Settings` — the `models` section parsing must pass `backtest` sub-dict through to `ModelOverride`:

```python
if "models" in raw:
    self.model_overrides = {
        run_name: ModelOverride(**(cfg or {}))
        for run_name, cfg in raw["models"].items()
    }
```

This already works because `ModelOverride` uses `extra="ignore"` and Pydantic v2 will recursively construct `BacktestScheduleOverride` from the nested dict. Verify this is the case by running the tests.

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_config_backtest_schedule.py -v
```
Expected: PASS

- [ ] **Step 5: Run existing config tests to check no regression**

```bash
pytest tests/unit/test_config_extensions.py -v
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add alphaTrade/config.py tests/unit/test_config_backtest_schedule.py
git commit -m "feat(config): add BacktestScheduleOverride and schedule fields to BacktestConfig"
```

---

## Task 3: Engine — `run_id` + `model_filter` params

**Files:**
- Modify: `alphaTrade/backtest/engine.py`
- Create: `tests/unit/test_backtest_engine_filter.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_backtest_engine_filter.py
from __future__ import annotations
from unittest.mock import MagicMock, patch
from pathlib import Path
import pandas as pd
import pytest
from sqlmodel import create_engine, Session
from alphaTrade.store.db import run_migrations
from alphaTrade.config import BacktestConfig


def _make_engine(tmp_path):
    db = tmp_path / "t.db"
    run_migrations(db)
    return create_engine(f"sqlite:///{db}")


def _fake_df():
    idx = pd.date_range("2025-01-01", periods=100, freq="1h")
    return pd.DataFrame({
        "Open": [100.0] * 100,
        "High": [105.0] * 100,
        "Low":  [95.0] * 100,
        "Close": [101.0] * 100,
        "Volume": [1000] * 100,
    }, index=idx)


@patch("alphaTrade.backtest.engine.scan_models")
@patch("alphaTrade.backtest.engine.YFinanceProvider")
def test_model_filter_runs_only_matching_model(mock_provider_cls, mock_scan, tmp_path):
    manifest_a = MagicMock(); manifest_a.run_name = "AAPL_v1"; manifest_a.ticker = "AAPL"; manifest_a.interval = "1h"; manifest_a.window = 10
    manifest_b = MagicMock(); manifest_b.run_name = "MSFT_v1"; manifest_b.ticker = "MSFT"; manifest_b.interval = "1h"; manifest_b.window = 10
    mock_scan.return_value = [(manifest_a, MagicMock()), (manifest_b, MagicMock())]
    mock_provider_cls.return_value.fetch_ohlcv_range.return_value = None  # causes early return

    engine = _make_engine(tmp_path)
    with Session(engine) as s:
        from alphaTrade.backtest.engine import run_backtest
        result = run_backtest(s, Path("models"), "2025-01-01", "2025-01-31", BacktestConfig(), model_filter="AAPL_v1")

    # Only AAPL_v1 provider call expected
    calls = mock_provider_cls.return_value.fetch_ohlcv_range.call_args_list
    tickers_called = [c.kwargs.get("ticker") or c.args[0] for c in calls]
    assert "AAPL" in str(calls)
    assert "MSFT" not in str(calls)


@patch("alphaTrade.backtest.engine.scan_models")
@patch("alphaTrade.backtest.engine.YFinanceProvider")
def test_run_id_reused_when_provided(mock_provider_cls, mock_scan, tmp_path):
    mock_scan.return_value = []
    engine = _make_engine(tmp_path)
    with Session(engine) as s:
        from alphaTrade.store.repos import BacktestRepo
        existing_id = BacktestRepo(s).create_run("2025-01-01", "2025-01-31", status="queued")

    with Session(engine) as s:
        from alphaTrade.backtest.engine import run_backtest
        result = run_backtest(s, Path("models"), "2025-01-01", "2025-01-31", BacktestConfig(), run_id=existing_id)
    assert result["run_id"] == existing_id

    # Confirm no second run was created
    with Session(engine) as s:
        from alphaTrade.store.repos import BacktestRepo
        runs = BacktestRepo(s).list_runs()
    assert len(runs) == 1
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/unit/test_backtest_engine_filter.py -v
```
Expected: FAIL

- [ ] **Step 3: Update `run_backtest` in `engine.py`**

```python
def run_backtest(
    session: Session,
    models_dir: Path,
    start: str,
    end: str,
    cfg: BacktestConfig,
    run_id: int | None = None,
    model_filter: str | None = None,
) -> dict[str, Any]:
    """Run backtest for all (or one filtered) model in models_dir. Returns summary dict."""
    models = scan_models(models_dir)
    if model_filter is not None:
        models = [(m, mo) for m, mo in models if m.run_name == model_filter]
    if not models:
        raise RuntimeError(f"No models found in {models_dir}" + (f" matching {model_filter!r}" if model_filter else ""))

    provider = YFinanceProvider()
    repo = BacktestRepo(session)
    if run_id is None:
        run_id = repo.create_run(start=start, end=end, config_json=cfg.model_dump_json())

    all_trades: list[dict] = []

    for manifest, model in models:
        log.info("backtest: running %s (%s, %s)", manifest.run_name, manifest.ticker, manifest.interval)
        trades = _run_single_model(
            manifest=manifest,
            model=model,
            provider=provider,
            start=start,
            end=end,
            cfg=cfg,
        )
        for t in trades:
            repo.record_trade(run_id=run_id, **t)
        all_trades.extend(trades)
        log.info("backtest: %s → %d trades", manifest.run_name, len(trades))

    return {"run_id": run_id, "trades": all_trades}
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_backtest_engine_filter.py -v
```
Expected: PASS

- [ ] **Step 5: Run existing engine tests**

```bash
pytest tests/unit/test_backtest_engine.py -v
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add alphaTrade/backtest/engine.py tests/unit/test_backtest_engine_filter.py
git commit -m "feat(backtest): add run_id and model_filter params to run_backtest"
```

---

## Task 4: `BacktestScheduler` Class

**Files:**
- Create: `alphaTrade/scheduler/backtest_scheduler.py`
- Create: `tests/unit/test_backtest_scheduler.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_backtest_scheduler.py
from __future__ import annotations
import asyncio
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from sqlmodel import create_engine, Session
from alphaTrade.store.db import run_migrations
from alphaTrade.config import Settings, BacktestConfig, ModelOverride, BacktestScheduleOverride


def _engine(tmp_path):
    db = tmp_path / "t.db"
    run_migrations(db)
    return create_engine(f"sqlite:///{db}")


def _settings(tmp_path, **backtest_kwargs):
    cfg = BacktestConfig(**backtest_kwargs)
    s = MagicMock(spec=Settings)
    s.backtest = cfg
    s.model_overrides = {}
    s.overrides_path = tmp_path / "overrides.yaml"
    return s


@pytest.mark.asyncio
async def test_scheduler_adds_job_per_model(tmp_path):
    from alphaTrade.scheduler.backtest_scheduler import BacktestScheduler
    engine = _engine(tmp_path)
    settings = _settings(tmp_path, schedule_enabled=True, cron="0 2 * * *", lookback_days=30)

    manifest = MagicMock(); manifest.run_name = "AAPL_v1"
    model = MagicMock()

    sched = BacktestScheduler(engine=engine, settings=settings, models=[(manifest, model)], models_dir=Path("models"))
    sched.start()
    try:
        jobs = sched.get_status()["jobs"]
        assert any(j["id"] == "backtest_AAPL_v1" for j in jobs)
    finally:
        sched.shutdown()


@pytest.mark.asyncio
async def test_scheduler_skips_disabled_model(tmp_path):
    from alphaTrade.scheduler.backtest_scheduler import BacktestScheduler
    engine = _engine(tmp_path)
    settings = _settings(tmp_path, schedule_enabled=True, cron="0 2 * * *", lookback_days=30)
    settings.model_overrides = {
        "AAPL_v1": ModelOverride(backtest=BacktestScheduleOverride(disabled=True))
    }

    manifest = MagicMock(); manifest.run_name = "AAPL_v1"
    sched = BacktestScheduler(engine=engine, settings=settings, models=[(manifest, MagicMock())], models_dir=Path("models"))
    sched.start()
    try:
        jobs = sched.get_status()["jobs"]
        assert not any(j["id"] == "backtest_AAPL_v1" for j in jobs)
    finally:
        sched.shutdown()


@pytest.mark.asyncio
async def test_scheduler_disabled_globally_starts_no_jobs(tmp_path):
    from alphaTrade.scheduler.backtest_scheduler import BacktestScheduler
    engine = _engine(tmp_path)
    settings = _settings(tmp_path, schedule_enabled=False, cron="0 2 * * *", lookback_days=30)

    manifest = MagicMock(); manifest.run_name = "AAPL_v1"
    sched = BacktestScheduler(engine=engine, settings=settings, models=[(manifest, MagicMock())], models_dir=Path("models"))
    sched.start()
    try:
        assert sched.get_status()["schedule_enabled"] is False
        assert sched.get_status()["jobs"] == []
    finally:
        sched.shutdown()


@pytest.mark.asyncio
async def test_update_global_reschedules_jobs(tmp_path):
    from alphaTrade.scheduler.backtest_scheduler import BacktestScheduler
    engine = _engine(tmp_path)
    settings = _settings(tmp_path, schedule_enabled=True, cron="0 2 * * *", lookback_days=30)

    manifest = MagicMock(); manifest.run_name = "AAPL_v1"
    sched = BacktestScheduler(engine=engine, settings=settings, models=[(manifest, MagicMock())], models_dir=Path("models"))
    sched.start()
    try:
        sched.update_global(schedule_enabled=True, cron="0 6 * * *", lookback_days=None)
        assert sched._settings.backtest.cron == "0 6 * * *"
        jobs = sched.get_status()["jobs"]
        assert any(j["id"] == "backtest_AAPL_v1" for j in jobs)
    finally:
        sched.shutdown()


@pytest.mark.asyncio
async def test_update_global_disable_removes_all_jobs(tmp_path):
    from alphaTrade.scheduler.backtest_scheduler import BacktestScheduler
    engine = _engine(tmp_path)
    settings = _settings(tmp_path, schedule_enabled=True, cron="0 2 * * *", lookback_days=30)

    manifest = MagicMock(); manifest.run_name = "AAPL_v1"
    sched = BacktestScheduler(engine=engine, settings=settings, models=[(manifest, MagicMock())], models_dir=Path("models"))
    sched.start()
    try:
        sched.update_global(schedule_enabled=False, cron=None, lookback_days=None)
        assert sched.get_status()["jobs"] == []
    finally:
        sched.shutdown()


@pytest.mark.asyncio
async def test_update_model_disable_removes_job(tmp_path):
    from alphaTrade.scheduler.backtest_scheduler import BacktestScheduler
    engine = _engine(tmp_path)
    settings = _settings(tmp_path, schedule_enabled=True, cron="0 2 * * *", lookback_days=30)

    manifest = MagicMock(); manifest.run_name = "AAPL_v1"
    sched = BacktestScheduler(engine=engine, settings=settings, models=[(manifest, MagicMock())], models_dir=Path("models"))
    sched.start()
    try:
        sched.update_model("AAPL_v1", disabled=True, cron=None, lookback_days=None)
        jobs = sched.get_status()["jobs"]
        assert not any(j["id"] == "backtest_AAPL_v1" for j in jobs)
    finally:
        sched.shutdown()
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/unit/test_backtest_scheduler.py -v
```
Expected: FAIL — module not found

- [ ] **Step 3: Create `alphaTrade/scheduler/backtest_scheduler.py`**

```python
"""APScheduler-based backtest scheduler. One job per model."""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.engine import Engine
from sqlmodel import Session

from alphaTrade.adapter.inference import OnnxModel
from alphaTrade.adapter.manifest import Manifest
from alphaTrade.backtest.engine import run_backtest
from alphaTrade.config import BacktestScheduleOverride, ModelOverride, Settings
from alphaTrade.store.repos import BacktestRepo

log = logging.getLogger(__name__)


async def _execute_backtest(
    engine: Engine,
    settings: Settings,
    models_dir: Path,
    run_id: int,
    start: str,
    end: str,
    model_filter: str | None = None,
) -> None:
    """Update run status and execute backtest. Shared by scheduler and API trigger."""
    with Session(engine) as session:
        BacktestRepo(session).update_status(run_id, "running")
    try:
        with Session(engine) as session:
            run_backtest(
                session=session,
                models_dir=models_dir,
                start=start,
                end=end,
                cfg=settings.backtest,
                run_id=run_id,
                model_filter=model_filter,
            )
        with Session(engine) as session:
            BacktestRepo(session).update_status(run_id, "done")
    except Exception as exc:
        log.error("backtest run_id=%d failed: %s", run_id, exc)
        with Session(engine) as session:
            BacktestRepo(session).update_status(run_id, "failed")


class BacktestScheduler:
    def __init__(
        self,
        engine: Engine,
        settings: Settings,
        models: list[tuple[Manifest, OnnxModel]],
        models_dir: Path,
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._models: dict[str, tuple[Manifest, OnnxModel]] = {m.run_name: (m, mo) for m, mo in models}
        self._models_dir = models_dir
        self._scheduler = AsyncIOScheduler()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._scheduler.start()
        if self._settings.backtest.schedule_enabled:
            self._rebuild_all_jobs()

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)

    # ------------------------------------------------------------------
    # Trigger (API-initiated)
    # ------------------------------------------------------------------

    async def trigger(
        self,
        session: Session,
        start: str,
        end: str,
        model_filter: str | None = None,
    ) -> int:
        """Pre-create run record and launch background task. Returns run_id."""
        run_id = BacktestRepo(session).create_run(
            start=start,
            end=end,
            config_json=self._settings.backtest.model_dump_json(),
            status="queued",
        )
        asyncio.create_task(
            _execute_backtest(
                engine=self._engine,
                settings=self._settings,
                models_dir=self._models_dir,
                run_id=run_id,
                start=start,
                end=end,
                model_filter=model_filter,
            )
        )
        return run_id

    # ------------------------------------------------------------------
    # Runtime updates
    # ------------------------------------------------------------------

    def update_global(
        self,
        schedule_enabled: bool | None,
        cron: str | None,
        lookback_days: int | None,
    ) -> None:
        """Update global schedule config, reschedule all jobs, persist yaml."""
        bt = self._settings.backtest
        if schedule_enabled is not None:
            bt.schedule_enabled = schedule_enabled
        if cron is not None:
            bt.cron = cron
        if lookback_days is not None:
            bt.lookback_days = lookback_days

        self._remove_all_jobs()
        if bt.schedule_enabled:
            self._rebuild_all_jobs()
        self._persist_overrides()

    def update_model(
        self,
        model_id: str,
        disabled: bool | None,
        cron: str | None,
        lookback_days: int | None,
    ) -> None:
        """Update per-model override, reschedule that job, persist yaml."""
        if model_id not in self._settings.model_overrides:
            self._settings.model_overrides[model_id] = ModelOverride()
        ov = self._settings.model_overrides[model_id].backtest
        if disabled is not None:
            ov.disabled = disabled
        if cron is not None:
            ov.cron = cron
        if lookback_days is not None:
            ov.lookback_days = lookback_days

        job_id = f"backtest_{model_id}"
        if self._scheduler.get_job(job_id):
            self._scheduler.remove_job(job_id)

        if self._settings.backtest.schedule_enabled and not ov.disabled and model_id in self._models:
            manifest, model = self._models[model_id]
            self._add_job(manifest, model, ov)

        self._persist_overrides()

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        jobs = []
        for job in self._scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
            })
        return {
            "schedule_enabled": self._settings.backtest.schedule_enabled,
            "cron": self._settings.backtest.cron,
            "lookback_days": self._settings.backtest.lookback_days,
            "jobs": jobs,
        }

    def get_model_status(self, model_id: str) -> dict[str, Any]:
        ov = self._settings.model_overrides.get(model_id, ModelOverride()).backtest
        job = self._scheduler.get_job(f"backtest_{model_id}")
        return {
            "model_id": model_id,
            "disabled": ov.disabled,
            "effective_cron": ov.cron or self._settings.backtest.cron,
            "effective_lookback_days": ov.lookback_days or self._settings.backtest.lookback_days,
            "next_run_time": job.next_run_time.isoformat() if job and job.next_run_time else None,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _rebuild_all_jobs(self) -> None:
        for run_name, (manifest, model) in self._models.items():
            ov = self._settings.model_overrides.get(run_name, ModelOverride()).backtest
            if not ov.disabled:
                self._add_job(manifest, model, ov)

    def _add_job(self, manifest: Manifest, model: OnnxModel, ov: BacktestScheduleOverride) -> None:
        effective_cron = ov.cron or self._settings.backtest.cron
        effective_lookback = ov.lookback_days or self._settings.backtest.lookback_days
        engine = self._engine
        settings = self._settings
        models_dir = self._models_dir

        async def _job() -> None:
            end = date.today().isoformat()
            start = (date.today() - timedelta(days=effective_lookback)).isoformat()
            with Session(engine) as session:
                run_id = BacktestRepo(session).create_run(
                    start=start, end=end,
                    config_json=settings.backtest.model_dump_json(),
                    status="queued",
                )
            await _execute_backtest(engine, settings, models_dir, run_id, start, end, manifest.run_name)

        self._scheduler.add_job(
            _job,
            CronTrigger.from_crontab(effective_cron),
            id=f"backtest_{manifest.run_name}",
            replace_existing=True,
        )

    def _remove_all_jobs(self) -> None:
        for job in self._scheduler.get_jobs():
            if job.id.startswith("backtest_"):
                self._scheduler.remove_job(job.id)

    def _persist_overrides(self) -> None:
        path = self._settings.overrides_path
        raw: dict = yaml.safe_load(path.read_text()) if path.exists() else {}

        raw.setdefault("backtest", {})
        raw["backtest"]["schedule_enabled"] = self._settings.backtest.schedule_enabled
        raw["backtest"]["cron"] = self._settings.backtest.cron
        raw["backtest"]["lookback_days"] = self._settings.backtest.lookback_days

        raw.setdefault("models", {})
        for run_name, override in self._settings.model_overrides.items():
            bt = override.backtest
            bt_dict: dict = {}
            if bt.disabled:
                bt_dict["disabled"] = True
            if bt.cron is not None:
                bt_dict["cron"] = bt.cron
            if bt.lookback_days is not None:
                bt_dict["lookback_days"] = bt.lookback_days
            raw["models"].setdefault(run_name, {})
            if bt_dict:
                raw["models"][run_name]["backtest"] = bt_dict
            elif "backtest" in raw["models"].get(run_name, {}):
                del raw["models"][run_name]["backtest"]

        path.write_text(yaml.dump(raw, default_flow_style=False))
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_backtest_scheduler.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add alphaTrade/scheduler/backtest_scheduler.py tests/unit/test_backtest_scheduler.py
git commit -m "feat(scheduler): BacktestScheduler with APScheduler per-model jobs"
```

---

## Task 5: API — `POST /backtest/trigger`

**Files:**
- Modify: `alphaTrade/api/routers/backtest.py`
- Create: `tests/unit/test_api_backtest_trigger.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_api_backtest_trigger.py
from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from sqlmodel import create_engine, Session
from alphaTrade.store.db import run_migrations
from alphaTrade.health import HealthState


def _engine(tmp_path):
    db = tmp_path / "t.db"
    run_migrations(db)
    return create_engine(f"sqlite:///{db}")


def _client(engine, scheduler):
    from alphaTrade.api.app import create_app
    return TestClient(create_app(engine, HealthState(), backtest_scheduler=scheduler))


def test_trigger_returns_run_id_and_queued(tmp_path):
    scheduler = MagicMock()
    scheduler.trigger = AsyncMock(return_value=42)
    client = _client(_engine(tmp_path), scheduler)

    resp = client.post("/api/v1/backtest/trigger", json={})
    assert resp.status_code == 202
    data = resp.json()
    assert data["run_id"] == 42
    assert data["status"] == "queued"


def test_trigger_with_explicit_dates(tmp_path):
    scheduler = MagicMock()
    scheduler.trigger = AsyncMock(return_value=7)
    client = _client(_engine(tmp_path), scheduler)

    resp = client.post("/api/v1/backtest/trigger", json={
        "start": "2025-01-01", "end": "2025-03-31"
    })
    assert resp.status_code == 202
    assert resp.json()["run_id"] == 7


def test_trigger_with_model_id(tmp_path):
    scheduler = MagicMock()
    scheduler.trigger = AsyncMock(return_value=3)
    client = _client(_engine(tmp_path), scheduler)

    resp = client.post("/api/v1/backtest/trigger", json={"model_id": "AAPL_v1"})
    assert resp.status_code == 202
    call_kwargs = scheduler.trigger.call_args.kwargs
    assert call_kwargs.get("model_filter") == "AAPL_v1"


def test_trigger_without_scheduler_returns_503(tmp_path):
    client = _client(_engine(tmp_path), None)
    resp = client.post("/api/v1/backtest/trigger", json={})
    assert resp.status_code == 503
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/unit/test_api_backtest_trigger.py -v
```
Expected: FAIL

- [ ] **Step 3: Update `alphaTrade/api/routers/backtest.py`**

Replace the entire file:

```python
from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from alphaTrade.store.repos import BacktestRun, BacktestTrade, BacktestRepo


class TriggerRequest(BaseModel):
    start: Optional[str] = None
    end: Optional[str] = None
    model_id: Optional[str] = None


class TriggerResponse(BaseModel):
    run_id: int
    status: str


def make_router(session_dep: Callable, api_key_dep: Callable, backtest_scheduler=None) -> APIRouter:
    router = APIRouter()

    @router.post("/backtest/trigger", response_model=TriggerResponse, status_code=202)
    async def trigger_backtest(
        req: TriggerRequest,
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        if backtest_scheduler is None:
            raise HTTPException(status_code=503, detail="Backtest scheduler not available")
        end = req.end or date.today().isoformat()
        if req.start:
            start = req.start
        else:
            lookback = backtest_scheduler._settings.backtest.lookback_days
            start = (date.today() - timedelta(days=lookback)).isoformat()
        run_id = await backtest_scheduler.trigger(
            session=session,
            start=start,
            end=end,
            model_filter=req.model_id,
        )
        return TriggerResponse(run_id=run_id, status="queued")

    @router.get("/backtest/runs", response_model=list[BacktestRun])
    def list_runs(
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        return BacktestRepo(session).list_runs()

    @router.get("/backtest/runs/{run_id}", response_model=BacktestRun)
    def get_run(
        run_id: int,
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        run = session.get(BacktestRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return run

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

- [ ] **Step 4: Update `alphaTrade/api/app.py`** to pass `backtest_scheduler`

```python
def create_app(engine: Engine, health_state: HealthState, registry=None, backtest_scheduler=None) -> FastAPI:
    from alphaTrade.api.routers import positions, orders, signals, pnl, models, backtest, health, settings, equity, trades, stream, kill_switch

    app = FastAPI(title="alphaTrade API", version="1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "PUT", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    session_dep = make_session_dep(engine)
    api_key_dep = make_api_key_dep(engine)

    app.include_router(positions.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(orders.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(signals.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(pnl.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(models.make_router(session_dep, api_key_dep, registry), prefix="/api/v1")
    app.include_router(backtest.make_router(session_dep, api_key_dep, backtest_scheduler), prefix="/api/v1")
    app.include_router(health.make_router(health_state, api_key_dep), prefix="/api/v1")
    app.include_router(settings.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(equity.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(trades.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(stream.make_router(engine, api_key_dep), prefix="/api/v1")
    app.include_router(kill_switch.make_router(api_key_dep), prefix="/api/v1")

    return app


async def start_api_server(
    engine: Engine,
    health_state: HealthState,
    port: int = 8081,
    registry=None,
    backtest_scheduler=None,
) -> uvicorn.Server:
    app = create_app(engine, health_state, registry, backtest_scheduler)
    config = uvicorn.Config(app, host="0.0.0.0", port=port, loop="none", log_level="warning")
    server = uvicorn.Server(config)
    asyncio.create_task(server.serve())
    log.info("API server listening on :%d", port)
    return server
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/unit/test_api_backtest_trigger.py -v
```
Expected: PASS

- [ ] **Step 6: Run existing API tests to check no regression**

```bash
pytest tests/unit/test_api_routers.py -v
```
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add alphaTrade/api/routers/backtest.py alphaTrade/api/app.py tests/unit/test_api_backtest_trigger.py
git commit -m "feat(api): POST /backtest/trigger endpoint with async execution"
```

---

## Task 6: API — Schedule Management Endpoints

**Files:**
- Modify: `alphaTrade/api/routers/backtest.py`
- Create: `tests/unit/test_api_backtest_schedule.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_api_backtest_schedule.py
from __future__ import annotations
from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient
from sqlmodel import create_engine
from alphaTrade.store.db import run_migrations
from alphaTrade.health import HealthState
from alphaTrade.config import BacktestConfig, Settings


def _engine(tmp_path):
    db = tmp_path / "t.db"
    run_migrations(db)
    return create_engine(f"sqlite:///{db}")


def _scheduler(schedule_enabled=True, cron="0 2 * * *", lookback_days=30, jobs=None):
    s = MagicMock()
    s.get_status.return_value = {
        "schedule_enabled": schedule_enabled,
        "cron": cron,
        "lookback_days": lookback_days,
        "jobs": jobs or [],
    }
    s.get_model_status.return_value = {
        "model_id": "AAPL_v1",
        "disabled": False,
        "effective_cron": cron,
        "effective_lookback_days": lookback_days,
        "next_run_time": None,
    }
    return s


def _client(engine, scheduler):
    from alphaTrade.api.app import create_app
    return TestClient(create_app(engine, HealthState(), backtest_scheduler=scheduler))


def test_get_schedule_returns_status(tmp_path):
    sched = _scheduler(jobs=[{"id": "backtest_AAPL_v1", "next_run_time": None}])
    resp = _client(_engine(tmp_path), sched).get("/api/v1/backtest/schedule")
    assert resp.status_code == 200
    data = resp.json()
    assert data["schedule_enabled"] is True
    assert len(data["jobs"]) == 1


def test_patch_schedule_calls_update_global(tmp_path):
    sched = _scheduler()
    client = _client(_engine(tmp_path), sched)
    resp = client.patch("/api/v1/backtest/schedule", json={"schedule_enabled": False})
    assert resp.status_code == 200
    sched.update_global.assert_called_once_with(
        schedule_enabled=False, cron=None, lookback_days=None
    )


def test_patch_schedule_invalid_cron_returns_422(tmp_path):
    sched = _scheduler()
    client = _client(_engine(tmp_path), sched)
    resp = client.patch("/api/v1/backtest/schedule", json={"cron": "not-a-cron"})
    assert resp.status_code == 422


def test_get_model_schedule(tmp_path):
    sched = _scheduler()
    resp = _client(_engine(tmp_path), sched).get("/api/v1/backtest/schedule/AAPL_v1")
    assert resp.status_code == 200
    assert resp.json()["model_id"] == "AAPL_v1"


def test_patch_model_schedule_calls_update_model(tmp_path):
    sched = _scheduler()
    client = _client(_engine(tmp_path), sched)
    resp = client.patch("/api/v1/backtest/schedule/AAPL_v1", json={"disabled": True})
    assert resp.status_code == 200
    sched.update_model.assert_called_once_with(
        "AAPL_v1", disabled=True, cron=None, lookback_days=None
    )


def test_get_schedule_no_scheduler_returns_503(tmp_path):
    client = _client(_engine(tmp_path), None)
    resp = client.get("/api/v1/backtest/schedule")
    assert resp.status_code == 503
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/unit/test_api_backtest_schedule.py -v
```
Expected: FAIL

- [ ] **Step 3: Add schedule endpoints to `alphaTrade/api/routers/backtest.py`**

Add these Pydantic request models at the top of the file (after the existing imports):

```python
from apscheduler.triggers.cron import CronTrigger as _CronTrigger
from pydantic import field_validator


class GlobalScheduleUpdate(BaseModel):
    schedule_enabled: Optional[bool] = None
    cron: Optional[str] = None
    lookback_days: Optional[int] = None

    @field_validator("cron")
    @classmethod
    def _valid_cron(cls, v: str | None) -> str | None:
        if v is not None:
            try:
                _CronTrigger.from_crontab(v)
            except Exception:
                raise ValueError(f"Invalid cron expression: {v!r}")
        return v


class ModelScheduleUpdate(BaseModel):
    disabled: Optional[bool] = None
    cron: Optional[str] = None
    lookback_days: Optional[int] = None

    @field_validator("cron")
    @classmethod
    def _valid_cron(cls, v: str | None) -> str | None:
        if v is not None:
            try:
                _CronTrigger.from_crontab(v)
            except Exception:
                raise ValueError(f"Invalid cron expression: {v!r}")
        return v
```

Add these routes inside `make_router` (before the `return router` statement):

```python
    @router.get("/backtest/schedule")
    def get_schedule(_: None = Depends(api_key_dep)):
        if backtest_scheduler is None:
            raise HTTPException(status_code=503, detail="Backtest scheduler not available")
        return backtest_scheduler.get_status()

    @router.patch("/backtest/schedule")
    def patch_schedule(
        req: GlobalScheduleUpdate,
        _: None = Depends(api_key_dep),
    ):
        if backtest_scheduler is None:
            raise HTTPException(status_code=503, detail="Backtest scheduler not available")
        backtest_scheduler.update_global(
            schedule_enabled=req.schedule_enabled,
            cron=req.cron,
            lookback_days=req.lookback_days,
        )
        return backtest_scheduler.get_status()

    @router.get("/backtest/schedule/{model_id}")
    def get_model_schedule(model_id: str, _: None = Depends(api_key_dep)):
        if backtest_scheduler is None:
            raise HTTPException(status_code=503, detail="Backtest scheduler not available")
        return backtest_scheduler.get_model_status(model_id)

    @router.patch("/backtest/schedule/{model_id}")
    def patch_model_schedule(
        model_id: str,
        req: ModelScheduleUpdate,
        _: None = Depends(api_key_dep),
    ):
        if backtest_scheduler is None:
            raise HTTPException(status_code=503, detail="Backtest scheduler not available")
        backtest_scheduler.update_model(
            model_id,
            disabled=req.disabled,
            cron=req.cron,
            lookback_days=req.lookback_days,
        )
        return backtest_scheduler.get_model_status(model_id)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_api_backtest_schedule.py -v
```
Expected: PASS

- [ ] **Step 5: Run full backtest test suite**

```bash
pytest tests/unit/test_api_backtest_trigger.py tests/unit/test_api_backtest_schedule.py tests/unit/test_api_routers.py -v
```
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add alphaTrade/api/routers/backtest.py tests/unit/test_api_backtest_schedule.py
git commit -m "feat(api): GET/PATCH /backtest/schedule endpoints for runtime schedule management"
```

---

## Task 7: Wire `BacktestScheduler` into `main.py`

**Files:**
- Modify: `alphaTrade/main.py`

No new tests needed — this is wiring code exercised by integration tests.

- [ ] **Step 1: Add import at top of `main.py`**

Find the imports block and add:

```python
from alphaTrade.scheduler.backtest_scheduler import BacktestScheduler
```

- [ ] **Step 2: Create and start `BacktestScheduler` after `start_api_server`**

Find this block in `main.py`:

```python
    api_server = None
    try:
        from alphaTrade.api.app import start_api_server
        api_server = await start_api_server(engine, health_state, port=settings.api_port, registry=registry)
    except Exception as exc:
        log.error("API server failed to start on :%d: %s", settings.api_port, exc)
```

Replace with:

```python
    backtest_scheduler: BacktestScheduler | None = None
    api_server = None
    try:
        models_list = list(registry.by_run_name.values()) if registry else []
        backtest_scheduler = BacktestScheduler(
            engine=engine,
            settings=settings,
            models=models_list,
            models_dir=settings.models_dir,
        )
        backtest_scheduler.start()
        log.info("BacktestScheduler started (enabled=%s)", settings.backtest.schedule_enabled)
    except Exception as exc:
        log.error("BacktestScheduler failed to start: %s", exc)

    try:
        from alphaTrade.api.app import start_api_server
        api_server = await start_api_server(
            engine, health_state,
            port=settings.api_port,
            registry=registry,
            backtest_scheduler=backtest_scheduler,
        )
    except Exception as exc:
        log.error("API server failed to start on :%d: %s", settings.api_port, exc)
```

- [ ] **Step 3: Add shutdown in cleanup block**

Find the cleanup section after `await asyncio.gather(*tasks)`:

```python
    if api_server is not None:
        api_server.should_exit = True
    if health_runner is not None:
        await health_runner.cleanup()
    alert_manager.shutdown(timeout=5.0)
    log.info("Graceful shutdown complete.")
```

Add backtest scheduler shutdown before `api_server.should_exit`:

```python
    if backtest_scheduler is not None:
        backtest_scheduler.shutdown()
    if api_server is not None:
        api_server.should_exit = True
    if health_runner is not None:
        await health_runner.cleanup()
    alert_manager.shutdown(timeout=5.0)
    log.info("Graceful shutdown complete.")
```

- [ ] **Step 4: Check registry structure for `models_list`**

The registry stores models — verify the correct attribute to use:

```bash
grep -n "by_run_name\|scan_models\|registry" /home/preeth/projects/alphaTrade/alphaTrade/main.py | head -20
```

If `registry.by_run_name` returns `{run_name: (manifest, model)}` dicts, extract values as:

```python
models_list = list(registry.by_run_name.values()) if registry else []
```

If it returns a different structure, adjust accordingly.

- [ ] **Step 5: Run full unit test suite**

```bash
pytest tests/unit/ -v --tb=short 2>&1 | tail -30
```
Expected: all existing tests PASS, new tests PASS

- [ ] **Step 6: Run migration test**

```bash
pytest tests/unit/test_backtest_status_migration.py tests/unit/test_migrations.py -v
```
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add alphaTrade/main.py
git commit -m "feat(main): start BacktestScheduler on bot startup"
```

---

## Final Verification

- [ ] **Run full test suite**

```bash
pytest tests/ -v --tb=short 2>&1 | tail -40
```
Expected: all tests PASS

- [ ] **Smoke test — verify endpoints exist**

```bash
cd /home/preeth/projects/alphaTrade
python -c "
from sqlmodel import create_engine
from alphaTrade.store.db import run_migrations
from alphaTrade.api.app import create_app
from alphaTrade.health import HealthState
from pathlib import Path
import tempfile, os
with tempfile.TemporaryDirectory() as d:
    db = Path(d) / 'test.db'
    run_migrations(db)
    engine = create_engine(f'sqlite:///{db}')
    app = create_app(engine, HealthState())
    routes = [r.path for r in app.routes]
    assert '/api/v1/backtest/trigger' in routes, routes
    assert '/api/v1/backtest/schedule' in routes, routes
    assert '/api/v1/backtest/schedule/{model_id}' in routes, routes
    print('All routes present')
"
```
Expected: `All routes present`
