# Backtest Model Run Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `BacktestModelRun` table that records one row per model per backtest run, giving visibility into models that ran but produced zero trades.

**Architecture:** New `BacktestModelRun` SQLModel written via `BacktestRepo.record_model_run()` at the end of each model's execution in `engine.py`. A new router endpoint exposes per-model results. Alembic migration creates the table. No changes to existing endpoints or data.

**Tech Stack:** SQLModel, SQLAlchemy, Alembic (SQLite), FastAPI, pytest

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `alphaTrade/store/migrations/versions/0010_backtest_model_runs.py` | Alembic migration — creates `backtestmodelrun` table |
| Modify | `alphaTrade/store/repos.py` | Add `BacktestModelRun` SQLModel + `BacktestRepo.record_model_run()` + `BacktestRepo.model_runs_for_run()` |
| Modify | `alphaTrade/backtest/engine.py` | Call `repo.record_model_run()` after each `_run_single_model` |
| Modify | `alphaTrade/api/routers/backtest.py` | Add `GET /backtest/runs/{run_id}/models` endpoint |
| Create | `tests/unit/test_backtest_model_run.py` | Unit tests for repo methods and engine recording |
| Create | `tests/unit/test_api_backtest_model_runs.py` | API endpoint tests |

---

### Task 1: Migration — create `backtestmodelrun` table

**Files:**
- Create: `alphaTrade/store/migrations/versions/0010_backtest_model_runs.py`

- [ ] **Step 1: Write the migration file**

```python
"""Add backtestmodelrun table for per-model run tracking.

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-19
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backtestmodelrun",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("run_id", sa.Integer(), nullable=False, index=True),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("ticker", sa.String(), nullable=False),
        sa.Column("interval", sa.String(), nullable=False),
        sa.Column("trade_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(), nullable=False, server_default="ran"),
        sa.Column("error_msg", sa.String(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_table("backtestmodelrun")
```

- [ ] **Step 2: Run migration against a fresh test DB to verify it applies cleanly**

```bash
cd /home/preeth/projects/alphaTrade
python3 -c "
from alphaTrade.store.db import run_migrations
from pathlib import Path
import tempfile, os
with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
    p = Path(f.name)
run_migrations(p)
import sqlite3
conn = sqlite3.connect(str(p))
tables = [r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")]
print('Tables:', tables)
assert 'backtestmodelrun' in tables, 'Migration failed'
print('OK')
os.unlink(p)
"
```

Expected output:
```
Tables: [..., 'backtestmodelrun']
OK
```

- [ ] **Step 3: Commit**

```bash
git add alphaTrade/store/migrations/versions/0010_backtest_model_runs.py
git commit -m "feat(store): migration 0010 — add backtestmodelrun table"
```

---

### Task 2: ORM model + repo methods

**Files:**
- Modify: `alphaTrade/store/repos.py` (after `BacktestTrade` class, around line 290)

- [ ] **Step 1: Write failing tests first**

Create `tests/unit/test_backtest_model_run.py`:

