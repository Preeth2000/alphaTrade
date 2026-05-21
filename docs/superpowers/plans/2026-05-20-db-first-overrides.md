# DB-First Overrides Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make SQLite the authoritative store for all runtime config changes so changes survive restarts and YAML is never written to at runtime.

**Architecture:** YAML seeds Settings at startup; `apply_bot_settings` and `_merge_overrides` apply DB values on top each tick. Two new migrations add the missing DB columns. The retirement router stops writing YAML and writes to `ModelOverrideRecord` instead. `_merge_overrides` is fixed to use all DB fields, not just `enabled`.

**Tech Stack:** SQLite via SQLAlchemy/SQLModel, Alembic migrations, FastAPI, Pydantic

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `alphaTrade/store/migrations/versions/0013_model_override_retirement_backtest.py` | CREATE | Add retirement + backtest schedule cols to `model_override` |
| `alphaTrade/store/migrations/versions/0014_botsettings_risk_backtest.py` | CREATE | Add sizing/portfolio/atr/vix/backtest cols to `botsettings` |
| `alphaTrade/store/repos.py` | MODIFY | Add new fields to `BotSettings` and `ModelOverrideRecord` SQLModels |
| `alphaTrade/main.py` | MODIFY | Extend `apply_bot_settings`; fix `_merge_overrides` |
| `alphaTrade/api/routers/retirement.py` | MODIFY | Per-model retirement writes to DB; remove `_persist_retirement_overrides` |
| `tests/unit/test_migrations.py` | MODIFY | Assert new columns exist after migration |
| `tests/unit/test_apply_bot_settings.py` | MODIFY | Test new fields applied from BotSettings |
| `tests/unit/test_retirement_api.py` | MODIFY | Replace YAML assertions with DB assertions |

---

### Task 1: Migration 0013 — retirement + backtest cols on `model_override`

**Files:**
- Create: `alphaTrade/store/migrations/versions/0013_model_override_retirement_backtest.py`
- Test: `tests/unit/test_migrations.py`

- [ ] **Step 1: Write failing test**

Add to `tests/unit/test_migrations.py`:

```python
def test_model_override_has_retirement_and_backtest_cols(self, tmp_path):
    from alphaTrade.store.db import run_migrations
    db_path = tmp_path / "test.db"
    run_migrations(db_path)
    conn = sqlite3.connect(db_path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(model_override)").fetchall()}
    conn.close()
    expected = {
        "retirement_enabled", "retirement_lookback_trades", "retirement_min_win_rate",
        "retirement_min_rolling_pnl", "retirement_min_trades_before_evaluation",
        "retirement_min_evaluation_period", "backtest_disabled", "backtest_cron",
        "backtest_lookback_days",
    }
    assert expected <= cols
```

- [ ] **Step 2: Run test, verify it fails**

```bash
cd /home/preeth/projects/alphaTrade && python -m pytest tests/unit/test_migrations.py::TestAlembicMigrations::test_model_override_has_retirement_and_backtest_cols -v
```

Expected: FAIL — columns not found.

- [ ] **Step 3: Create migration file**

Create `alphaTrade/store/migrations/versions/0013_model_override_retirement_backtest.py`:

```python
"""Add retirement and backtest schedule fields to model_override.

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-20
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    cols = [row[1] for row in conn.execute(sa.text("PRAGMA table_info(model_override)"))]
    new_cols = [
        ("retirement_enabled", sa.Boolean()),
        ("retirement_lookback_trades", sa.Integer()),
        ("retirement_min_win_rate", sa.Float()),
        ("retirement_min_rolling_pnl", sa.Float()),
        ("retirement_min_trades_before_evaluation", sa.Integer()),
        ("retirement_min_evaluation_period", sa.String()),
        ("backtest_disabled", sa.Boolean()),
        ("backtest_cron", sa.String()),
        ("backtest_lookback_days", sa.Integer()),
    ]
    for col_name, col_type in new_cols:
        if col_name not in cols:
            op.add_column("model_override", sa.Column(col_name, col_type, nullable=True))


def downgrade() -> None:
    raise NotImplementedError("Downgrade not supported for 0013.")
```

- [ ] **Step 4: Run test, verify it passes**

```bash
cd /home/preeth/projects/alphaTrade && python -m pytest tests/unit/test_migrations.py::TestAlembicMigrations::test_model_override_has_retirement_and_backtest_cols -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add alphaTrade/store/migrations/versions/0013_model_override_retirement_backtest.py tests/unit/test_migrations.py
git commit -m "feat(db): migration 0013 — retirement+backtest cols on model_override"
```

---

