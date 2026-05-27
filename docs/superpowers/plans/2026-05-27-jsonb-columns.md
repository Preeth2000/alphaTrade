# JSONB Columns Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert 5 TEXT/String JSON columns to native `sa.JSON` (cross-DB) with Postgres-side JSONB migration, removing all app-layer `json.dumps/loads` boilerplate.

**Architecture:** New Alembic migration 0016 ALTERs the 5 columns to JSONB on Postgres (no-op on SQLite via dialect guard). SQLAlchemy model fields switch to `sa.JSON` type with Python-native `dict`/`list` defaults. All `json.dumps/loads` call sites removed. Tests updated to use native types.

**Tech Stack:** SQLAlchemy `JSON` type, `sqlalchemy.dialects.postgresql.JSONB`, Alembic `op.get_bind().dialect.name`, SQLModel `Field(default_factory=..., sa_column=Column(JSON))`

---

## Files

- Create: `alphaTrade/store/migrations/versions/0016_jsonb_columns.py`
- Modify: `alphaTrade/store/repos.py` — 5 model fields + `create_run` signature + remove validator
- Modify: `alphaTrade/risk/performance.py` — remove `json.loads/dumps`
- Modify: `alphaTrade/api/routers/retirement.py:208` — `"[]"` → `[]`
- Modify: `alphaTrade/main.py:166,544` — remove `json.loads/dumps`
- Modify: `alphaTrade/backtest/engine.py:139` — `model_dump_json()` → `model_dump()`
- Modify: `alphaTrade/scheduler/backtest_scheduler.py:114,225` — `model_dump_json()` → `model_dump()`
- Modify: `tests/unit/test_model_performance.py` — remove `json.loads`, update assertion
- Modify: `tests/unit/test_retirement_api.py:148,161` — string → list literals
- Modify: `tests/unit/test_new_orm_models.py:50` — `"[]"` → `[]`
- Modify: `tests/unit/test_apply_bot_settings.py:121` — JSON string → dict

---

### Task 1: Alembic migration 0016

**Files:**
- Create: `alphaTrade/store/migrations/versions/0016_jsonb_columns.py`

- [ ] **Step 1: Write the migration file**

```python
"""Convert JSON text columns to JSONB (Postgres only).

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-27
"""
from __future__ import annotations

from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.alter_column("signal", "raw_json",
                    type_=postgresql.JSONB, postgresql_using="raw_json::jsonb")
    op.alter_column("pnlsnapshot", "positions_json",
                    type_=postgresql.JSONB, postgresql_using="positions_json::jsonb")
    op.alter_column("modelperformance", "rolling_trades_json",
                    type_=postgresql.JSONB, postgresql_using="rolling_trades_json::jsonb")
    op.alter_column("backtestrun", "config_json",
                    type_=postgresql.JSONB, postgresql_using="config_json::jsonb")
    op.alter_column("botsettings", "unbalanced_sector_overrides",
                    type_=postgresql.JSONB,
                    postgresql_using="unbalanced_sector_overrides::jsonb",
                    nullable=True)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    from sqlalchemy import String
    op.alter_column("signal", "raw_json", type_=String)
    op.alter_column("pnlsnapshot", "positions_json", type_=String)
    op.alter_column("modelperformance", "rolling_trades_json", type_=String)
    op.alter_column("backtestrun", "config_json", type_=String)
    op.alter_column("botsettings", "unbalanced_sector_overrides", type_=String, nullable=True)
```

- [ ] **Step 2: Verify migration runs against SQLite without error**

```bash
cd /home/preeth/projects/alphaTrade
python -c "
from alphaTrade.store.db import run_migrations
import tempfile, pathlib
with tempfile.TemporaryDirectory() as d:
    run_migrations(pathlib.Path(d) / 'test.db')
    print('SQLite migration OK')
"
```
Expected: `SQLite migration OK`

- [ ] **Step 3: Verify migration runs against Postgres**