```python
"""Tests for BacktestModelRun ORM model and BacktestRepo methods."""
from __future__ import annotations
import pytest
from sqlmodel import create_engine, Session
from alphaTrade.store.db import run_migrations
from alphaTrade.store.repos import BacktestRepo, BacktestModelRun


@pytest.fixture()
def session(tmp_path):
    db = tmp_path / "test.db"
    run_migrations(db)
    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as s:
        yield s


def test_record_model_run_creates_row(session):
    repo = BacktestRepo(session)
    run_id = repo.create_run(start="2026-04-01", end="2026-05-01")
    repo.record_model_run(
        run_id=run_id,
        model_id="AAPL_Transformer_test1",
        ticker="AAPL",
        interval="1d",
        trade_count=0,
        status="ran",
        error_msg="",
    )
    rows = repo.model_runs_for_run(run_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.model_id == "AAPL_Transformer_test1"
    assert row.ticker == "AAPL"
    assert row.interval == "1d"
    assert row.trade_count == 0
    assert row.status == "ran"
    assert row.error_msg == ""


def test_record_model_run_multiple_models(session):
    repo = BacktestRepo(session)
    run_id = repo.create_run(start="2026-04-01", end="2026-05-01")
    repo.record_model_run(run_id=run_id, model_id="m1", ticker="AAPL", interval="1d", trade_count=5, status="ran", error_msg="")
    repo.record_model_run(run_id=run_id, model_id="m2", ticker="AAPL", interval="1m", trade_count=0, status="no_data", error_msg="not enough bars")
    rows = repo.model_runs_for_run(run_id)
    assert len(rows) == 2
    statuses = {r.model_id: r.status for r in rows}
    assert statuses["m1"] == "ran"
    assert statuses["m2"] == "no_data"


def test_model_runs_for_run_empty(session):
    repo = BacktestRepo(session)
    run_id = repo.create_run(start="2026-04-01", end="2026-05-01")
    rows = repo.model_runs_for_run(run_id)
    assert rows == []


def test_model_runs_scoped_to_run(session):
    repo = BacktestRepo(session)
    run_id_a = repo.create_run(start="2026-04-01", end="2026-05-01")
    run_id_b = repo.create_run(start="2026-04-01", end="2026-05-01")
    repo.record_model_run(run_id=run_id_a, model_id="m1", ticker="AAPL", interval="1d", trade_count=1, status="ran", error_msg="")
    repo.record_model_run(run_id=run_id_b, model_id="m2", ticker="AAPL", interval="1m", trade_count=2, status="ran", error_msg="")
    assert len(repo.model_runs_for_run(run_id_a)) == 1
    assert len(repo.model_runs_for_run(run_id_b)) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/preeth/projects/alphaTrade
python -m pytest tests/unit/test_backtest_model_run.py -v 2>&1 | head -30
```

Expected: `ImportError` or `AttributeError` — `BacktestModelRun` not defined yet.

- [ ] **Step 3: Add `BacktestModelRun` SQLModel to `repos.py`**

In `alphaTrade/store/repos.py`, add this class immediately after `BacktestTrade` (around line 291), before the repo classes:

```python
class BacktestModelRun(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(index=True)
    model_id: str
    ticker: str
    interval: str
    trade_count: int = 0
    status: str = "ran"   # ran | no_data | failed
    error_msg: str = ""
```

- [ ] **Step 4: Add `record_model_run` and `model_runs_for_run` to `BacktestRepo`**

In `alphaTrade/store/repos.py`, add these two methods to the `BacktestRepo` class (after `trades_for_run`, around line 439):

```python
    def record_model_run(
        self,
        run_id: int,
        model_id: str,
        ticker: str,
        interval: str,
        trade_count: int,
        status: str,
        error_msg: str = "",
    ) -> None:
        self._s.add(BacktestModelRun(
            run_id=run_id,
            model_id=model_id,
            ticker=ticker,
            interval=interval,
            trade_count=trade_count,
            status=status,
            error_msg=error_msg,
        ))
        self._s.commit()

    def model_runs_for_run(self, run_id: int) -> list[BacktestModelRun]:
        return list(self._s.exec(
            select(BacktestModelRun).where(BacktestModelRun.run_id == run_id)
        ).all())
```

- [ ] **Step 5: Run tests — expect pass**

```bash
cd /home/preeth/projects/alphaTrade
python -m pytest tests/unit/test_backtest_model_run.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 6: Commit**

```bash
git add alphaTrade/store/repos.py tests/unit/test_backtest_model_run.py
git commit -m "feat(store): BacktestModelRun model and repo methods"
```

---

### Task 3: Engine records per-model outcomes

**Files:**
- Modify: `alphaTrade/backtest/engine.py` (function `run_backtest`, lines 69–111)

- [ ] **Step 1: Write failing test**

Add to `tests/unit/test_backtest_model_run.py` (append to the file):

```python
# Engine integration test
from unittest.mock import MagicMock, patch
from pathlib import Path
import pandas as pd
from alphaTrade.backtest.engine import run_backtest
from alphaTrade.config import BacktestConfig


def _make_manifest(run_name="m1", ticker="AAPL", interval="1d", window=3):
    m = MagicMock()
    m.run_name = run_name
    m.ticker = ticker
    m.interval = interval
    m.window = window
    m.feature_names = []
    return m


def _make_df(n=20):
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "Open": [100.0] * n,
        "High": [105.0] * n,
        "Low": [95.0] * n,
        "Close": [102.0] * n,
        "Volume": [1000] * n,
    }, index=idx)