### Task 2: Migration 0014 — risk/backtest cols on `botsettings`

**Files:**
- Create: `alphaTrade/store/migrations/versions/0014_botsettings_risk_backtest.py`
- Test: `tests/unit/test_migrations.py`

- [ ] **Step 1: Write failing test**

Add to the `TestAlembicMigrations` class in `tests/unit/test_migrations.py`:

```python
def test_botsettings_has_risk_and_backtest_cols(self, tmp_path):
    from alphaTrade.store.db import run_migrations
    db_path = tmp_path / "test.db"
    run_migrations(db_path)
    conn = sqlite3.connect(db_path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(botsettings)").fetchall()}
    conn.close()
    expected = {
        "sizing_mode", "portfolio_mode", "order_stale_window_multiplier",
        "order_queue_max_depth", "balanced_max_sector_pct",
        "unbalanced_max_per_sector", "unbalanced_sector_overrides",
        "atr_risk_pct", "atr_multiplier",
        "vix_base_size_pct", "vix_scalar", "vix_max_size_pct",
        "backtest_slippage_bps", "backtest_commission_per_trade",
        "backtest_initial_equity", "backtest_default_size_pct",
        "backtest_sl_pct", "backtest_tp_pct", "backtest_schedule_enabled",
        "backtest_cron", "backtest_lookback_days", "backtest_simulate_oco_lag",
        "backtest_oco_stop_gap_secs", "backtest_oco_limit_gap_secs",
    }
    assert expected <= cols
```

- [ ] **Step 2: Run test, verify it fails**

```bash
cd /home/preeth/projects/alphaTrade && python -m pytest tests/unit/test_migrations.py::TestAlembicMigrations::test_botsettings_has_risk_and_backtest_cols -v
```

Expected: FAIL — columns not found.

- [ ] **Step 3: Create migration file**

Create `alphaTrade/store/migrations/versions/0014_botsettings_risk_backtest.py`:

```python
"""Add risk sizing, portfolio, ATR, VIX, and backtest fields to botsettings.

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-20
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    cols = [row[1] for row in conn.execute(sa.text("PRAGMA table_info(botsettings)"))]
    new_cols = [
        ("sizing_mode", sa.String()),
        ("portfolio_mode", sa.String()),
        ("order_stale_window_multiplier", sa.Float()),
        ("order_queue_max_depth", sa.Integer()),
        ("balanced_max_sector_pct", sa.Float()),
        ("unbalanced_max_per_sector", sa.Integer()),
        ("unbalanced_sector_overrides", sa.String()),   # JSON string
        ("atr_risk_pct", sa.Float()),
        ("atr_multiplier", sa.Float()),
        ("vix_base_size_pct", sa.Float()),
        ("vix_scalar", sa.Float()),
        ("vix_max_size_pct", sa.Float()),
        ("backtest_slippage_bps", sa.Integer()),
        ("backtest_commission_per_trade", sa.Float()),
        ("backtest_initial_equity", sa.Float()),
        ("backtest_default_size_pct", sa.Float()),
        ("backtest_sl_pct", sa.Float()),
        ("backtest_tp_pct", sa.Float()),
        ("backtest_schedule_enabled", sa.Boolean()),
        ("backtest_cron", sa.String()),
        ("backtest_lookback_days", sa.Integer()),
        ("backtest_simulate_oco_lag", sa.Boolean()),
        ("backtest_oco_stop_gap_secs", sa.Float()),
        ("backtest_oco_limit_gap_secs", sa.Float()),
    ]
    for col_name, col_type in new_cols:
        if col_name not in cols:
            op.add_column("botsettings", sa.Column(col_name, col_type, nullable=True))


def downgrade() -> None:
    raise NotImplementedError("Downgrade not supported for 0014.")
```

- [ ] **Step 4: Run test, verify it passes**

```bash
cd /home/preeth/projects/alphaTrade && python -m pytest tests/unit/test_migrations.py::TestAlembicMigrations::test_botsettings_has_risk_and_backtest_cols -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add alphaTrade/store/migrations/versions/0014_botsettings_risk_backtest.py tests/unit/test_migrations.py
git commit -m "feat(db): migration 0014 — risk/backtest cols on botsettings"
```

---

### Task 3: Update SQLModel classes in `repos.py`

**Files:**
- Modify: `alphaTrade/store/repos.py`

- [ ] **Step 1: Add retirement + backtest fields to `ModelOverrideRecord`**

In `alphaTrade/store/repos.py`, find `ModelOverrideRecord` (line ~549). Add after `dangerously_allow_pyramid`:

```python
class ModelOverrideRecord(SQLModel, table=True):
    __tablename__ = "model_override"

    run_name: str = Field(primary_key=True)
    enabled: Optional[bool] = None
    broker_ticker: Optional[str] = None
    size_pct: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    cooldown_bars: Optional[int] = None
    safe_mode: Optional[bool] = None
    dangerously_allow_pyramid: Optional[bool] = None
    # retirement sub-override
    retirement_enabled: Optional[bool] = None
    retirement_lookback_trades: Optional[int] = None
    retirement_min_win_rate: Optional[float] = None
    retirement_min_rolling_pnl: Optional[float] = None
    retirement_min_trades_before_evaluation: Optional[int] = None
    retirement_min_evaluation_period: Optional[str] = None
    # backtest schedule sub-override
    backtest_disabled: Optional[bool] = None
    backtest_cron: Optional[str] = None
    backtest_lookback_days: Optional[int] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)
```

- [ ] **Step 2: Add risk/backtest fields to `BotSettings`**

In `alphaTrade/store/repos.py`, find `BotSettings` (line ~487). Add after `dangerously_allow_pyramid`:

```python
    # risk sizing / portfolio
    sizing_mode: Optional[str] = Field(default=None, sa_column=Column(String, nullable=True))
    portfolio_mode: Optional[str] = Field(default=None, sa_column=Column(String, nullable=True))
    order_stale_window_multiplier: Optional[float] = Field(default=None, sa_column=Column(Float, nullable=True))
    order_queue_max_depth: Optional[int] = Field(default=None, sa_column=Column(Integer, nullable=True))
    # balanced portfolio
    balanced_max_sector_pct: Optional[float] = Field(default=None, sa_column=Column(Float, nullable=True))
    # unbalanced portfolio
    unbalanced_max_per_sector: Optional[int] = Field(default=None, sa_column=Column(Integer, nullable=True))
    unbalanced_sector_overrides: Optional[str] = Field(default=None, sa_column=Column(String, nullable=True))  # JSON
    # ATR sizing
    atr_risk_pct: Optional[float] = Field(default=None, sa_column=Column(Float, nullable=True))
    atr_multiplier: Optional[float] = Field(default=None, sa_column=Column(Float, nullable=True))
    # VIX sizing
    vix_base_size_pct: Optional[float] = Field(default=None, sa_column=Column(Float, nullable=True))
    vix_scalar: Optional[float] = Field(default=None, sa_column=Column(Float, nullable=True))
    vix_max_size_pct: Optional[float] = Field(default=None, sa_column=Column(Float, nullable=True))
    # backtest
    backtest_slippage_bps: Optional[int] = Field(default=None, sa_column=Column(Integer, nullable=True))
    backtest_commission_per_trade: Optional[float] = Field(default=None, sa_column=Column(Float, nullable=True))
    backtest_initial_equity: Optional[float] = Field(default=None, sa_column=Column(Float, nullable=True))
    backtest_default_size_pct: Optional[float] = Field(default=None, sa_column=Column(Float, nullable=True))
    backtest_sl_pct: Optional[float] = Field(default=None, sa_column=Column(Float, nullable=True))
    backtest_tp_pct: Optional[float] = Field(default=None, sa_column=Column(Float, nullable=True))
    backtest_schedule_enabled: Optional[bool] = Field(default=None, sa_column=Column(Boolean, nullable=True))
    backtest_cron: Optional[str] = Field(default=None, sa_column=Column(String, nullable=True))
    backtest_lookback_days: Optional[int] = Field(default=None, sa_column=Column(Integer, nullable=True))
    backtest_simulate_oco_lag: Optional[bool] = Field(default=None, sa_column=Column(Boolean, nullable=True))
    backtest_oco_stop_gap_secs: Optional[float] = Field(default=None, sa_column=Column(Float, nullable=True))
    backtest_oco_limit_gap_secs: Optional[float] = Field(default=None, sa_column=Column(Float, nullable=True))
```

- [ ] **Step 3: Run existing repo tests to verify no regressions**

```bash
cd /home/preeth/projects/alphaTrade && python -m pytest tests/unit/test_migrations.py tests/unit/test_new_orm_models.py -v
```

Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add alphaTrade/store/repos.py
git commit -m "feat(db): add retirement/backtest fields to ModelOverrideRecord and BotSettings"
```

---

### Task 4: Fix `_merge_overrides` in `main.py`

**Files:**
- Modify: `alphaTrade/main.py:275-290`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_merge_overrides.py`:

```python
"""Tests for _merge_overrides — DB fields win over YAML for all fields."""
from __future__ import annotations
from datetime import datetime
from alphaTrade.config import ModelOverride, ModelRetirementOverride, BacktestScheduleOverride
from alphaTrade.store.repos import ModelOverrideRecord
from alphaTrade.main import _merge_overrides


def _db(run_name, **kwargs) -> ModelOverrideRecord:
    return ModelOverrideRecord(run_name=run_name, updated_at=datetime.utcnow(), **kwargs)


def test_db_enabled_wins_over_yaml():
    yaml = {"m1": ModelOverride(enabled=True)}
    db = {"m1": _db("m1", enabled=False)}
    result = _merge_overrides(yaml, db)
    assert result["m1"].enabled is False


def test_db_size_pct_wins_over_yaml():
    yaml = {"m1": ModelOverride(size_pct=0.10)}
    db = {"m1": _db("m1", size_pct=0.05)}
    result = _merge_overrides(yaml, db)
    assert result["m1"].size_pct == 0.05


def test_db_broker_ticker_wins_over_yaml():
    yaml = {"m1": ModelOverride(t212_ticker="AAPL_US_EQ")}
    db = {"m1": _db("m1", broker_ticker="MSFT_US_EQ")}
    result = _merge_overrides(yaml, db)
    assert result["m1"].t212_ticker == "MSFT_US_EQ"


def test_db_safe_mode_wins_over_yaml():
    yaml = {"m1": ModelOverride(safe_mode=True)}
    db = {"m1": _db("m1", safe_mode=False)}
    result = _merge_overrides(yaml, db)
    assert result["m1"].safe_mode is False


def test_db_retirement_fields_win_over_yaml():
    yaml_ret = ModelRetirementOverride(enabled=False, min_win_rate=0.4)
    yaml = {"m1": ModelOverride(retirement=yaml_ret)}
    db = {"m1": _db("m1", retirement_enabled=True, retirement_min_win_rate=0.6)}
    result = _merge_overrides(yaml, db)
    assert result["m1"].retirement.enabled is True
    assert result["m1"].retirement.min_win_rate == 0.6


def test_db_backtest_fields_win_over_yaml():
    yaml_bt = BacktestScheduleOverride(disabled=False, lookback_days=30)
    yaml = {"m1": ModelOverride(backtest=yaml_bt)}
    db = {"m1": _db("m1", backtest_disabled=True, backtest_lookback_days=60)}
    result = _merge_overrides(yaml, db)
    assert result["m1"].backtest.disabled is True
    assert result["m1"].backtest.lookback_days == 60


def test_yaml_fields_preserved_when_db_null():
    yaml = {"m1": ModelOverride(size_pct=0.10, t212_ticker="AAPL_US_EQ")}
    db = {"m1": _db("m1", enabled=False)}  # only enabled set
    result = _merge_overrides(yaml, db)
    assert result["m1"].size_pct == 0.10
    assert result["m1"].t212_ticker == "AAPL_US_EQ"
    assert result["m1"].enabled is False


def test_db_only_model_added_to_result():
    yaml = {}
    db = {"m1": _db("m1", enabled=True, size_pct=0.05)}
    result = _merge_overrides(yaml, db)
    assert "m1" in result
    assert result["m1"].enabled is True
    assert result["m1"].size_pct == 0.05
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/preeth/projects/alphaTrade && python -m pytest tests/unit/test_merge_overrides.py -v
```

Expected: most FAIL (current `_merge_overrides` only handles `enabled`).

- [ ] **Step 3: Replace `_merge_overrides` in `main.py`**

Replace lines 275-290 in `alphaTrade/main.py` with:

```python
def _merge_overrides(
    yaml_overrides: dict,
    db_overrides: dict[str, ModelOverrideRecord],
) -> dict:
    """Merge DB fields on top of YAML overrides. DB wins for any non-None field."""
    from alphaTrade.config import ModelOverride, ModelRetirementOverride, BacktestScheduleOverride
    merged = dict(yaml_overrides)
    for run_name, db_ov in db_overrides.items():
        existing = merged.get(run_name, ModelOverride())

        ret_base = existing.retirement.model_dump()
        if db_ov.retirement_enabled is not None:
            ret_base["enabled"] = db_ov.retirement_enabled
        if db_ov.retirement_lookback_trades is not None:
            ret_base["lookback_trades"] = db_ov.retirement_lookback_trades
        if db_ov.retirement_min_win_rate is not None:
            ret_base["min_win_rate"] = db_ov.retirement_min_win_rate
        if db_ov.retirement_min_rolling_pnl is not None:
            ret_base["min_rolling_pnl"] = db_ov.retirement_min_rolling_pnl
        if db_ov.retirement_min_trades_before_evaluation is not None:
            ret_base["min_trades_before_evaluation"] = db_ov.retirement_min_trades_before_evaluation
        if db_ov.retirement_min_evaluation_period is not None:
            ret_base["min_evaluation_period"] = db_ov.retirement_min_evaluation_period

        bt_base = existing.backtest.model_dump()
        if db_ov.backtest_disabled is not None:
            bt_base["disabled"] = db_ov.backtest_disabled
        if db_ov.backtest_cron is not None:
            bt_base["cron"] = db_ov.backtest_cron
        if db_ov.backtest_lookback_days is not None:
            bt_base["lookback_days"] = db_ov.backtest_lookback_days

        merged[run_name] = ModelOverride(
            enabled=db_ov.enabled if db_ov.enabled is not None else existing.enabled,
            t212_ticker=db_ov.broker_ticker if db_ov.broker_ticker is not None else existing.t212_ticker,
            size_pct=db_ov.size_pct if db_ov.size_pct is not None else existing.size_pct,
            safe_mode=db_ov.safe_mode if db_ov.safe_mode is not None else existing.safe_mode,
            dangerously_allow_pyramid=db_ov.dangerously_allow_pyramid if db_ov.dangerously_allow_pyramid is not None else existing.dangerously_allow_pyramid,
            retirement=ModelRetirementOverride(**ret_base),
            backtest=BacktestScheduleOverride(**bt_base),
        )
    return merged
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
cd /home/preeth/projects/alphaTrade && python -m pytest tests/unit/test_merge_overrides.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add alphaTrade/main.py tests/unit/test_merge_overrides.py
git commit -m "fix(main): _merge_overrides uses all DB fields, not just enabled"
```

---

### Task 5: Extend `apply_bot_settings` in `main.py`

**Files:**
- Modify: `alphaTrade/main.py:88-150`
- Test: `tests/unit/test_apply_bot_settings.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/test_apply_bot_settings.py`:

```python
from unittest.mock import MagicMock
from alphaTrade.config import Settings
from alphaTrade.store.repos import BotSettings
from alphaTrade.main import apply_bot_settings
import os


def _make_settings(tmp_path) -> Settings:
    overrides = tmp_path / "overrides.yaml"
    overrides.write_text("")
    os.environ.setdefault("ALPHATRADE_API_KEY", "test-key")
    return Settings(overrides_path=overrides, state_db_path=tmp_path / "state.db")


def test_apply_bot_settings_sizing_mode(tmp_path):
    settings = _make_settings(tmp_path)
    db_s = BotSettings(id=1, sizing_mode="atr")
    apply_bot_settings(db_s, settings, [MagicMock()], [MagicMock()])
    assert settings.risk.sizing_mode == "atr"


def test_apply_bot_settings_portfolio_mode(tmp_path):
    settings = _make_settings(tmp_path)
    db_s = BotSettings(id=1, portfolio_mode="balanced")
    apply_bot_settings(db_s, settings, [MagicMock()], [MagicMock()])
    assert settings.risk.portfolio_mode == "balanced"


def test_apply_bot_settings_order_stale_window(tmp_path):
    settings = _make_settings(tmp_path)
    db_s = BotSettings(id=1, order_stale_window_multiplier=0.75)
    apply_bot_settings(db_s, settings, [MagicMock()], [MagicMock()])
    assert settings.risk.order_stale_window_multiplier == 0.75


def test_apply_bot_settings_atr(tmp_path):
    settings = _make_settings(tmp_path)
    db_s = BotSettings(id=1, atr_risk_pct=0.02, atr_multiplier=3.0)
    apply_bot_settings(db_s, settings, [MagicMock()], [MagicMock()])
    assert settings.risk.atr.risk_pct == 0.02
    assert settings.risk.atr.atr_multiplier == 3.0


def test_apply_bot_settings_vix(tmp_path):
    settings = _make_settings(tmp_path)
    db_s = BotSettings(id=1, vix_base_size_pct=0.03, vix_scalar=25.0, vix_max_size_pct=0.20)
    apply_bot_settings(db_s, settings, [MagicMock()], [MagicMock()])
    assert settings.risk.vix.base_size_pct == 0.03
    assert settings.risk.vix.vix_scalar == 25.0
    assert settings.risk.vix.max_size_pct == 0.20


def test_apply_bot_settings_balanced(tmp_path):
    settings = _make_settings(tmp_path)
    db_s = BotSettings(id=1, balanced_max_sector_pct=0.50)
    apply_bot_settings(db_s, settings, [MagicMock()], [MagicMock()])
    assert settings.risk.balanced.max_sector_pct == 0.50


def test_apply_bot_settings_unbalanced(tmp_path):
    settings = _make_settings(tmp_path)
    db_s = BotSettings(id=1, unbalanced_max_per_sector=5, unbalanced_sector_overrides='{"Technology": 7}')
    apply_bot_settings(db_s, settings, [MagicMock()], [MagicMock()])
    assert settings.risk.unbalanced.max_per_sector == 5
    assert settings.risk.unbalanced.sector_overrides == {"Technology": 7}


def test_apply_bot_settings_backtest(tmp_path):
    settings = _make_settings(tmp_path)
    db_s = BotSettings(
        id=1,
        backtest_slippage_bps=10,
        backtest_initial_equity=50000.0,
        backtest_cron="0 3 * * *",
        backtest_lookback_days=60,
        backtest_simulate_oco_lag=True,
        backtest_oco_stop_gap_secs=3.0,
    )
    apply_bot_settings(db_s, settings, [MagicMock()], [MagicMock()])
    assert settings.backtest.slippage_bps == 10
    assert settings.backtest.initial_equity == 50000.0
    assert settings.backtest.cron == "0 3 * * *"
    assert settings.backtest.lookback_days == 60
    assert settings.backtest.simulate_oco_lag is True
    assert settings.backtest.oco_stop_gap_secs == 3.0


def test_apply_bot_settings_null_fields_not_overwrite(tmp_path):
    settings = _make_settings(tmp_path)
    settings.risk.sizing_mode = "vix"
    db_s = BotSettings(id=1)  # all new fields None
    apply_bot_settings(db_s, settings, [MagicMock()], [MagicMock()])
    assert settings.risk.sizing_mode == "vix"  # unchanged
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/preeth/projects/alphaTrade && python -m pytest tests/unit/test_apply_bot_settings.py -k "sizing_mode or portfolio_mode or stale_window or atr or vix or balanced or unbalanced or backtest or null_fields" -v
```