```bash
docker start alphaframe-postgres-1
python -c "
from alphaTrade.store.db import run_migrations
run_migrations('postgresql+psycopg2://platform:changeme@localhost:5432/alphatrade')
print('Postgres migration OK')
"
```
Expected: `Postgres migration OK`

- [ ] **Step 4: Confirm columns are JSONB in Postgres**

```bash
docker exec alphaframe-postgres-1 psql -U platform -d alphatrade -c "
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE column_name IN ('raw_json','positions_json','rolling_trades_json','config_json','unbalanced_sector_overrides')
ORDER BY table_name, column_name;
"
```
Expected: all 5 rows show `data_type = jsonb`

- [ ] **Step 5: Commit**

```bash
git add alphaTrade/store/migrations/versions/0016_jsonb_columns.py
git commit -m "feat(store): migration 0016 — convert JSON text columns to JSONB"
```

---

### Task 2: Update model fields in repos.py

**Files:**
- Modify: `alphaTrade/store/repos.py`

- [ ] **Step 1: Update imports — add `JSON` to sqlalchemy imports**

In `repos.py` line 7, change:
```python
from sqlalchemy import Boolean, Column, Float, Integer, String
```
to:
```python
from sqlalchemy import Boolean, Column, Float, Integer, JSON, String
```

Also remove `from pydantic import field_validator` (line 9) — the validator being removed was the only use. Verify no other `field_validator` usage first:

```bash
grep -n "field_validator" alphaTrade/store/repos.py
```
If only line 9 and the validator method, remove the import.

- [ ] **Step 2: Update `Signal.raw_json`**

Change line 19:
```python
raw_json: str = ""   # JSON-encoded logits list for audit
```
to:
```python
raw_json: list = Field(default_factory=list, sa_column=Column(JSON, nullable=False, server_default="[]"))
```

- [ ] **Step 3: Update `PnlSnapshot.positions_json`**

Change line 247:
```python
positions_json: str = "{}"
```
to:
```python
positions_json: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False, server_default="{}"))
```

- [ ] **Step 4: Update `ModelPerformance.rolling_trades_json`**

Change line 258:
```python
rolling_trades_json: str = "[]"  # JSON list of last N realized_pnl values
```
to:
```python
rolling_trades_json: list = Field(default_factory=list, sa_column=Column(JSON, nullable=False, server_default="[]"))
```

- [ ] **Step 5: Update `BacktestRun.config_json`**

Change line 277:
```python
config_json: str = "{}"
```
to:
```python
config_json: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False, server_default="{}"))
```

- [ ] **Step 6: Update `BotSettings.unbalanced_sector_overrides` and remove validator**

Change line 555:
```python
unbalanced_sector_overrides: Optional[str] = Field(default=None, sa_column=Column(String, nullable=True))
```
to:
```python
unbalanced_sector_overrides: Optional[dict] = Field(default=None, sa_column=Column(JSON, nullable=True))
```

Delete lines 577–588 (the entire `_validate_sector_overrides_json` method):
```python
    @field_validator("unbalanced_sector_overrides", mode="before")
    @classmethod
    def _validate_sector_overrides_json(cls, v):
        if v is not None:
            import json
            try:
                parsed = json.loads(v)
            except (json.JSONDecodeError, TypeError) as exc:
                raise ValueError(f"unbalanced_sector_overrides must be valid JSON: {exc}") from exc
            if not isinstance(parsed, dict):
                raise ValueError("unbalanced_sector_overrides must be a JSON object (dict)")
        return v
```

- [ ] **Step 7: Update `BacktestRepo.create_run` signature**

Change line 432:
```python
def create_run(self, start: str, end: str, config_json: str = "{}", status: str = "done") -> int:
```
to:
```python
def create_run(self, start: str, end: str, config_json: dict | None = None, status: str = "done") -> int:
```

And the body line 434:
```python
run = BacktestRun(start_date=start, end_date=end, config_json=config_json, status=status)
```
to:
```python
run = BacktestRun(start_date=start, end_date=end, config_json=config_json or {}, status=status)
```