def test_run_backtest_records_model_run_zero_trades(tmp_path):
    """Engine records a BacktestModelRun row even when model produces 0 trades."""
    db = tmp_path / "test.db"
    run_migrations(db)
    engine_db = create_engine(f"sqlite:///{db}")

    manifest = _make_manifest()
    mock_model = MagicMock()
    cfg = BacktestConfig()

    provider = MagicMock()
    provider.fetch_ohlcv_range.return_value = _make_df(20)

    with patch("alphaTrade.backtest.engine.scan_models", return_value=[(manifest, mock_model)]):
        with patch("alphaTrade.backtest.engine._infer", return_value="HOLD"):
            with Session(engine_db) as session:
                run_backtest(
                    session=session,
                    models_dir=Path("/fake"),
                    start="2026-01-10",
                    end="2026-01-20",
                    cfg=cfg,
                    provider=provider,
                )

    with Session(engine_db) as session:
        repo = BacktestRepo(session)
        runs = repo.list_runs()
        assert len(runs) == 1
        model_runs = repo.model_runs_for_run(runs[0].id)
        assert len(model_runs) == 1
        mr = model_runs[0]
        assert mr.model_id == "m1"
        assert mr.ticker == "AAPL"
        assert mr.interval == "1d"
        assert mr.trade_count == 0
        assert mr.status == "ran"


def test_run_backtest_records_no_data_status(tmp_path):
    """Engine records no_data status when provider returns None."""
    db = tmp_path / "test.db"
    run_migrations(db)
    engine_db = create_engine(f"sqlite:///{db}")

    manifest = _make_manifest()
    mock_model = MagicMock()
    cfg = BacktestConfig()
    provider = MagicMock()
    provider.fetch_ohlcv_range.return_value = None  # simulate no data

    with patch("alphaTrade.backtest.engine.scan_models", return_value=[(manifest, mock_model)]):
        with Session(engine_db) as session:
            run_backtest(
                session=session,
                models_dir=Path("/fake"),
                start="2026-01-10",
                end="2026-01-20",
                cfg=cfg,
                provider=provider,
            )

    with Session(engine_db) as session:
        repo = BacktestRepo(session)
        runs = repo.list_runs()
        model_runs = repo.model_runs_for_run(runs[0].id)
        assert len(model_runs) == 1
        assert model_runs[0].status == "no_data"
        assert model_runs[0].trade_count == 0
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/preeth/projects/alphaTrade
python -m pytest tests/unit/test_backtest_model_run.py::test_run_backtest_records_model_run_zero_trades tests/unit/test_backtest_model_run.py::test_run_backtest_records_no_data_status -v 2>&1 | tail -20
```

Expected: FAIL — no `BacktestModelRun` rows recorded.

- [ ] **Step 3: Update `run_backtest` in `engine.py` to record per-model outcomes**

Replace the `for manifest, model in models:` loop in `run_backtest` (lines 96–109) with:

```python
    for manifest, model in models:
        log.info("backtest: running %s (%s, %s)", manifest.run_name, manifest.ticker, manifest.interval)
        status = "ran"
        error_msg = ""
        trades = _run_single_model(
            manifest=manifest,
            model=model,
            provider=provider,
            start=start,
            end=end,
            cfg=cfg,
        )
        if trades is None:
            # _run_single_model returns [] not None, but guard defensively
            trades = []
            status = "failed"
        # Detect no_data: _run_single_model returns [] with a warning log when df is None or too short
        # We distinguish it by checking provider directly is not needed — status stays "ran" with 0 trades
        # unless we expose the reason. To capture no_data, check the provider result here.
        for t in trades:
            repo.record_trade(run_id=run_id, **t)
        repo.record_model_run(
            run_id=run_id,
            model_id=manifest.run_name,
            ticker=manifest.ticker,
            interval=manifest.interval,
            trade_count=len(trades),
            status=status,
            error_msg=error_msg,
        )
        all_trades.extend(trades)
        log.info("backtest: %s → %d trades", manifest.run_name, len(trades))