Expected: FAIL — `apply_bot_settings` doesn't handle these fields yet.

- [ ] **Step 3: Extend `apply_bot_settings` in `main.py`**

After line 149 (`settings.risk.model_retirement.min_evaluation_period = ...`) in `alphaTrade/main.py`, add:

```python
    if db_s.sizing_mode is not None:
        settings.risk.sizing_mode = db_s.sizing_mode
    if db_s.portfolio_mode is not None:
        settings.risk.portfolio_mode = db_s.portfolio_mode
    if db_s.order_stale_window_multiplier is not None:
        settings.risk.order_stale_window_multiplier = db_s.order_stale_window_multiplier
    if db_s.order_queue_max_depth is not None:
        settings.risk.order_queue_max_depth = db_s.order_queue_max_depth
    if db_s.balanced_max_sector_pct is not None:
        settings.risk.balanced.max_sector_pct = db_s.balanced_max_sector_pct
    if db_s.unbalanced_max_per_sector is not None:
        settings.risk.unbalanced.max_per_sector = db_s.unbalanced_max_per_sector
    if db_s.unbalanced_sector_overrides is not None:
        import json
        settings.risk.unbalanced.sector_overrides = json.loads(db_s.unbalanced_sector_overrides)
    if db_s.atr_risk_pct is not None:
        settings.risk.atr.risk_pct = db_s.atr_risk_pct
    if db_s.atr_multiplier is not None:
        settings.risk.atr.atr_multiplier = db_s.atr_multiplier
    if db_s.vix_base_size_pct is not None:
        settings.risk.vix.base_size_pct = db_s.vix_base_size_pct
    if db_s.vix_scalar is not None:
        settings.risk.vix.vix_scalar = db_s.vix_scalar
    if db_s.vix_max_size_pct is not None:
        settings.risk.vix.max_size_pct = db_s.vix_max_size_pct
    if db_s.backtest_slippage_bps is not None:
        settings.backtest.slippage_bps = db_s.backtest_slippage_bps
    if db_s.backtest_commission_per_trade is not None:
        settings.backtest.commission_per_trade = db_s.backtest_commission_per_trade
    if db_s.backtest_initial_equity is not None:
        settings.backtest.initial_equity = db_s.backtest_initial_equity
    if db_s.backtest_default_size_pct is not None:
        settings.backtest.default_size_pct = db_s.backtest_default_size_pct
    if db_s.backtest_sl_pct is not None:
        settings.backtest.sl_pct = db_s.backtest_sl_pct
    if db_s.backtest_tp_pct is not None:
        settings.backtest.tp_pct = db_s.backtest_tp_pct
    if db_s.backtest_schedule_enabled is not None:
        settings.backtest.schedule_enabled = db_s.backtest_schedule_enabled
    if db_s.backtest_cron is not None:
        settings.backtest.cron = db_s.backtest_cron
    if db_s.backtest_lookback_days is not None:
        settings.backtest.lookback_days = db_s.backtest_lookback_days
    if db_s.backtest_simulate_oco_lag is not None:
        settings.backtest.simulate_oco_lag = db_s.backtest_simulate_oco_lag
    if db_s.backtest_oco_stop_gap_secs is not None:
        settings.backtest.oco_stop_gap_secs = db_s.backtest_oco_stop_gap_secs
    if db_s.backtest_oco_limit_gap_secs is not None:
        settings.backtest.oco_limit_gap_secs = db_s.backtest_oco_limit_gap_secs
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
cd /home/preeth/projects/alphaTrade && python -m pytest tests/unit/test_apply_bot_settings.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add alphaTrade/main.py tests/unit/test_apply_bot_settings.py
git commit -m "feat(main): apply_bot_settings covers all risk/backtest/sizing DB fields"
```

