# Enhancements P1: Foundation (DB, Config, Trade Journal)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 6 new DB tables, extend config with all enhancement settings, and wire trade journal writes on position close.

**Architecture:** DB-centric, Alembic migration adds tables, SQLModel ORM + repos follow existing pattern in `store/repos.py`. Config classes follow existing `BaseSettings(extra="ignore")` pattern. Plans P2/P3/P4 all depend on this plan being complete first.

**Tech Stack:** SQLModel, Alembic, SQLite, pydantic-settings, pytest

---

## File Map

| Action | File |
|---|---|
| Create | `alphalink/store/migrations/versions/0002_enhancement_tables.py` |
| Modify | `alphalink/store/repos.py` — add 6 ORM models + 6 repo classes |
| Modify | `alphalink/config.py` — add 9 new config classes, extend RiskConfig + Settings |
| Modify | `alphalink/broker/oco_monitor.py` — accept entry/exit context, write TradeJournal on close |
| Modify | `alphalink/main.py` — pass entry context to monitor_oco, write TradeJournal on SELL |
| Modify | `tests/unit/test_logging_config.py` — no change needed |
| Create | `tests/unit/test_trade_journal.py` |
| Create | `tests/unit/test_config_extensions.py` |

---

### Task 1: Alembic migration for 6 new tables

**Files:**
- Create: `alphalink/store/migrations/versions/0002_enhancement_tables.py`

- [ ] **Step 1: Write the migration file**

```python
"""enhancement tables: trade_journal, pnl_snapshot, model_performance, sector_cache, backtest_run, backtest_trade

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-11
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tradejournal",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False, index=True),
        sa.Column("ticker", sa.String(), nullable=False, index=True),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("exit_price", sa.Float(), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("entry_time", sa.DateTime(), nullable=False),
        sa.Column("exit_time", sa.DateTime(), nullable=False),
        sa.Column("hold_bars", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("exit_reason", sa.String(), nullable=False),
        sa.Column("sl_price", sa.Float(), nullable=True),
        sa.Column("tp_price", sa.Float(), nullable=True),
        sa.Column("realized_pnl", sa.Float(), nullable=False),
        sa.Column("pnl_pct", sa.Float(), nullable=False),
    )

    op.create_table(
        "pnlsnapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("date", sa.String(), nullable=False, unique=True, index=True),
        sa.Column("total_equity", sa.Float(), nullable=False),
        sa.Column("day_pnl", sa.Float(), nullable=False),
        sa.Column("day_pnl_pct", sa.Float(), nullable=False),
        sa.Column("realized_pnl", sa.Float(), nullable=False),
        sa.Column("unrealized_pnl", sa.Float(), nullable=False),
        sa.Column("positions_json", sa.String(), nullable=False, server_default="{}"),
    )

    op.create_table(
        "modelperformance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("model_id", sa.String(), nullable=False, unique=True, index=True),
        sa.Column("trade_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("win_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rolling_pnl", sa.Float(), nullable=False, server_default="0"),
        sa.Column("rolling_trades_json", sa.String(), nullable=False, server_default="[]"),
        sa.Column("retired", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("retired_at", sa.DateTime(), nullable=True),
        sa.Column("last_updated", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "sectorcache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("yf_ticker", sa.String(), nullable=False, unique=True, index=True),
        sa.Column("sector", sa.String(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "backtestrun",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("start_date", sa.String(), nullable=False),
        sa.Column("end_date", sa.String(), nullable=False),
        sa.Column("initial_equity", sa.Float(), nullable=False),
        sa.Column("final_equity", sa.Float(), nullable=False),
        sa.Column("total_return_pct", sa.Float(), nullable=False),
        sa.Column("max_drawdown_pct", sa.Float(), nullable=False),
        sa.Column("win_rate", sa.Float(), nullable=False),
        sa.Column("sharpe", sa.Float(), nullable=False),
        sa.Column("trade_count", sa.Integer(), nullable=False),
        sa.Column("slippage_bps", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("params_json", sa.String(), nullable=False, server_default="{}"),
    )

    op.create_table(
        "backtesttrade",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), nullable=False, index=True),
        sa.Column("ticker", sa.String(), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("side", sa.String(), nullable=False),
        sa.Column("entry_time", sa.DateTime(), nullable=False),
        sa.Column("exit_time", sa.DateTime(), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("exit_price", sa.Float(), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("exit_reason", sa.String(), nullable=False),
        sa.Column("realized_pnl", sa.Float(), nullable=False),
        sa.Column("pnl_pct", sa.Float(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("backtesttrade")
    op.drop_table("backtestrun")
    op.drop_table("sectorcache")
    op.drop_table("modelperformance")
    op.drop_table("pnlsnapshot")
    op.drop_table("tradejournal")
```