```

The `no_data` status requires detecting the insufficient-data case. Refactor `_run_single_model` to return a status sentinel. Replace its signature and early-return:

In `_run_single_model` (line 114), change the return type annotation and the early return at lines 126–128:

```python
def _run_single_model(
    manifest: Manifest,
    model: OnnxModel,
    provider: DataProvider,
    start: str,
    end: str,
    cfg: BacktestConfig,
) -> tuple[list[dict], str]:
    """Walk forward bar-by-bar for one model. Returns (trades, status)."""
    warmup_bars = manifest.window + 50
    df = provider.fetch_ohlcv_range(manifest.ticker, manifest.interval, start=start, end=end, extra_bars=warmup_bars)
    if df is None or len(df) < manifest.window + 2:
        log.warning("backtest: not enough data for %s", manifest.run_name)
        return [], "no_data"
```

And at the end of `_run_single_model`, change `return trades` to `return trades, "ran"`.

Update `run_backtest`'s loop to unpack the tuple:

```python
    for manifest, model in models:
        log.info("backtest: running %s (%s, %s)", manifest.run_name, manifest.ticker, manifest.interval)
        trades, status = _run_single_model(
            manifest=manifest,
            model=model,
            provider=provider,
            start=start,
            end=end,
            cfg=cfg,
        )
        for t in trades:
            repo.record_trade(run_id=run_id, **t)
        repo.record_model_run(
            run_id=run_id,
            model_id=manifest.run_name,
            ticker=manifest.ticker,
            interval=manifest.interval,
            trade_count=len(trades),
            status=status,
            error_msg="",
        )
        all_trades.extend(trades)
        log.info("backtest: %s → %d trades", manifest.run_name, len(trades))
```

- [ ] **Step 4: Run new tests**

```bash
cd /home/preeth/projects/alphaTrade
python -m pytest tests/unit/test_backtest_model_run.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Run full existing backtest test suite to check for regressions**

```bash
cd /home/preeth/projects/alphaTrade
python -m pytest tests/unit/test_backtest_engine.py tests/unit/test_backtest_engine_filter.py tests/unit/test_backtest_scheduler.py -v
```

Expected: all pass. If `test_backtest_engine_filter.py` calls `_run_single_model` directly and checks return value, update those call sites to unpack the tuple.

- [ ] **Step 6: Fix any test_backtest_engine_filter.py regressions**

Check if `_run_single_model` is called directly in tests:

```bash
grep -n "_run_single_model" /home/preeth/projects/alphaTrade/tests/unit/test_backtest_engine_filter.py
```

If found, update each call to unpack: `trades, status = _run_single_model(...)` or `trades, _ = _run_single_model(...)`.

- [ ] **Step 7: Commit**

```bash
git add alphaTrade/backtest/engine.py tests/unit/test_backtest_model_run.py
git commit -m "feat(engine): record BacktestModelRun per model after each backtest"
```

---

### Task 4: API endpoint — `GET /backtest/runs/{run_id}/models`

**Files:**
- Modify: `alphaTrade/api/routers/backtest.py`
- Create: `tests/unit/test_api_backtest_model_runs.py`

- [ ] **Step 1: Write failing test**

Create `tests/unit/test_api_backtest_model_runs.py`:

```python
"""Tests for GET /backtest/runs/{run_id}/models endpoint."""
from __future__ import annotations
import pytest
from fastapi.testclient import TestClient
from sqlmodel import create_engine, Session
from alphaTrade.store.db import run_migrations
from alphaTrade.store.repos import BacktestRepo
from alphaTrade.health import HealthState


def _engine(tmp_path):
    db = tmp_path / "t.db"
    run_migrations(db)
    return create_engine(f"sqlite:///{db}")


def _client(engine):
    from alphaTrade.api.app import create_app
    return TestClient(create_app(engine, HealthState(), backtest_scheduler=None))


def test_get_model_runs_empty(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        run_id = BacktestRepo(session).create_run(start="2026-04-01", end="2026-05-01")
    client = _client(engine)
    resp = client.get(f"/api/v1/backtest/runs/{run_id}/models", headers={"X-API-Key": ""})
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_model_runs_returns_rows(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        repo = BacktestRepo(session)
        run_id = repo.create_run(start="2026-04-01", end="2026-05-01")
        repo.record_model_run(run_id=run_id, model_id="AAPL_Transformer_test1", ticker="AAPL", interval="1d", trade_count=0, status="ran", error_msg="")
        repo.record_model_run(run_id=run_id, model_id="AAPL_Transformer_test2", ticker="AAPL", interval="1m", trade_count=3, status="ran", error_msg="")
    client = _client(engine)
    resp = client.get(f"/api/v1/backtest/runs/{run_id}/models", headers={"X-API-Key": ""})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    by_id = {r["model_id"]: r for r in data}
    assert by_id["AAPL_Transformer_test1"]["trade_count"] == 0
    assert by_id["AAPL_Transformer_test1"]["status"] == "ran"
    assert by_id["AAPL_Transformer_test2"]["trade_count"] == 3
    assert by_id["AAPL_Transformer_test2"]["interval"] == "1m"


def test_get_model_runs_404_for_missing_run(tmp_path):
    client = _client(_engine(tmp_path))
    resp = client.get("/api/v1/backtest/runs/9999/models", headers={"X-API-Key": ""})
    assert resp.status_code == 404


def test_get_model_runs_scoped_to_run(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        repo = BacktestRepo(session)
        run_a = repo.create_run(start="2026-04-01", end="2026-05-01")
        run_b = repo.create_run(start="2026-04-01", end="2026-05-01")
        repo.record_model_run(run_id=run_a, model_id="m1", ticker="AAPL", interval="1d", trade_count=1, status="ran", error_msg="")
        repo.record_model_run(run_id=run_b, model_id="m2", ticker="AAPL", interval="1m", trade_count=2, status="ran", error_msg="")
    client = _client(engine)
    resp_a = client.get(f"/api/v1/backtest/runs/{run_a}/models", headers={"X-API-Key": ""})
    assert len(resp_a.json()) == 1
    assert resp_a.json()[0]["model_id"] == "m1"
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/preeth/projects/alphaTrade
python -m pytest tests/unit/test_api_backtest_model_runs.py -v 2>&1 | tail -20
```

Expected: FAIL — endpoint not found (404).

- [ ] **Step 3: Add endpoint to `backtest.py` router**

In `alphaTrade/api/routers/backtest.py`, add this import at the top alongside the existing import:

```python
from alphaTrade.store.repos import BacktestRun, BacktestTrade, BacktestRepo, BacktestModelRun
```

Then add the new endpoint inside `make_router`, after the existing `list_trades` route (after line 102):

```python
    @router.get("/backtest/runs/{run_id}/models", response_model=list[BacktestModelRun])
    def list_model_runs(
        run_id: int,
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        if session.get(BacktestRun, run_id) is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return BacktestRepo(session).model_runs_for_run(run_id)
```

- [ ] **Step 4: Run endpoint tests**

```bash
cd /home/preeth/projects/alphaTrade
python -m pytest tests/unit/test_api_backtest_model_runs.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Run full test suite**

```bash
cd /home/preeth/projects/alphaTrade
python -m pytest tests/ -v --tb=short 2>&1 | tail -40
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add alphaTrade/api/routers/backtest.py tests/unit/test_api_backtest_model_runs.py
git commit -m "feat(api): GET /backtest/runs/{run_id}/models endpoint"
```

---

### Task 5: Verify end-to-end against live DB

- [ ] **Step 1: Apply migration to live `state.db`**

```bash
cd /home/preeth/projects/alphaTrade
python3 -c "
from alphaTrade.store.db import run_migrations
from pathlib import Path
run_migrations(Path('./state.db'))
print('Migration applied')
"
```

Expected: `Migration applied` (no errors).

- [ ] **Step 2: Verify table exists in live DB**

```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('state.db')
cols = [r[1] for r in conn.execute('PRAGMA table_info(backtestmodelrun)')]
print('Columns:', cols)
assert 'run_id' in cols
print('OK')
"
```

Expected:
```
Columns: ['id', 'run_id', 'model_id', 'ticker', 'interval', 'trade_count', 'status', 'error_msg']
OK
```

- [ ] **Step 3: Confirm existing run 24 has no model_run rows (expected — pre-feature data)**

```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('state.db')
rows = conn.execute('SELECT * FROM backtestmodelrun WHERE run_id=24').fetchall()
print('Run 24 model_runs:', rows)
"
```

Expected: `Run 24 model_runs: []` — old runs don't get backfilled, by design.

- [ ] **Step 4: Final commit (push)**

```bash
git pull --rebase
git push
```