- [ ] **Step 8: Run existing tests to see which fail (expected red)**

```bash
pytest tests/unit/test_model_performance.py tests/unit/test_retirement_api.py tests/unit/test_new_orm_models.py tests/unit/test_apply_bot_settings.py -v 2>&1 | tail -30
```
Expected: multiple failures due to type mismatches — these are the call sites to fix next.

---

### Task 3: Fix performance.py

**Files:**
- Modify: `alphaTrade/risk/performance.py`

- [ ] **Step 1: Remove `json` import and `json.loads/dumps` calls**

Remove line 4: `import json`

Change lines 40–45:
```python
    trades: list[float] = json.loads(perf.rolling_trades_json)
    trades.append(realized_pnl)
    if len(trades) > cfg.lookback_trades:
        trades = trades[-cfg.lookback_trades:]

    perf.rolling_trades_json = json.dumps(trades)
```
to:
```python
    trades: list[float] = list(perf.rolling_trades_json)
    trades.append(realized_pnl)
    if len(trades) > cfg.lookback_trades:
        trades = trades[-cfg.lookback_trades:]

    perf.rolling_trades_json = trades
```

Change line 81:
```python
    trades: list[float] = json.loads(perf.rolling_trades_json)
```
to:
```python
    trades: list[float] = list(perf.rolling_trades_json)
```

- [ ] **Step 2: Run performance tests**

```bash
pytest tests/unit/test_model_performance.py -v
```
Expected: tests still fail due to test-side `json.loads` — fixed in Task 6.

---

### Task 4: Fix retirement.py

**Files:**
- Modify: `alphaTrade/api/routers/retirement.py`

- [ ] **Step 1: Change string literal to list**

Change line 208:
```python
        perf.rolling_trades_json = "[]"
```
to:
```python
        perf.rolling_trades_json = []
```

---

### Task 5: Fix main.py

**Files:**
- Modify: `alphaTrade/main.py`

- [ ] **Step 1: Fix `unbalanced_sector_overrides` read (line 166)**

Change:
```python
        settings.risk.unbalanced.sector_overrides = json.loads(db_s.unbalanced_sector_overrides)
```
to:
```python
        settings.risk.unbalanced.sector_overrides = db_s.unbalanced_sector_overrides
```

- [ ] **Step 2: Fix `raw_json` write (line 544)**

Change:
```python
                    raw_json=json.dumps([l.tolist() for l in ticker_logits[yf_ticker]]),
```
to:
```python
                    raw_json=[l.tolist() for l in ticker_logits[yf_ticker]],
```

- [ ] **Step 3: Check if `json` import still needed in main.py**

```bash
grep -n "^import json\|json\." alphaTrade/main.py | grep -v "raw_json\|sector_overrides"
```
If no other `json.` usages remain, remove the `import json` line.

---

### Task 6: Fix engine.py and scheduler.py

**Files:**
- Modify: `alphaTrade/backtest/engine.py`
- Modify: `alphaTrade/scheduler/backtest_scheduler.py`

- [ ] **Step 1: Fix engine.py line 139**

Change:
```python
        run_id = repo.create_run(start=start, end=end, config_json=cfg.model_dump_json())
```
to:
```python
        run_id = repo.create_run(start=start, end=end, config_json=cfg.model_dump())
```

- [ ] **Step 2: Fix scheduler.py line 114**

Change:
```python
            config_json=self._settings.backtest.model_dump_json(),
```
to:
```python
            config_json=self._settings.backtest.model_dump(),
```

- [ ] **Step 3: Fix scheduler.py line 225**

Change:
```python
                    config_json=settings.backtest.model_dump_json(),
```
to:
```python
                    config_json=settings.backtest.model_dump(),
```

- [ ] **Step 4: Commit all app-layer changes so far**

```bash
git add alphaTrade/store/repos.py alphaTrade/risk/performance.py \
        alphaTrade/api/routers/retirement.py alphaTrade/main.py \
        alphaTrade/backtest/engine.py alphaTrade/scheduler/backtest_scheduler.py
git commit -m "feat(store): convert JSON text columns to native dict/list — remove json.dumps/loads"
```