- [ ] **Step 2: Run migration against a test DB to verify it applies cleanly**

```bash
cd /home/preeth/projects/alphaLink
python -c "
from alphalink.store.db import run_migrations
import tempfile, os
with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
    db = f.name
run_migrations(db)
print('Migration OK:', db)
os.unlink(db)
"
```

Expected: `Migration OK: /tmp/tmpXXXXXX.db`

- [ ] **Step 3: Commit**

```bash
git add alphalink/store/migrations/versions/0002_enhancement_tables.py
git commit -m "feat(store): add enhancement tables via alembic migration 0002"
```

---

### Task 2: ORM models in repos.py

**Files:**
- Modify: `alphalink/store/repos.py`

- [ ] **Step 1: Write failing test for new ORM models**

Create `tests/unit/test_new_orm_models.py`:

```python
"""Verify new ORM models create + round-trip via SQLite."""
import os, tempfile
from datetime import datetime

import pytest
from sqlmodel import Session

from alphalink.store.db import get_engine
from alphalink.store.repos import (
    TradeJournal, PnlSnapshot, ModelPerformance,
    SectorCache, BacktestRun, BacktestTrade,
)


@pytest.fixture
def engine(tmp_path):
    db = tmp_path / "test.db"
    from alphalink.store.db import _engine as _e
    import alphalink.store.db as _db
    _db._engine = None  # reset module-level singleton
    eng = get_engine(db)
    yield eng
    _db._engine = None


def test_trade_journal_round_trip(engine):
    now = datetime.utcnow()
    with Session(engine) as s:
        entry = TradeJournal(
            model_id="model_a",
            ticker="AAPL",
            entry_price=150.0,
            exit_price=160.0,
            quantity=10.0,
            entry_time=now,
            exit_time=now,
            exit_reason="SIGNAL_SELL",
            realized_pnl=100.0,
            pnl_pct=0.066,
        )
        s.add(entry)
        s.commit()
        s.refresh(entry)
    assert entry.id is not None
    assert entry.ticker == "AAPL"
    assert entry.exit_reason == "SIGNAL_SELL"


def test_model_performance_defaults(engine):
    with Session(engine) as s:
        perf = ModelPerformance(model_id="model_x", last_updated=datetime.utcnow())
        s.add(perf)
        s.commit()
        s.refresh(perf)
    assert perf.retired is False
    assert perf.trade_count == 0
    assert perf.rolling_trades_json == "[]"


def test_sector_cache_round_trip(engine):
    with Session(engine) as s:
        sc = SectorCache(yf_ticker="AAPL", sector="Technology")
        s.add(sc)
        s.commit()
        s.refresh(sc)
    assert sc.sector == "Technology"


def test_backtest_run_round_trip(engine):
    with Session(engine) as s:
        run = BacktestRun(
            ts=datetime.utcnow(),
            start_date="2024-01-01",
            end_date="2024-12-31",
            initial_equity=10000.0,
            final_equity=11000.0,
            total_return_pct=10.0,
            max_drawdown_pct=5.0,
            win_rate=0.6,
            sharpe=1.2,
            trade_count=50,
        )
        s.add(run)
        s.commit()
        s.refresh(run)
    assert run.id is not None
```

- [ ] **Step 2: Run test, confirm it fails**

```bash
pytest tests/unit/test_new_orm_models.py -v 2>&1 | head -20
```

Expected: `ImportError: cannot import name 'TradeJournal' from 'alphalink.store.repos'`

- [ ] **Step 3: Add ORM models to repos.py**

Append to `alphalink/store/repos.py` after the `InstrumentCache` class:

```python
class TradeJournal(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=datetime.utcnow)
    model_id: str = Field(index=True)
    ticker: str = Field(index=True)
    entry_price: float
    exit_price: float
    quantity: float
    entry_time: datetime
    exit_time: datetime
    hold_bars: int = 0
    exit_reason: str  # OCO_SL | OCO_TP | SIGNAL_SELL | HALT | MANUAL
    sl_price: Optional[float] = None
    tp_price: Optional[float] = None
    realized_pnl: float
    pnl_pct: float


class PnlSnapshot(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    date: str = Field(unique=True, index=True)  # YYYY-MM-DD
    total_equity: float
    day_pnl: float
    day_pnl_pct: float
    realized_pnl: float
    unrealized_pnl: float
    positions_json: str = "{}"


class ModelPerformance(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    model_id: str = Field(unique=True, index=True)
    trade_count: int = 0
    win_count: int = 0
    rolling_pnl: float = 0.0
    rolling_trades_json: str = "[]"  # JSON list of last N realized_pnl values
    retired: bool = False
    retired_at: Optional[datetime] = None
    last_updated: datetime = Field(default_factory=datetime.utcnow)


class SectorCache(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    yf_ticker: str = Field(unique=True, index=True)
    sector: str
    resolved_at: datetime = Field(default_factory=datetime.utcnow)


class BacktestRun(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=datetime.utcnow)
    start_date: str
    end_date: str
    initial_equity: float
    final_equity: float
    total_return_pct: float
    max_drawdown_pct: float
    win_rate: float
    sharpe: float
    trade_count: int
    slippage_bps: int = 5
    params_json: str = "{}"


class BacktestTrade(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(index=True)
    ticker: str
    model_id: str
    side: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    quantity: float
    exit_reason: str
    realized_pnl: float
    pnl_pct: float
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
pytest tests/unit/test_new_orm_models.py -v
```