---

### Task 6: Update retirement router — DB instead of YAML

**Files:**
- Modify: `alphaTrade/api/routers/retirement.py`
- Test: `tests/unit/test_retirement_api.py`

- [ ] **Step 1: Update retirement API tests**

Replace the `TestPerModelRetirementConfig` class in `tests/unit/test_retirement_api.py`:

```python
class TestPerModelRetirementConfig:
    def test_get_returns_null_overrides_and_effective_globals(self, tmp_path):
        engine = _make_engine(tmp_path)
        settings = _make_settings(tmp_path)
        client = _make_client(engine, settings)
        r = client.get("/api/v1/models/my_model/retirement")
        assert r.status_code == 200
        data = r.json()
        assert data["run_name"] == "my_model"
        assert data["enabled"] is None
        assert data["effective_enabled"] is False  # global default
        assert data["effective_min_win_rate"] == 0.4

    def test_patch_sets_override_and_persists_to_db(self, tmp_path):
        engine = _make_engine(tmp_path)
        settings = _make_settings(tmp_path)
        client = _make_client(engine, settings)
        r = client.patch("/api/v1/models/my_model/retirement", json={"enabled": False, "min_win_rate": 0.3})
        assert r.status_code == 200
        data = r.json()
        assert data["enabled"] is False
        assert data["min_win_rate"] == 0.3
        assert data["effective_enabled"] is False
        assert data["effective_min_win_rate"] == 0.3
        # persisted to DB
        with Session(engine) as s:
            from alphaTrade.store.repos import ModelOverrideRepo
            rec = ModelOverrideRepo(s).get("my_model")
            assert rec is not None
            assert rec.retirement_enabled is False
            assert rec.retirement_min_win_rate == 0.3
        # YAML not touched
        raw = settings.overrides_path.read_text()
        assert "my_model" not in raw

    def test_delete_clears_retirement_fields_in_db(self, tmp_path):
        engine = _make_engine(tmp_path)
        settings = _make_settings(tmp_path)
        client = _make_client(engine, settings)
        client.patch("/api/v1/models/my_model/retirement", json={"enabled": False})
        r = client.delete("/api/v1/models/my_model/retirement")
        assert r.status_code == 200
        with Session(engine) as s:
            from alphaTrade.store.repos import ModelOverrideRepo
            rec = ModelOverrideRepo(s).get("my_model")
            # retirement fields cleared
            assert rec is None or rec.retirement_enabled is None
```

Also remove the `import yaml` from the test file imports — it's no longer needed.

- [ ] **Step 2: Run updated tests, verify they fail**

```bash
cd /home/preeth/projects/alphaTrade && python -m pytest tests/unit/test_retirement_api.py::TestPerModelRetirementConfig -v
```

Expected: `test_patch_sets_override_and_persists_to_db` FAIL (still writes YAML), `test_delete_clears_retirement_fields_in_db` FAIL.

- [ ] **Step 3: Rewrite per-model retirement in `retirement.py`**

In `alphaTrade/api/routers/retirement.py`:

1. Remove the `_yaml_lock` and `_persist_retirement_overrides` function (lines ~79-110).

2. Change `_per_model_response` to read from DB:

```python
def _per_model_response(run_name: str, session: Session) -> PerModelRetirementResponse:
    from alphaTrade.store.repos import ModelOverrideRepo, ModelOverrideRecord
    rec = ModelOverrideRepo(session).get(run_name)
    override = ModelRetirementOverride(
        enabled=rec.retirement_enabled if rec else None,
        lookback_trades=rec.retirement_lookback_trades if rec else None,
        min_win_rate=rec.retirement_min_win_rate if rec else None,
        min_rolling_pnl=rec.retirement_min_rolling_pnl if rec else None,
        min_trades_before_evaluation=rec.retirement_min_trades_before_evaluation if rec else None,
        min_evaluation_period=rec.retirement_min_evaluation_period if rec else None,
    )
    effective = _effective_config(settings.risk.model_retirement, override)
    return PerModelRetirementResponse(
        run_name=run_name,
        enabled=override.enabled,
        lookback_trades=override.lookback_trades,
        min_win_rate=override.min_win_rate,
        min_rolling_pnl=override.min_rolling_pnl,
        min_trades_before_evaluation=override.min_trades_before_evaluation,
        min_evaluation_period=override.min_evaluation_period,
        effective_enabled=effective.enabled,
        effective_lookback_trades=effective.lookback_trades,
        effective_min_win_rate=effective.min_win_rate,
        effective_min_rolling_pnl=effective.min_rolling_pnl,
        effective_min_trades_before_evaluation=effective.min_trades_before_evaluation,
        effective_min_evaluation_period=effective.min_evaluation_period,
    )
```

Note: `_per_model_response` is now defined inside `make_router` where `settings` is in scope, so `settings` reference works. Add `session: Session` parameter to call sites.

3. Update the three per-model endpoints inside `make_router`:

```python
@router.get("/models/{run_name}/retirement", response_model=PerModelRetirementResponse)
def get_per_model(
    run_name: str,
    session: Session = Depends(session_dep),
    _: None = Depends(api_key_dep),
):
    return _per_model_response(run_name, session)

@router.patch("/models/{run_name}/retirement", response_model=PerModelRetirementResponse)
def patch_per_model(
    run_name: str,
    update: PerModelRetirementUpdate,
    session: Session = Depends(session_dep),
    _: None = Depends(api_key_dep),
):
    from alphaTrade.store.repos import ModelOverrideRepo, ModelOverrideRecord
    repo = ModelOverrideRepo(session)
    rec = repo.get(run_name) or ModelOverrideRecord(run_name=run_name)
    field_map = {
        "enabled": "retirement_enabled",
        "lookback_trades": "retirement_lookback_trades",
        "min_win_rate": "retirement_min_win_rate",
        "min_rolling_pnl": "retirement_min_rolling_pnl",
        "min_trades_before_evaluation": "retirement_min_trades_before_evaluation",
        "min_evaluation_period": "retirement_min_evaluation_period",
    }
    for field, val in update.model_dump(exclude_unset=True).items():
        setattr(rec, field_map[field], val)
    repo.upsert(rec)
    return _per_model_response(run_name, session)

@router.delete("/models/{run_name}/retirement")
def delete_per_model(
    run_name: str,
    session: Session = Depends(session_dep),
    _: None = Depends(api_key_dep),
):
    from alphaTrade.store.repos import ModelOverrideRepo
    repo = ModelOverrideRepo(session)
    rec = repo.get(run_name)
    if rec is not None:
        for col in ("retirement_enabled", "retirement_lookback_trades", "retirement_min_win_rate",
                    "retirement_min_rolling_pnl", "retirement_min_trades_before_evaluation",
                    "retirement_min_evaluation_period"):
            setattr(rec, col, None)
        repo.upsert(rec)
    return {"deleted": True, "run_name": run_name}
```

4. Remove the `import threading` (was used for `_yaml_lock`) and `import yaml` imports if no longer used elsewhere in the file.

- [ ] **Step 4: Run all retirement tests**

```bash
cd /home/preeth/projects/alphaTrade && python -m pytest tests/unit/test_retirement_api.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add alphaTrade/api/routers/retirement.py tests/unit/test_retirement_api.py
git commit -m "feat(retirement): per-model overrides write to DB; remove YAML write-back"
```

---

### Task 7: Full test suite + cleanup

- [ ] **Step 1: Run full test suite**

```bash
cd /home/preeth/projects/alphaTrade && python -m pytest tests/ -v --tb=short 2>&1 | tail -40
```

Expected: all PASS. Fix any failures before continuing.

- [ ] **Step 2: Verify YAML is never written at runtime**

```bash
grep -rn "write_text\|yaml\.dump\|open.*['\"]w" /home/preeth/projects/alphaTrade/alphaTrade --include="*.py" | grep -v __pycache__ | grep -v test_
```

Expected: no hits involving `overrides.yaml` or `overrides_path`.

- [ ] **Step 3: Final commit**

```bash
git add -u
git commit -m "chore: db-first overrides complete — YAML is read-only at runtime"
```