---

### Task 7: Fix tests

**Files:**
- Modify: `tests/unit/test_model_performance.py`
- Modify: `tests/unit/test_retirement_api.py`
- Modify: `tests/unit/test_new_orm_models.py`
- Modify: `tests/unit/test_apply_bot_settings.py`

- [ ] **Step 1: Fix `test_model_performance.py` line 54**

Remove `import json` (line 2) if it's only used for the one call.

Change lines 53–56:
```python
    with Session(engine) as s:
        perf = ModelPerformanceRepo(s).get_or_create("model_a")
        trades = json.loads(perf.rolling_trades_json)
    assert len(trades) == 3
    assert trades == [20.0, 30.0, 40.0]
```
to:
```python
    with Session(engine) as s:
        perf = ModelPerformanceRepo(s).get_or_create("model_a")
        trades = perf.rolling_trades_json
    assert len(trades) == 3
    assert trades == [20.0, 30.0, 40.0]
```

- [ ] **Step 2: Fix `test_retirement_api.py` lines 148 and 161**

Change line 148:
```python
            perf.rolling_trades_json = "[-50, -100]"
```
to:
```python
            perf.rolling_trades_json = [-50, -100]
```

Change line 161:
```python
            assert perf.rolling_trades_json == "[]"
```
to:
```python
            assert perf.rolling_trades_json == []
```

- [ ] **Step 3: Fix `test_new_orm_models.py` line 50**

Change:
```python
    assert perf.rolling_trades_json == "[]"
```
to:
```python
    assert perf.rolling_trades_json == []
```

- [ ] **Step 4: Fix `test_apply_bot_settings.py` line 121**

Change:
```python
    db_s = BotSettings(id=1, unbalanced_max_per_sector=5, unbalanced_sector_overrides='{"Technology": 7}')
```
to:
```python
    db_s = BotSettings(id=1, unbalanced_max_per_sector=5, unbalanced_sector_overrides={"Technology": 7})
```

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/unit/ -v 2>&1 | tail -40
```
Expected: all tests pass.

- [ ] **Step 6: Commit test fixes**

```bash
git add tests/unit/test_model_performance.py tests/unit/test_retirement_api.py \
        tests/unit/test_new_orm_models.py tests/unit/test_apply_bot_settings.py
git commit -m "test: update JSON column tests — string literals to native dict/list"
```

---

### Task 8: Close beads issue and verify Postgres

- [ ] **Step 1: Verify Postgres columns are JSONB and accept native types**

```bash
docker start alphaframe-postgres-1
python -c "
import os; os.environ['DATABASE_URL'] = 'postgresql+psycopg2://platform:changeme@localhost:5432/alphatrade'
import alphaTrade.store.db as _db; _db._engine = None
from alphaTrade.store.db import get_engine
from alphaTrade.store.repos import ModelPerformance, ModelPerformanceRepo
from sqlmodel import Session
eng = get_engine()
with Session(eng) as s:
    repo = ModelPerformanceRepo(s)
    perf = repo.get_or_create('test_jsonb')
    perf.rolling_trades_json = [1.0, 2.0, 3.0]
    repo.update(perf)
with Session(eng) as s:
    perf = ModelPerformanceRepo(s).get_or_create('test_jsonb')
    assert perf.rolling_trades_json == [1.0, 2.0, 3.0], perf.rolling_trades_json
    print('JSONB round-trip OK:', perf.rolling_trades_json)
"
```
Expected: `JSONB round-trip OK: [1.0, 2.0, 3.0]`

- [ ] **Step 2: Close beads issue**

```bash
bd close alphatrade-bhz --reason="JSONB migration complete. 5 columns converted. All json.dumps/loads removed from app layer."
```

- [ ] **Step 3: Push**

```bash
git pull --rebase
bd dolt push
git push
git status
```
Expected: `nothing to commit, working tree clean` and `up to date with origin`