Expected: all 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add alphalink/store/repos.py tests/unit/test_new_orm_models.py
git commit -m "feat(store): add 6 new ORM models for enhancement tables"
```

---

### Task 3: Repo classes in repos.py

**Files:**
- Modify: `alphalink/store/repos.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/test_new_orm_models.py`:

```python
from alphalink.store.repos import (
    TradeJournalRepo, PnlSnapshotRepo, ModelPerformanceRepo,
    SectorCacheRepo, BacktestRepo,
)


def test_trade_journal_repo_save_and_query(engine):
    now = datetime.utcnow()
    with Session(engine) as s:
        repo = TradeJournalRepo(s)
        entry = repo.save(TradeJournal(
            model_id="model_a", ticker="AAPL",
            entry_price=100.0, exit_price=110.0, quantity=5.0,
            entry_time=now, exit_time=now,
            exit_reason="SIGNAL_SELL",
            realized_pnl=50.0, pnl_pct=0.1,
        ))
        assert entry.id is not None
        rows = repo.by_model("model_a")
        assert len(rows) == 1
        since_rows = repo.since(now)
        assert len(since_rows) == 1


def test_model_performance_repo_get_or_create(engine):
    with Session(engine) as s:
        repo = ModelPerformanceRepo(s)
        perf = repo.get_or_create("model_b")
        assert perf.model_id == "model_b"
        assert perf.retired is False
        perf2 = repo.get_or_create("model_b")
        assert perf2.id == perf.id  # idempotent


def test_model_performance_repo_is_retired(engine):
    with Session(engine) as s:
        repo = ModelPerformanceRepo(s)
        assert repo.is_retired("model_c") is False  # unknown = not retired
        perf = repo.get_or_create("model_c")
        perf.retired = True
        repo.update(perf)
    with Session(engine) as s:
        repo = ModelPerformanceRepo(s)
        assert repo.is_retired("model_c") is True


def test_sector_cache_repo_put_and_get(engine):
    with Session(engine) as s:
        repo = SectorCacheRepo(s)
        repo.put("MSFT", "Technology")
        row = repo.get("MSFT")
        assert row is not None
        assert row.sector == "Technology"
        repo.put("MSFT", "Software")  # update
    with Session(engine) as s:
        repo = SectorCacheRepo(s)
        assert repo.get("MSFT").sector == "Software"


def test_pnl_snapshot_repo_upsert(engine):
    with Session(engine) as s:
        repo = PnlSnapshotRepo(s)
        repo.upsert(PnlSnapshot(
            date="2026-05-11",
            total_equity=10500.0, day_pnl=500.0, day_pnl_pct=0.05,
            realized_pnl=300.0, unrealized_pnl=200.0,
        ))
        repo.upsert(PnlSnapshot(
            date="2026-05-11",
            total_equity=10600.0, day_pnl=600.0, day_pnl_pct=0.06,
            realized_pnl=400.0, unrealized_pnl=200.0,
        ))
    with Session(engine) as s:
        from sqlmodel import select
        rows = list(s.exec(select(PnlSnapshot)).all())
    assert len(rows) == 1
    assert rows[0].total_equity == 10600.0


def test_backtest_repo_save_run_and_trade(engine):
    with Session(engine) as s:
        repo = BacktestRepo(s)
        run = repo.save_run(BacktestRun(
            ts=datetime.utcnow(),
            start_date="2024-01-01", end_date="2024-12-31",
            initial_equity=10000.0, final_equity=11000.0,
            total_return_pct=10.0, max_drawdown_pct=5.0,
            win_rate=0.6, sharpe=1.2, trade_count=50,
        ))
        assert run.id is not None
        now = datetime.utcnow()
        repo.save_trade(BacktestTrade(
            run_id=run.id, ticker="AAPL", model_id="model_a",
            side="BUY", entry_time=now, exit_time=now,
            entry_price=150.0, exit_price=160.0,
            quantity=10.0, exit_reason="OCO_TP",
            realized_pnl=100.0, pnl_pct=0.066,
        ))
```

- [ ] **Step 2: Run tests, confirm failure**

```bash
pytest tests/unit/test_new_orm_models.py -v 2>&1 | grep "FAILED\|ImportError" | head -10
```

Expected: `ImportError: cannot import name 'TradeJournalRepo'`

- [ ] **Step 3: Add repo classes to repos.py**

Append to `alphalink/store/repos.py` after the `InstrumentCacheRepo` class:

```python
class TradeJournalRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def save(self, entry: TradeJournal) -> TradeJournal:
        self._s.add(entry)
        self._s.commit()
        self._s.refresh(entry)
        return entry

    def by_model(self, model_id: str, limit: int = 100) -> list[TradeJournal]:
        return list(self._s.exec(
            select(TradeJournal)
            .where(TradeJournal.model_id == model_id)
            .order_by(TradeJournal.ts.desc())
            .limit(limit)
        ).all())

    def since(self, since: datetime) -> list[TradeJournal]:
        return list(self._s.exec(
            select(TradeJournal).where(TradeJournal.ts >= since)
        ).all())


class PnlSnapshotRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def upsert(self, snap: PnlSnapshot) -> None:
        existing = self._s.exec(
            select(PnlSnapshot).where(PnlSnapshot.date == snap.date)
        ).first()
        if existing:
            existing.total_equity = snap.total_equity
            existing.day_pnl = snap.day_pnl
            existing.day_pnl_pct = snap.day_pnl_pct
            existing.realized_pnl = snap.realized_pnl
            existing.unrealized_pnl = snap.unrealized_pnl
            existing.positions_json = snap.positions_json
        else:
            self._s.add(snap)
        self._s.commit()


class ModelPerformanceRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def get_or_create(self, model_id: str) -> ModelPerformance:
        existing = self._s.exec(
            select(ModelPerformance).where(ModelPerformance.model_id == model_id)
        ).first()
        if existing:
            return existing
        new = ModelPerformance(model_id=model_id, last_updated=datetime.utcnow())
        self._s.add(new)
        self._s.commit()
        self._s.refresh(new)
        return new

    def update(self, perf: ModelPerformance) -> None:
        perf.last_updated = datetime.utcnow()
        self._s.add(perf)
        self._s.commit()

    def is_retired(self, model_id: str) -> bool:
        row = self._s.exec(
            select(ModelPerformance).where(ModelPerformance.model_id == model_id)
        ).first()
        return row.retired if row else False


class SectorCacheRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def get(self, yf_ticker: str) -> SectorCache | None:
        return self._s.exec(
            select(SectorCache).where(SectorCache.yf_ticker == yf_ticker)
        ).first()

    def put(self, yf_ticker: str, sector: str) -> None:
        existing = self.get(yf_ticker)
        if existing:
            existing.sector = sector
            existing.resolved_at = datetime.utcnow()
        else:
            self._s.add(SectorCache(yf_ticker=yf_ticker, sector=sector))
        self._s.commit()


class BacktestRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def save_run(self, run: BacktestRun) -> BacktestRun:
        self._s.add(run)
        self._s.commit()
        self._s.refresh(run)
        return run

    def save_trade(self, trade: BacktestTrade) -> None:
        self._s.add(trade)
        self._s.commit()

    def trades_for_run(self, run_id: int) -> list[BacktestTrade]:
        return list(self._s.exec(
            select(BacktestTrade).where(BacktestTrade.run_id == run_id)
        ).all())
```

- [ ] **Step 4: Run all ORM tests**

```bash
pytest tests/unit/test_new_orm_models.py -v
```

Expected: all 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add alphalink/store/repos.py tests/unit/test_new_orm_models.py
git commit -m "feat(store): add 6 repo classes for new enhancement tables"
```

---

### Task 4: Config extensions

**Files:**
- Modify: `alphalink/config.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_config_extensions.py`:

```python
"""Verify new config classes parse from dicts (simulates overrides.yaml loading)."""
from alphalink.config import (
    AlertsConfig, AlertSlackConfig, AlertEmailConfig,
    BacktestConfig, ModelRetirementConfig,
    AtrSizingConfig, VixSizingConfig,
    BalancedPortfolioConfig, UnbalancedPortfolioConfig,
    RiskConfig,
)


def test_alerts_config_defaults():
    cfg = AlertsConfig()
    assert cfg.slack.enabled is False
    assert cfg.email.enabled is False
    assert cfg.slack.min_level == "WARNING"
    assert cfg.email.min_level == "CRITICAL"


def test_alerts_config_from_dict():
    cfg = AlertsConfig(**{
        "slack": {"enabled": True, "webhook_url": "https://hooks.slack.com/x", "min_level": "INFO"},
        "email": {"enabled": True, "smtp_host": "smtp.gmail.com", "to": ["a@b.com"]},
    })
    assert cfg.slack.enabled is True
    assert cfg.slack.webhook_url == "https://hooks.slack.com/x"
    assert cfg.email.to == ["a@b.com"]


def test_backtest_config_defaults():
    cfg = BacktestConfig()
    assert cfg.slippage_bps == 5
    assert cfg.initial_equity == 10000.0


def test_risk_config_with_retirement():
    cfg = RiskConfig(**{
        "max_positions": 3,
        "sizing_mode": "atr",
        "model_retirement": {"enabled": True, "lookback_trades": 15},
    })
    assert cfg.max_positions == 3
    assert cfg.sizing_mode == "atr"
    assert cfg.model_retirement.enabled is True
    assert cfg.model_retirement.lookback_trades == 15


def test_risk_config_balanced_mode():
    cfg = RiskConfig(**{"portfolio_mode": "balanced", "balanced": {"max_sector_pct": 0.25}})
    assert cfg.portfolio_mode == "balanced"
    assert cfg.balanced.max_sector_pct == 0.25


def test_risk_config_unbalanced_overrides():
    cfg = RiskConfig(**{
        "portfolio_mode": "unbalanced",
        "unbalanced": {"max_per_sector": 2, "sector_overrides": {"technology": 4}},
    })
    assert cfg.unbalanced.max_per_sector == 2
    assert cfg.unbalanced.sector_overrides["technology"] == 4
```

- [ ] **Step 2: Run tests, confirm failure**

```bash
pytest tests/unit/test_config_extensions.py -v 2>&1 | head -10
```

Expected: `ImportError: cannot import name 'AlertsConfig'`

- [ ] **Step 3: Add new config classes to config.py**

Add before the existing `RiskConfig` class:

```python
class ModelRetirementConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    enabled: bool = False
    lookback_trades: int = 20
    min_win_rate: float = 0.4
    min_rolling_pnl: float = -500.0
    auto_reload: bool = True


class BalancedPortfolioConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    max_sector_pct: float = 0.33


class UnbalancedPortfolioConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    max_per_sector: int = 3
    sector_overrides: dict[str, int] = {}


class AtrSizingConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    risk_pct: float = 0.01
    atr_multiplier: float = 2.0


class VixSizingConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    base_size_pct: float = 0.05
    vix_scalar: float = 20.0
    max_size_pct: float = 0.15


class AlertSlackConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    enabled: bool = False
    webhook_url: str = ""
    min_level: str = "WARNING"


class AlertEmailConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    to: list[str] = []
    min_level: str = "CRITICAL"


class AlertsConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    slack: AlertSlackConfig = AlertSlackConfig()
    email: AlertEmailConfig = AlertEmailConfig()


class BacktestConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    slippage_bps: int = 5
    commission_per_trade: float = 0.0
    initial_equity: float = 10000.0
```

- [ ] **Step 4: Replace existing RiskConfig with extended version**

Replace:
```python
class RiskConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    max_positions: int = 5
    daily_loss_halt_pct: float = 0.05
```

With:
```python
class RiskConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    max_positions: int = 5
    daily_loss_halt_pct: float = 0.05
    sizing_mode: str = "fixed"         # fixed | atr | vix
    portfolio_mode: str = "unbalanced"  # balanced | unbalanced
    model_retirement: ModelRetirementConfig = ModelRetirementConfig()
    balanced: BalancedPortfolioConfig = BalancedPortfolioConfig()
    unbalanced: UnbalancedPortfolioConfig = UnbalancedPortfolioConfig()
    atr: AtrSizingConfig = AtrSizingConfig()
    vix: VixSizingConfig = VixSizingConfig()
```

- [ ] **Step 5: Add alerts and backtest to Settings**

In `Settings` class, add after `model_overrides`:
```python
    alerts: AlertsConfig = AlertsConfig()
    backtest: BacktestConfig = BacktestConfig()
```

In `_load_overrides`, add after the `risk` block:
```python
            if "alerts" in raw:
                self.alerts = AlertsConfig(**raw["alerts"])
            if "backtest" in raw:
                self.backtest = BacktestConfig(**raw["backtest"])
```

- [ ] **Step 6: Run all config tests**

```bash
pytest tests/unit/test_config_extensions.py -v
```

Expected: all 6 tests PASS

- [ ] **Step 7: Run full test suite to check for regressions**

```bash
pytest tests/ -x -q 2>&1 | tail -10
```

Expected: no new failures

- [ ] **Step 8: Commit**

```bash
git add alphalink/config.py tests/unit/test_config_extensions.py
git commit -m "feat(config): add config classes for risk enhancements, alerts, backtest"
```

---

### Task 5: Trade journal writes in oco_monitor.py

**Files:**
- Modify: `alphalink/broker/oco_monitor.py`
- Modify: `alphalink/main.py` (caller change only — pass new params to monitor_oco)

- [ ] **Step 1: Write failing test**

Create `tests/unit/test_trade_journal.py`:

```python
"""Verify trade journal entries are written on position close."""
import asyncio
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session, select

from alphalink.broker.oco_monitor import monitor_oco
from alphalink.store.db import get_engine
from alphalink.store.repos import TradeJournal


@pytest.fixture
def engine(tmp_path):
    import alphalink.store.db as _db
    _db._engine = None
    eng = get_engine(tmp_path / "test.db")
    yield eng
    _db._engine = None


def make_t212(stop_status: str, limit_status: str, fill_price: float = 155.0):
    t212 = MagicMock()
    t212.get_order.side_effect = lambda order_id: {
        "status": stop_status if order_id == "stop-1" else limit_status,
        "fillPrice": fill_price,
    }
    t212.cancel_order.return_value = {}
    return t212


@pytest.mark.asyncio
async def test_oco_sl_writes_trade_journal(engine):
    t212 = make_t212(stop_status="FILLED", limit_status="WORKING")
    entry_time = datetime.utcnow() - timedelta(hours=2)

    await monitor_oco(
        t212=t212,
        t212_ticker="AAPL_US_EQ",
        stop_order_id="stop-1",
        limit_order_id="limit-1",
        engine=engine,
        cooldown_td=timedelta(hours=1),
        entry_price=150.0,
        sl_price=145.0,
        tp_price=160.0,
        quantity=10.0,
        model_id="model_a",
        entry_time=entry_time,
        poll_interval_s=0.0,
    )

    with Session(engine) as s:
        entries = list(s.exec(select(TradeJournal)).all())

    assert len(entries) == 1
    assert entries[0].exit_reason == "OCO_SL"
    assert entries[0].ticker == "AAPL_US_EQ"
    assert entries[0].model_id == "model_a"
    assert entries[0].entry_price == 150.0
    assert entries[0].realized_pnl < 0  # stop loss = loss


@pytest.mark.asyncio
async def test_oco_tp_writes_trade_journal(engine):
    t212 = make_t212(stop_status="WORKING", limit_status="FILLED", fill_price=160.0)
    entry_time = datetime.utcnow() - timedelta(hours=1)

    await monitor_oco(
        t212=t212,
        t212_ticker="AAPL_US_EQ",
        stop_order_id="stop-1",
        limit_order_id="limit-1",
        engine=engine,
        cooldown_td=timedelta(hours=1),
        entry_price=150.0,
        sl_price=145.0,
        tp_price=160.0,
        quantity=10.0,
        model_id="model_a",
        entry_time=entry_time,
        poll_interval_s=0.0,
    )

    with Session(engine) as s:
        entries = list(s.exec(select(TradeJournal)).all())

    assert entries[0].exit_reason == "OCO_TP"
    assert entries[0].realized_pnl > 0  # take profit = gain
```

- [ ] **Step 2: Run tests, confirm failure**

```bash
pytest tests/unit/test_trade_journal.py -v 2>&1 | head -15
```

Expected: `TypeError: monitor_oco() got an unexpected keyword argument 'entry_price'`

- [ ] **Step 3: Rewrite oco_monitor.py**

```python
"""OCO monitor: polls stop/limit legs after BUY fill, cancels surviving leg on exit."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.engine import Engine
from sqlmodel import Session

from alphalink.broker.t212_client import T212Client
from alphalink.notify import webhook as wh
from alphalink.store.repos import Position, PositionRepo, TradeJournal, TradeJournalRepo

log = logging.getLogger(__name__)

_TERMINAL = frozenset({"FILLED", "CANCELLED", "REJECTED"})


async def monitor_oco(
    t212: T212Client,
    t212_ticker: str,
    stop_order_id: str,
    limit_order_id: str,
    engine: Engine,
    cooldown_td: timedelta,
    entry_price: float = 0.0,
    sl_price: float = 0.0,
    tp_price: float = 0.0,
    quantity: float = 0.0,
    model_id: str = "",
    entry_time: Optional[datetime] = None,
    poll_interval_s: float = 10.0,
) -> None:
    """Poll stop and limit orders until one fills. Cancel the other leg and close position."""
    while True:
        await asyncio.sleep(poll_interval_s)
        try:
            stop_order = t212.get_order(stop_order_id)
            limit_order = t212.get_order(limit_order_id)
            stop_status = stop_order.get("status", "")
            limit_status = limit_order.get("status", "")
        except Exception as exc:
            log.error("OCO poll error for %s: %s", t212_ticker, exc)
            continue

        if stop_status == "FILLED":
            fill_price = float(stop_order.get("fillPrice") or sl_price)
            _close_position(engine, t212_ticker, cooldown_td, "OCO_SL",
                            exit_price=fill_price, entry_price=entry_price,
                            quantity=quantity, model_id=model_id,
                            sl_price=sl_price, tp_price=tp_price,
                            entry_time=entry_time)
            _cancel_leg(t212, limit_order_id, t212_ticker)
            break

        if limit_status == "FILLED":
            fill_price = float(limit_order.get("fillPrice") or tp_price)
            _close_position(engine, t212_ticker, cooldown_td, "OCO_TP",
                            exit_price=fill_price, entry_price=entry_price,
                            quantity=quantity, model_id=model_id,
                            sl_price=sl_price, tp_price=tp_price,
                            entry_time=entry_time)
            _cancel_leg(t212, stop_order_id, t212_ticker)
            break

        if stop_status in _TERMINAL and stop_status != "FILLED":
            log.warning("Stop leg %s for %s reached %s without fill — cancelling limit leg",
                        stop_order_id, t212_ticker, stop_status)
            _cancel_leg(t212, limit_order_id, t212_ticker)
            wh.notify("WARNING", f"OCO stop leg cancelled/rejected for {t212_ticker} — limit leg cancelled",
                      category="oco")
            break

        if limit_status in _TERMINAL and limit_status != "FILLED":
            log.warning("Limit leg %s for %s reached %s without fill — cancelling stop leg",
                        limit_order_id, t212_ticker, limit_status)
            _cancel_leg(t212, stop_order_id, t212_ticker)
            wh.notify("WARNING", f"OCO limit leg cancelled/rejected for {t212_ticker} — stop leg cancelled",
                      category="oco")
            break


def _cancel_leg(t212: T212Client, order_id: str, t212_ticker: str) -> None:
    try:
        t212.cancel_order(order_id)
    except Exception as exc:
        log.warning("Failed to cancel OCO leg %s for %s: %s", order_id, t212_ticker, exc)


def _close_position(
    engine: Engine,
    t212_ticker: str,
    cooldown_td: timedelta,
    exit_reason: str,
    exit_price: float = 0.0,
    entry_price: float = 0.0,
    quantity: float = 0.0,
    model_id: str = "",
    sl_price: float = 0.0,
    tp_price: float = 0.0,
    entry_time: Optional[datetime] = None,
) -> None:
    now = datetime.utcnow()
    realized_pnl = (exit_price - entry_price) * quantity
    pnl_pct = (exit_price - entry_price) / entry_price if entry_price else 0.0

    with Session(engine) as session:
        repo = PositionRepo(session)
        repo.remove(t212_ticker)
        repo.upsert(Position(
            t212_ticker=t212_ticker,
            quantity=0,
            avg_entry=0,
            cooldown_until_ts=now + cooldown_td,
        ))
        if model_id:
            journal_repo = TradeJournalRepo(session)
            journal_repo.save(TradeJournal(
                model_id=model_id,
                ticker=t212_ticker,
                entry_price=entry_price,
                exit_price=exit_price,
                quantity=quantity,
                entry_time=entry_time or now,
                exit_time=now,
                exit_reason=exit_reason,
                sl_price=sl_price,
                tp_price=tp_price,
                realized_pnl=realized_pnl,
                pnl_pct=pnl_pct,
            ))

    log.info("OCO %s hit for %s — position closed pnl=%.2f", exit_reason, t212_ticker, realized_pnl)
    wh.notify("INFO", f"OCO {exit_reason} hit for {t212_ticker} pnl={realized_pnl:.2f}", category="oco")
```

- [ ] **Step 4: Update monitor_oco call site in main.py**

In `alphalink/main.py`, find the `asyncio.create_task(monitor_oco(...))` call (around line 356) and add the new params:

```python
                            _task = asyncio.create_task(monitor_oco(
                                t212=t212,
                                t212_ticker=t212_ticker,
                                stop_order_id=str(stop_resp["id"]),
                                limit_order_id=str(limit_resp["id"]),
                                engine=engine,
                                cooldown_td=cooldown_td,
                                entry_price=entry_price,
                                sl_price=sl_price,
                                tp_price=tp_price,
                                quantity=qty,
                                model_id=manifest.run_name,
                                entry_time=datetime.utcnow(),
                            ))
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/unit/test_trade_journal.py -v
```

Expected: both tests PASS

- [ ] **Step 6: Run full suite for regressions**

```bash
pytest tests/ -x -q 2>&1 | tail -5
```

Expected: no new failures

- [ ] **Step 7: Commit**

```bash
git add alphalink/broker/oco_monitor.py alphalink/main.py tests/unit/test_trade_journal.py
git commit -m "feat(broker): write trade journal on OCO position close"
```

---

### Task 6: Trade journal write on SELL in main.py

**Files:**
- Modify: `alphalink/main.py`

- [ ] **Step 1: Add failing test for SELL journal write**

Add to `tests/unit/test_trade_journal.py`:

```python
from alphalink.store.repos import Position


def test_sell_writes_trade_journal(engine):
    """Journal entry created when build_sell_journal_entry is called with SELL params."""
    from alphalink.main import build_sell_journal_entry

    now = datetime.utcnow()
    pos = Position(
        t212_ticker="AAPL_US_EQ",
        quantity=10.0,
        avg_entry=150.0,
        opened_at=now - timedelta(hours=3),
    )
    entry = build_sell_journal_entry(
        model_id="model_a",
        t212_ticker="AAPL_US_EQ",
        exit_price=160.0,
        quantity=10.0,
        position=pos,
    )
    assert entry.exit_reason == "SIGNAL_SELL"
    assert entry.entry_price == 150.0
    assert entry.exit_price == 160.0
    assert entry.realized_pnl == 100.0
    assert entry.pnl_pct == pytest.approx(0.0667, rel=1e-2)
```

- [ ] **Step 2: Run test, confirm failure**

```bash
pytest tests/unit/test_trade_journal.py::test_sell_writes_trade_journal -v 2>&1 | head -10
```

Expected: `ImportError: cannot import name 'build_sell_journal_entry' from 'alphalink.main'`

- [ ] **Step 3: Add build_sell_journal_entry helper to main.py**

Add after the `scan_models` function in `alphalink/main.py`:

```python
def build_sell_journal_entry(
    model_id: str,
    t212_ticker: str,
    exit_price: float,
    quantity: float,
    position: "Position",
) -> "TradeJournal":
    from alphalink.store.repos import TradeJournal
    entry_price = position.avg_entry
    realized_pnl = (exit_price - entry_price) * quantity
    pnl_pct = (exit_price - entry_price) / entry_price if entry_price else 0.0
    return TradeJournal(
        model_id=model_id,
        ticker=t212_ticker,
        entry_price=entry_price,
        exit_price=exit_price,
        quantity=quantity,
        entry_time=position.opened_at,
        exit_time=datetime.utcnow(),
        exit_reason="SIGNAL_SELL",
        realized_pnl=realized_pnl,
        pnl_pct=pnl_pct,
    )
```

- [ ] **Step 4: Add journal write in make_tick SELL branch**

In the `elif signal == "SELL":` block in `make_tick`, after `pos_repo.remove(t212_ticker)` and the `pos_repo.upsert(...)` call, add:

```python
                        # Write trade journal for SELL
                        if pos:
                            from alphalink.store.repos import TradeJournalRepo
                            fill_price_raw = resp.get("fillPrice")
                            exit_p = float(fill_price_raw) if fill_price_raw else current_price
                            journal_repo = TradeJournalRepo(session)
                            journal_repo.save(build_sell_journal_entry(
                                model_id=manifest.run_name,
                                t212_ticker=t212_ticker,
                                exit_price=exit_p,
                                quantity=qty,
                                position=pos,
                            ))
```

Note: `pos` is already fetched in gates.py logic. In the tick loop, `pos = pos_repo.get(t212_ticker)` needs to be available in the SELL branch. Check that `pos_repo.get(t212_ticker)` is accessible — the existing tick loop calls `run_gates` which internally fetches position but does not return it. Add `pos = pos_repo.get(t212_ticker)` before the gate call in the tick loop:

```python
                pos = pos_repo.get(t212_ticker)  # used for SELL journal write

                gate: GateResult = run_gates(
                    signal=signal,
                    t212_ticker=t212_ticker,
                    position_repo=pos_repo,
                    max_positions=settings.risk.max_positions,
                    daily_loss_halted=daily_loss_halted,
                )
```

- [ ] **Step 5: Run all trade journal tests**

```bash
pytest tests/unit/test_trade_journal.py -v
```

Expected: all 3 tests PASS

- [ ] **Step 6: Run full test suite**

```bash
pytest tests/ -x -q 2>&1 | tail -5
```

Expected: no new failures

- [ ] **Step 7: Commit**

```bash
git add alphalink/main.py tests/unit/test_trade_journal.py
git commit -m "feat(main): write trade journal on SELL fill"
```
