# Model Retirement Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add time-based evaluation gating, per-model retirement config overrides, and a dedicated retirement API with full runtime mutability.

**Architecture:** `ModelRetirementOverride` nests inside `ModelOverride` (YAML + in-memory). `_effective_config` merges per-model overrides over global config at call time. `check_retirement` gains an OR gate (time elapsed OR trade count reached) replacing the old trade-count-only gate. A new `retirement.py` router exposes `GET/PATCH /retirement/config` (global) and `GET/PATCH/DELETE /models/{run_name}/retirement` + `POST /models/{run_name}/unretire` (per-model), wired with a direct `settings` reference for immediate in-memory effect.

**Tech Stack:** Python 3.11+, FastAPI, SQLModel, SQLite/Alembic, Pydantic-settings, PyYAML, pytest

---

## File Map

| Action | File |
|---|---|
| Modify | `alphaTrade/config.py` |
| Modify | `alphaTrade/risk/performance.py` |
| Modify | `alphaTrade/store/repos.py` |
| Create | `alphaTrade/store/migrations/versions/0009_retirement_config.py` |
| Modify | `alphaTrade/main.py` |
| Modify | `alphaTrade/broker/oco_monitor.py` |
| Create | `alphaTrade/api/routers/retirement.py` |
| Modify | `alphaTrade/api/app.py` |
| Create | `tests/unit/test_retirement_config.py` |
| Modify | `tests/unit/test_model_performance.py` |
| Create | `tests/unit/test_retirement_api.py` |
| Modify | `tests/unit/test_oco_monitor.py` |

---

## Task 1: Config classes + merge helpers

**Files:**
- Modify: `alphaTrade/config.py`
- Modify: `alphaTrade/risk/performance.py`
- Create: `tests/unit/test_retirement_config.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_retirement_config.py`:

```python
"""Tests for ModelRetirementOverride merge logic and period parser."""
import pytest
from alphaTrade.config import ModelRetirementConfig, ModelRetirementOverride
from alphaTrade.risk.performance import _effective_config, _parse_period
from datetime import timedelta


def _global():
    return ModelRetirementConfig(
        enabled=True,
        lookback_trades=20,
        min_win_rate=0.4,
        min_rolling_pnl=-500.0,
        min_evaluation_period="30d",
        min_trades_before_evaluation=5,
    )


def test_none_per_model_fields_fall_back_to_global():
    cfg = _effective_config(_global(), ModelRetirementOverride())
    assert cfg.enabled is True
    assert cfg.lookback_trades == 20
    assert cfg.min_win_rate == 0.4
    assert cfg.min_rolling_pnl == -500.0
    assert cfg.min_evaluation_period == "30d"
    assert cfg.min_trades_before_evaluation == 5


def test_non_none_per_model_fields_override_global():
    override = ModelRetirementOverride(
        min_win_rate=0.3,
        min_rolling_pnl=-200.0,
        lookback_trades=10,
        min_trades_before_evaluation=3,
        min_evaluation_period="60d",
    )
    cfg = _effective_config(_global(), override)
    assert cfg.min_win_rate == 0.3
    assert cfg.min_rolling_pnl == -200.0
    assert cfg.lookback_trades == 10
    assert cfg.min_trades_before_evaluation == 3
    assert cfg.min_evaluation_period == "60d"
    # non-overridden fields still come from global
    assert cfg.enabled is True


def test_per_model_enabled_false_overrides_global_true():
    override = ModelRetirementOverride(enabled=False)
    cfg = _effective_config(_global(), override)
    assert cfg.enabled is False


def test_parse_period_days():
    assert _parse_period("30d") == timedelta(days=30)
    assert _parse_period("7d") == timedelta(days=7)
    assert _parse_period("1d") == timedelta(days=1)


def test_parse_period_invalid_raises():
    with pytest.raises(ValueError, match="Invalid period format"):
        _parse_period("1m")
    with pytest.raises(ValueError, match="Invalid period format"):
        _parse_period("30")
    with pytest.raises(ValueError, match="Invalid period format"):
        _parse_period("")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/preeth/projects/alphaTrade
python -m pytest tests/unit/test_retirement_config.py -v
```

Expected: `ImportError` or `AttributeError` — `ModelRetirementOverride` and new fields don't exist yet.

- [ ] **Step 3: Add `ModelRetirementOverride` and new fields to `alphaTrade/config.py`**

In `alphaTrade/config.py`, after the `ModelRetirementConfig` class, add:

```python
class ModelRetirementOverride(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    enabled: Optional[bool] = None
    lookback_trades: Optional[int] = None
    min_win_rate: Optional[float] = None
    min_rolling_pnl: Optional[float] = None
    min_trades_before_evaluation: Optional[int] = None
    min_evaluation_period: Optional[str] = None
```

Update `ModelRetirementConfig` to add the two new fields:

```python
class ModelRetirementConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    enabled: bool = False
    lookback_trades: int = 20
    min_win_rate: float = 0.4
    min_rolling_pnl: float = -500.0
    auto_reload: bool = True
    min_evaluation_period: str = "30d"
    min_trades_before_evaluation: int = 5
```

Update `ModelOverride` to include the retirement field:

```python
class ModelOverride(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    enabled: bool = True
    t212_ticker: Optional[str] = None
    size_pct: Optional[float] = None
    backtest: BacktestScheduleOverride = BacktestScheduleOverride()
    retirement: "ModelRetirementOverride" = None  # set in __init__ to avoid forward ref issues

    def __init__(self, **data):
        if "retirement" not in data:
            data["retirement"] = ModelRetirementOverride()
        super().__init__(**data)
```

Wait — pydantic-settings handles forward refs fine if `ModelRetirementOverride` is defined before `ModelOverride`. Reorder so `ModelRetirementOverride` comes before `ModelOverride` in the file, then:

```python
class ModelOverride(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    enabled: bool = True
    t212_ticker: Optional[str] = None
    size_pct: Optional[float] = None
    backtest: BacktestScheduleOverride = BacktestScheduleOverride()
    retirement: ModelRetirementOverride = ModelRetirementOverride()
```

Also update `_load_overrides` in `Settings` to parse the `retirement` key under each model:

```python
if "models" in raw:
    self.model_overrides = {
        run_name: ModelOverride(**(cfg or {}))
        for run_name, cfg in raw["models"].items()
    }
```

This already works because `ModelOverride(**cfg)` will pass `retirement: dict` to pydantic which constructs `ModelRetirementOverride` from it. No change needed — pydantic-settings handles nested model construction from dicts.

- [ ] **Step 4: Add `_parse_period` and `_effective_config` to `alphaTrade/risk/performance.py`**

Add at the top of `alphaTrade/risk/performance.py` after the imports:

```python
from datetime import datetime, timedelta
from alphaTrade.config import ModelRetirementConfig, ModelRetirementOverride


def _parse_period(s: str) -> timedelta:
    if s.endswith("d"):
        return timedelta(days=int(s[:-1]))
    raise ValueError(f"Invalid period format: {s!r}. Use e.g. '30d'")


def _effective_config(
    global_cfg: ModelRetirementConfig,
    per_model: ModelRetirementOverride,
) -> ModelRetirementConfig:
    return ModelRetirementConfig(
        enabled=per_model.enabled if per_model.enabled is not None else global_cfg.enabled,
        lookback_trades=per_model.lookback_trades if per_model.lookback_trades is not None else global_cfg.lookback_trades,
        min_win_rate=per_model.min_win_rate if per_model.min_win_rate is not None else global_cfg.min_win_rate,
        min_rolling_pnl=per_model.min_rolling_pnl if per_model.min_rolling_pnl is not None else global_cfg.min_rolling_pnl,
        min_trades_before_evaluation=per_model.min_trades_before_evaluation if per_model.min_trades_before_evaluation is not None else global_cfg.min_trades_before_evaluation,
        min_evaluation_period=per_model.min_evaluation_period if per_model.min_evaluation_period is not None else global_cfg.min_evaluation_period,
    )
```

Note: `datetime` is already imported in this file. Check before adding a duplicate import.

- [ ] **Step 5: Run tests — expect pass**

```bash
python -m pytest tests/unit/test_retirement_config.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add alphaTrade/config.py alphaTrade/risk/performance.py tests/unit/test_retirement_config.py
git commit -m "feat(retirement): ModelRetirementOverride config class and merge helpers"
```

---

## Task 2: DB schema — `first_trade_at` + `BotSettings` retirement fields

**Files:**
- Modify: `alphaTrade/store/repos.py`
- Create: `alphaTrade/store/migrations/versions/0009_retirement_config.py`
- Modify: `tests/unit/test_model_performance.py`

- [ ] **Step 1: Write failing test for `first_trade_at`**

Add to `tests/unit/test_model_performance.py` (after existing tests):

```python
def test_first_trade_at_set_on_first_trade(engine):
    cfg = ModelRetirementConfig(enabled=False)
    with Session(engine) as s:
        record_trade(s, model_id="model_x", realized_pnl=10.0, cfg=cfg)
        perf = ModelPerformanceRepo(s).get_or_create("model_x")
        assert perf.first_trade_at is not None

def test_first_trade_at_not_updated_on_subsequent_trades(engine):
    cfg = ModelRetirementConfig(enabled=False)
    with Session(engine) as s:
        record_trade(s, model_id="model_x", realized_pnl=10.0, cfg=cfg)
        perf = ModelPerformanceRepo(s).get_or_create("model_x")
        first = perf.first_trade_at
    with Session(engine) as s:
        record_trade(s, model_id="model_x", realized_pnl=20.0, cfg=cfg)
        perf = ModelPerformanceRepo(s).get_or_create("model_x")
        assert perf.first_trade_at == first
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/unit/test_model_performance.py::test_first_trade_at_set_on_first_trade tests/unit/test_model_performance.py::test_first_trade_at_not_updated_on_subsequent_trades -v
```

Expected: `AttributeError: 'ModelPerformance' object has no attribute 'first_trade_at'`

- [ ] **Step 3: Add `first_trade_at` to `ModelPerformance` in `alphaTrade/store/repos.py`**

Find the `ModelPerformance` class (around line 252) and add the field after `retired_at`:

```python
class ModelPerformance(SQLModel, table=True):
    model_id: str = Field(primary_key=True)
    trade_count: int = Field(default=0)
    win_count: int = Field(default=0)
    rolling_pnl: float = Field(default=0.0)
    rolling_trades_json: str = Field(default="[]")
    retired: bool = Field(default=False)
    retired_at: Optional[datetime] = Field(default=None)
    first_trade_at: Optional[datetime] = Field(default=None)
    last_updated: Optional[datetime] = Field(default=None)
```

- [ ] **Step 4: Add retirement fields to `BotSettings` in `alphaTrade/store/repos.py`**

Find `BotSettings` (around line 443) and add after `alphaTrade_api_key`:

```python
    retirement_enabled: Optional[bool] = Field(default=None, sa_column=Column(Boolean, nullable=True))
    retirement_lookback_trades: Optional[int] = Field(default=None, sa_column=Column(Integer, nullable=True))
    retirement_min_win_rate: Optional[float] = Field(default=None, sa_column=Column(Float, nullable=True))
    retirement_min_rolling_pnl: Optional[float] = Field(default=None, sa_column=Column(Float, nullable=True))
    retirement_min_trades_before_evaluation: Optional[int] = Field(default=None, sa_column=Column(Integer, nullable=True))
    retirement_min_evaluation_period: Optional[str] = Field(default=None, sa_column=Column(String, nullable=True))
```

Note: `Column`, `Boolean`, `Integer`, `Float`, `String` must be imported. Check the existing imports in `repos.py` — add any missing ones from `sqlalchemy`.

- [ ] **Step 5: Create migration `0009_retirement_config.py`**

Create `alphaTrade/store/migrations/versions/0009_retirement_config.py`:

```python
"""Add first_trade_at to modelperformance and retirement fields to botsettings.

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-18
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # Add first_trade_at to modelperformance
    mp_cols = [row[1] for row in conn.execute(sa.text("PRAGMA table_info(modelperformance)"))]
    if "first_trade_at" not in mp_cols:
        op.add_column("modelperformance", sa.Column("first_trade_at", sa.DateTime(), nullable=True))

    # Add retirement fields to botsettings
    bs_cols = [row[1] for row in conn.execute(sa.text("PRAGMA table_info(botsettings)"))]
    new_cols = [
        ("retirement_enabled", sa.Boolean(), None),
        ("retirement_lookback_trades", sa.Integer(), None),
        ("retirement_min_win_rate", sa.Float(), None),
        ("retirement_min_rolling_pnl", sa.Float(), None),
        ("retirement_min_trades_before_evaluation", sa.Integer(), None),
        ("retirement_min_evaluation_period", sa.String(), None),
    ]
    for col_name, col_type, _ in new_cols:
        if col_name not in bs_cols:
            op.add_column("botsettings", sa.Column(col_name, col_type, nullable=True))


def downgrade() -> None:
    raise NotImplementedError("Downgrade not supported for 0009.")
```

- [ ] **Step 6: Run tests — expect pass**

```bash
python -m pytest tests/unit/test_model_performance.py::test_first_trade_at_set_on_first_trade tests/unit/test_model_performance.py::test_first_trade_at_not_updated_on_subsequent_trades -v
```

Expected: FAIL — `record_trade` doesn't set `first_trade_at` yet. That's correct — implementation comes in Task 3. These tests remain failing until Task 3.

- [ ] **Step 7: Verify migration runs without error**

```bash
python -c "
from alphaTrade.store.db import run_migrations, get_engine
import tempfile, pathlib
with tempfile.TemporaryDirectory() as d:
    db = pathlib.Path(d) / 'test.db'
    run_migrations(db)
    print('Migration OK')
"
```

Expected: `Migration OK`

- [ ] **Step 8: Commit**

```bash
git add alphaTrade/store/repos.py alphaTrade/store/migrations/versions/0009_retirement_config.py tests/unit/test_model_performance.py
git commit -m "feat(retirement): add first_trade_at to ModelPerformance and retirement fields to BotSettings"
```

---

## Task 3: Updated `record_trade` and `check_retirement` logic

**Files:**
- Modify: `alphaTrade/risk/performance.py`
- Modify: `tests/unit/test_model_performance.py`

- [ ] **Step 1: Write failing tests for OR gate**

Add to `tests/unit/test_model_performance.py`:

```python
from datetime import datetime, timedelta
from unittest.mock import patch
from alphaTrade.risk.performance import _effective_config
from alphaTrade.config import ModelRetirementOverride


def test_retirement_fires_after_trade_count_gate(engine):
    """Retires when trade count reached, even before evaluation period elapsed."""
    cfg = ModelRetirementConfig(
        enabled=True,
        lookback_trades=5,
        min_win_rate=0.9,   # impossible to meet
        min_rolling_pnl=-9999,
        min_trades_before_evaluation=3,
        min_evaluation_period="9999d",  # will never elapse
    )
    with Session(engine) as s:
        for _ in range(3):
            record_trade(s, model_id="m", realized_pnl=-10.0, cfg=cfg)
        retired = check_retirement(s, model_id="m", cfg=cfg)
    assert retired is True


def test_retirement_fires_after_evaluation_period(engine):
    """Retires when evaluation period elapsed, even if trade count gate not met."""
    cfg = ModelRetirementConfig(
        enabled=True,
        lookback_trades=5,
        min_win_rate=0.9,   # impossible to meet
        min_rolling_pnl=-9999,
        min_trades_before_evaluation=9999,  # will never be met by trade count alone
        min_evaluation_period="1d",
    )
    with Session(engine) as s:
        record_trade(s, model_id="m2", realized_pnl=-10.0, cfg=cfg)
        # backdate first_trade_at to 2 days ago
        perf = ModelPerformanceRepo(s).get_or_create("m2")
        perf.first_trade_at = datetime.utcnow() - timedelta(days=2)
        s.add(perf)
        s.commit()
        retired = check_retirement(s, model_id="m2", cfg=cfg)
    assert retired is True


def test_retirement_not_triggered_when_neither_gate_met(engine):
    """Neither trade count nor period gate met — no evaluation."""
    cfg = ModelRetirementConfig(
        enabled=True,
        lookback_trades=5,
        min_win_rate=0.9,
        min_rolling_pnl=-9999,
        min_trades_before_evaluation=99,
        min_evaluation_period="9999d",
    )
    with Session(engine) as s:
        record_trade(s, model_id="m3", realized_pnl=-10.0, cfg=cfg)
        retired = check_retirement(s, model_id="m3", cfg=cfg)
    assert retired is False


def test_already_retired_returns_true_immediately(engine):
    """Already-retired model short-circuits without re-evaluating."""
    cfg = ModelRetirementConfig(
        enabled=True,
        lookback_trades=5,
        min_win_rate=0.0,   # would pass if evaluated
        min_rolling_pnl=-9999,
        min_trades_before_evaluation=1,
        min_evaluation_period="1d",
    )
    with Session(engine) as s:
        record_trade(s, model_id="m4", realized_pnl=100.0, cfg=cfg)
        perf = ModelPerformanceRepo(s).get_or_create("m4")
        perf.retired = True
        s.add(perf)
        s.commit()
        result = check_retirement(s, model_id="m4", cfg=cfg)
    assert result is True
```

- [ ] **Step 2: Run new tests to verify they fail**

```bash
python -m pytest tests/unit/test_model_performance.py::test_retirement_fires_after_trade_count_gate tests/unit/test_model_performance.py::test_retirement_fires_after_evaluation_period tests/unit/test_model_performance.py::test_retirement_not_triggered_when_neither_gate_met tests/unit/test_model_performance.py::test_already_retired_returns_true_immediately -v
```

Expected: FAIL — `ModelRetirementConfig` missing new fields, and `check_retirement` uses old trade-count gate.

- [ ] **Step 3: Update `record_trade` in `alphaTrade/risk/performance.py`**

In the `record_trade` function, add `first_trade_at` assignment before incrementing `trade_count`:

```python
def record_trade(
    session: Session,
    model_id: str,
    realized_pnl: float,
    cfg: ModelRetirementConfig,
) -> None:
    repo = ModelPerformanceRepo(session)
    perf = repo.get_or_create(model_id)

    trades: list[float] = json.loads(perf.rolling_trades_json)
    trades.append(realized_pnl)
    if len(trades) > cfg.lookback_trades:
        trades = trades[-cfg.lookback_trades:]

    perf.rolling_trades_json = json.dumps(trades)
    if perf.trade_count == 0:
        perf.first_trade_at = datetime.utcnow()
    perf.trade_count += 1
    if realized_pnl > 0:
        perf.win_count += 1
    perf.rolling_pnl = sum(trades)

    repo.update(perf)
```

- [ ] **Step 4: Replace `check_retirement` in `alphaTrade/risk/performance.py`**

Replace the full function with:

```python
def check_retirement(
    session: Session,
    model_id: str,
    cfg: ModelRetirementConfig,
) -> bool:
    """Return True and mark retired if model breaches thresholds, False otherwise."""
    if not cfg.enabled:
        return False

    repo = ModelPerformanceRepo(session)
    perf = repo.get_or_create(model_id)

    if perf.retired:
        return True

    period = _parse_period(cfg.min_evaluation_period)
    age_ok = (
        perf.first_trade_at is not None
        and (datetime.utcnow() - perf.first_trade_at) >= period
    )
    trades_ok = perf.trade_count >= cfg.min_trades_before_evaluation

    if not (age_ok or trades_ok):
        return False

    trades: list[float] = json.loads(perf.rolling_trades_json)
    win_rate = sum(1 for t in trades if t > 0) / len(trades) if trades else 0.0
    rolling_pnl = sum(trades)

    should_retire = win_rate < cfg.min_win_rate or rolling_pnl < cfg.min_rolling_pnl
    if should_retire:
        perf.retired = True
        perf.retired_at = datetime.utcnow()
        repo.update(perf)
        log.warning(
            "Model %s retired: win_rate=%.2f (min=%.2f) rolling_pnl=%.2f (min=%.2f)",
            model_id, win_rate, cfg.min_win_rate, rolling_pnl, cfg.min_rolling_pnl,
        )
    return should_retire
```

- [ ] **Step 5: Run all performance tests**

```bash
python -m pytest tests/unit/test_model_performance.py -v
```

Expected: all tests PASS including the two `first_trade_at` tests from Task 2 (now that `record_trade` sets it) and all four new OR gate tests.

- [ ] **Step 6: Commit**

```bash
git add alphaTrade/risk/performance.py tests/unit/test_model_performance.py
git commit -m "feat(retirement): OR gate evaluation (time elapsed or trade count), first_trade_at tracking"
```

---

## Task 4: Update callers — tick loop + OCO monitor

**Files:**
- Modify: `alphaTrade/main.py`
- Modify: `alphaTrade/broker/oco_monitor.py`
- Modify: `tests/unit/test_oco_monitor.py`

- [ ] **Step 1: Write failing test for OCO monitor with merged config**

Add to `tests/unit/test_oco_monitor.py`:

```python
from alphaTrade.config import ModelRetirementConfig, ModelRetirementOverride
from alphaTrade.risk.performance import record_trade, check_retirement, _effective_config
from alphaTrade.store.repos import ModelPerformanceRepo
from sqlmodel import Session


def test_oco_retirement_uses_passed_config(tmp_path):
    """OCO monitor fires retirement using the passed retirement_cfg, not defaults."""
    engine = _make_engine()
    _seed_position(engine)

    retirement_cfg = ModelRetirementConfig(
        enabled=True,
        lookback_trades=1,
        min_win_rate=0.9,    # impossible — will retire on first loss
        min_rolling_pnl=-9999,
        min_trades_before_evaluation=1,
        min_evaluation_period="9999d",
    )

    t212 = MagicMock()
    t212.get_order.side_effect = [
        {"status": "FILLED", "fillPrice": "170.0"},  # stop fills
        {"status": "PENDING"},
    ]
    t212.cancel_order.return_value = None

    import asyncio
    with patch("alphaTrade.broker.oco_monitor.wh"):
        asyncio.run(monitor_oco(
            t212=t212,
            t212_ticker="AAPL_US_EQ",
            stop_order_id="s1",
            limit_order_id="l1",
            engine=engine,
            cooldown_td=timedelta(hours=1),
            entry_price=175.0,
            sl_price=170.0,
            tp_price=182.0,
            quantity=1.0,
            model_id="my_model",
            retirement_cfg=retirement_cfg,
        ))

    with Session(engine) as s:
        perf = ModelPerformanceRepo(s).get_or_create("my_model")
        assert perf.retired is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/unit/test_oco_monitor.py::test_oco_retirement_uses_passed_config -v
```

Expected: FAIL — `monitor_oco` has `retirement_cfg` param already (added earlier) but the internal logic may not propagate it fully. Run to confirm the exact failure.

- [ ] **Step 3: Update tick loop in `alphaTrade/main.py`**

Find the two `monitor_oco` calls in `main.py`.

**First call (around line 556)** — the active trade call. Replace to pass merged config:

```python
per_model_ret = settings.model_overrides.get(manifest.run_name, ModelOverride()).retirement
_ret_cfg = _effective_config(settings.risk.model_retirement, per_model_ret)
_task = asyncio.create_task(monitor_oco(
    t212=t212,
    t212_ticker=t212_ticker,
    stop_order_id=stop_id,
    limit_order_id=limit_id,
    engine=engine,
    cooldown_td=cooldown_td,
    entry_price=entry_price,
    sl_price=sl_price,
    tp_price=tp_price,
    quantity=qty,
    model_id=manifest.run_name,
    entry_time=datetime.utcnow(),
    retirement_cfg=_ret_cfg,
))
```

Also add the `check_retirement` + `record_trade` block in the tick loop — find where it's already called (around line 608) and update it to use the merged config:

```python
per_model_ret = settings.model_overrides.get(manifest.run_name, ModelOverride()).retirement
_ret_cfg = _effective_config(settings.risk.model_retirement, per_model_ret)
record_trade(session, model_id=manifest.run_name,
             realized_pnl=realized_pnl,
             cfg=_ret_cfg)
if check_retirement(session, model_id=manifest.run_name,
                    cfg=_ret_cfg):
    msg = f"Model {manifest.run_name} auto-retired: performance below threshold"
    wh.notify("WARNING", msg, category="model-retirement")
```

**Second call (around line 733)** — startup rescan with `model_id=""`. Leave `retirement_cfg=None` (already correct — empty model_id means no retirement fires).

Add the missing imports at the top of `main.py` if not already present:

```python
from alphaTrade.risk.performance import _effective_config
from alphaTrade.config import ModelRetirementOverride
```

- [ ] **Step 4: Run the OCO test**

```bash
python -m pytest tests/unit/test_oco_monitor.py -v
```

Expected: all tests PASS including the new retirement test.

- [ ] **Step 5: Run full existing test suite to check no regressions**

```bash
python -m pytest tests/unit/ -v --tb=short
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add alphaTrade/main.py alphaTrade/broker/oco_monitor.py tests/unit/test_oco_monitor.py
git commit -m "feat(retirement): pass merged effective config to tick loop and OCO monitor"
```

---

## Task 5: `apply_bot_settings` — runtime global retirement config

**Files:**
- Modify: `alphaTrade/main.py`

- [ ] **Step 1: Update `apply_bot_settings` in `alphaTrade/main.py`**

Find the `apply_bot_settings` function (around line 84) and add at the end:

```python
    if db_s.retirement_enabled is not None:
        settings.risk.model_retirement.enabled = db_s.retirement_enabled
    if db_s.retirement_lookback_trades is not None:
        settings.risk.model_retirement.lookback_trades = db_s.retirement_lookback_trades
    if db_s.retirement_min_win_rate is not None:
        settings.risk.model_retirement.min_win_rate = db_s.retirement_min_win_rate
    if db_s.retirement_min_rolling_pnl is not None:
        settings.risk.model_retirement.min_rolling_pnl = db_s.retirement_min_rolling_pnl
    if db_s.retirement_min_trades_before_evaluation is not None:
        settings.risk.model_retirement.min_trades_before_evaluation = db_s.retirement_min_trades_before_evaluation
    if db_s.retirement_min_evaluation_period is not None:
        settings.risk.model_retirement.min_evaluation_period = db_s.retirement_min_evaluation_period
```

- [ ] **Step 2: Verify import for `BotSettings` retirement fields**

Run a quick smoke test:

```bash
python -c "
from alphaTrade.store.repos import BotSettings
b = BotSettings(id=1)
print('retirement_enabled:', b.retirement_enabled)
print('OK')
"
```

Expected: `retirement_enabled: None` and `OK`.

- [ ] **Step 3: Commit**

```bash
git add alphaTrade/main.py
git commit -m "feat(retirement): apply_bot_settings merges DB retirement fields into settings each tick"
```

---

## Task 6: Retirement router

**Files:**
- Create: `alphaTrade/api/routers/retirement.py`
- Modify: `alphaTrade/api/app.py`
- Create: `tests/unit/test_retirement_api.py`

- [ ] **Step 1: Write failing API tests**

Create `tests/unit/test_retirement_api.py`:

```python
"""Tests for retirement config API endpoints."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from alphaTrade.api.app import create_app
from alphaTrade.config import ModelRetirementConfig, ModelRetirementOverride, ModelOverride, Settings
from alphaTrade.health import HealthState
from alphaTrade.store.db import run_migrations
from alphaTrade.store.repos import BotSettings, BotSettingsRepo, ModelPerformance, ModelPerformanceRepo


def _make_engine(tmp_path):
    db = tmp_path / "test.db"
    run_migrations(db)
    return create_engine(f"sqlite:///{db}")


def _make_settings(tmp_path) -> Settings:
    overrides = tmp_path / "overrides.yaml"
    overrides.write_text("")
    import os
    os.environ.setdefault("ALPHATRADE_API_KEY", "test-key")
    return Settings(
        overrides_path=overrides,
        state_db_path=tmp_path / "state.db",
    )


def _make_client(engine, settings):
    app = create_app(engine, HealthState(), settings=settings)
    return TestClient(app, headers={"X-API-Key": "test-key"})


class TestGlobalRetirementConfig:
    def test_get_returns_yaml_defaults(self, tmp_path):
        engine = _make_engine(tmp_path)
        settings = _make_settings(tmp_path)
        client = _make_client(engine, settings)
        r = client.get("/api/v1/retirement/config")
        assert r.status_code == 200
        data = r.json()
        assert data["enabled"] is False
        assert data["lookback_trades"] == 20
        assert data["min_win_rate"] == 0.4
        assert data["min_rolling_pnl"] == -500.0
        assert data["min_trades_before_evaluation"] == 5
        assert data["min_evaluation_period"] == "30d"

    def test_patch_updates_in_memory_and_db(self, tmp_path):
        engine = _make_engine(tmp_path)
        settings = _make_settings(tmp_path)
        client = _make_client(engine, settings)
        r = client.patch("/api/v1/retirement/config", json={"enabled": True, "min_win_rate": 0.55})
        assert r.status_code == 200
        data = r.json()
        assert data["enabled"] is True
        assert data["min_win_rate"] == 0.55
        # in-memory mutated immediately
        assert settings.risk.model_retirement.enabled is True
        assert settings.risk.model_retirement.min_win_rate == 0.55
        # persisted to DB
        with Session(engine) as s:
            bs = BotSettingsRepo(s).get()
            assert bs.retirement_enabled is True
            assert bs.retirement_min_win_rate == 0.55

    def test_patch_invalid_period_returns_422(self, tmp_path):
        engine = _make_engine(tmp_path)
        settings = _make_settings(tmp_path)
        client = _make_client(engine, settings)
        r = client.patch("/api/v1/retirement/config", json={"min_evaluation_period": "1month"})
        assert r.status_code == 422


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

    def test_patch_sets_override_and_persists_to_yaml(self, tmp_path):
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
        # in-memory
        assert settings.model_overrides["my_model"].retirement.enabled is False
        # YAML persisted
        raw = yaml.safe_load(settings.overrides_path.read_text())
        assert raw["models"]["my_model"]["retirement"]["enabled"] is False

    def test_delete_clears_overrides_and_reverts_to_global(self, tmp_path):
        engine = _make_engine(tmp_path)
        settings = _make_settings(tmp_path)
        client = _make_client(engine, settings)
        client.patch("/api/v1/models/my_model/retirement", json={"enabled": False})
        r = client.delete("/api/v1/models/my_model/retirement")
        assert r.status_code == 200
        assert settings.model_overrides.get("my_model") is None or \
               settings.model_overrides["my_model"].retirement.enabled is None


class TestUnretire:
    def test_unretire_clears_retired_flag_and_resets_stats(self, tmp_path):
        engine = _make_engine(tmp_path)
        settings = _make_settings(tmp_path)
        client = _make_client(engine, settings)
        # seed a retired model
        with Session(engine) as s:
            perf = ModelPerformanceRepo(s).get_or_create("old_model")
            perf.retired = True
            perf.retired_at = datetime.utcnow()
            perf.trade_count = 10
            perf.win_count = 3
            perf.rolling_pnl = -300.0
            perf.rolling_trades_json = "[-50, -100]"
            perf.first_trade_at = datetime.utcnow()
            s.add(perf)
            s.commit()
        r = client.post("/api/v1/models/old_model/unretire")
        assert r.status_code == 200
        with Session(engine) as s:
            perf = ModelPerformanceRepo(s).get_or_create("old_model")
            assert perf.retired is False
            assert perf.retired_at is None
            assert perf.trade_count == 0
            assert perf.win_count == 0
            assert perf.rolling_pnl == 0.0
            assert perf.rolling_trades_json == "[]"
            assert perf.first_trade_at is None

    def test_unretire_nonexistent_model_returns_404(self, tmp_path):
        engine = _make_engine(tmp_path)
        settings = _make_settings(tmp_path)
        client = _make_client(engine, settings)
        r = client.post("/api/v1/models/ghost_model/unretire")
        assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/unit/test_retirement_api.py -v
```

Expected: `ImportError` — `retirement` router doesn't exist yet, and `create_app` doesn't accept `settings`.

- [ ] **Step 3: Create `alphaTrade/api/routers/retirement.py`**

```python
"""Retirement config API — global and per-model."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Optional

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlmodel import Session

from alphaTrade.config import ModelOverride, ModelRetirementOverride, Settings
from alphaTrade.risk.performance import _effective_config, _parse_period
from alphaTrade.store.repos import BotSettingsRepo, ModelPerformanceRepo

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class GlobalRetirementUpdate(BaseModel):
    enabled: Optional[bool] = None
    lookback_trades: Optional[int] = None
    min_win_rate: Optional[float] = None
    min_rolling_pnl: Optional[float] = None
    min_trades_before_evaluation: Optional[int] = None
    min_evaluation_period: Optional[str] = None

    @field_validator("min_evaluation_period")
    @classmethod
    def _valid_period(cls, v: str | None) -> str | None:
        if v is not None:
            _parse_period(v)  # raises ValueError on bad format
        return v


class GlobalRetirementResponse(BaseModel):
    enabled: bool
    lookback_trades: int
    min_win_rate: float
    min_rolling_pnl: float
    min_trades_before_evaluation: int
    min_evaluation_period: str


class PerModelRetirementUpdate(BaseModel):
    enabled: Optional[bool] = None
    lookback_trades: Optional[int] = None
    min_win_rate: Optional[float] = None
    min_rolling_pnl: Optional[float] = None
    min_trades_before_evaluation: Optional[int] = None
    min_evaluation_period: Optional[str] = None

    @field_validator("min_evaluation_period")
    @classmethod
    def _valid_period(cls, v: str | None) -> str | None:
        if v is not None:
            _parse_period(v)
        return v


class PerModelRetirementResponse(BaseModel):
    run_name: str
    enabled: Optional[bool] = None
    lookback_trades: Optional[int] = None
    min_win_rate: Optional[float] = None
    min_rolling_pnl: Optional[float] = None
    min_trades_before_evaluation: Optional[int] = None
    min_evaluation_period: Optional[str] = None
    effective_enabled: bool
    effective_lookback_trades: int
    effective_min_win_rate: float
    effective_min_rolling_pnl: float
    effective_min_trades_before_evaluation: int
    effective_min_evaluation_period: str


# ---------------------------------------------------------------------------
# YAML persistence
# ---------------------------------------------------------------------------

def _persist_retirement_overrides(settings: Settings) -> None:
    path = settings.overrides_path
    raw: dict = yaml.safe_load(path.read_text()) if path.exists() and path.stat().st_size > 0 else {}
    raw.setdefault("models", {})
    for run_name, override in settings.model_overrides.items():
        ret = override.retirement
        ret_dict: dict = {}
        if ret.enabled is not None:
            ret_dict["enabled"] = ret.enabled
        if ret.lookback_trades is not None:
            ret_dict["lookback_trades"] = ret.lookback_trades
        if ret.min_win_rate is not None:
            ret_dict["min_win_rate"] = ret.min_win_rate
        if ret.min_rolling_pnl is not None:
            ret_dict["min_rolling_pnl"] = ret.min_rolling_pnl
        if ret.min_trades_before_evaluation is not None:
            ret_dict["min_trades_before_evaluation"] = ret.min_trades_before_evaluation
        if ret.min_evaluation_period is not None:
            ret_dict["min_evaluation_period"] = ret.min_evaluation_period
        raw["models"].setdefault(run_name, {})
        if ret_dict:
            raw["models"][run_name]["retirement"] = ret_dict
        elif "retirement" in raw["models"].get(run_name, {}):
            del raw["models"][run_name]["retirement"]
    path.write_text(yaml.dump(raw, default_flow_style=False))


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def make_router(session_dep: Callable, api_key_dep: Callable, settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.get("/retirement/config", response_model=GlobalRetirementResponse)
    def get_global_config(_: None = Depends(api_key_dep)):
        cfg = settings.risk.model_retirement
        return GlobalRetirementResponse(
            enabled=cfg.enabled,
            lookback_trades=cfg.lookback_trades,
            min_win_rate=cfg.min_win_rate,
            min_rolling_pnl=cfg.min_rolling_pnl,
            min_trades_before_evaluation=cfg.min_trades_before_evaluation,
            min_evaluation_period=cfg.min_evaluation_period,
        )

    @router.patch("/retirement/config", response_model=GlobalRetirementResponse)
    def patch_global_config(
        update: GlobalRetirementUpdate,
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        cfg = settings.risk.model_retirement
        repo = BotSettingsRepo(session)
        bs = repo.get()
        if bs is None:
            from alphaTrade.store.repos import BotSettings
            bs = BotSettings(id=1)

        for field, val in update.model_dump(exclude_unset=True).items():
            setattr(cfg, field, val)
            setattr(bs, f"retirement_{field}", val)

        repo.upsert(bs)
        return GlobalRetirementResponse(
            enabled=cfg.enabled,
            lookback_trades=cfg.lookback_trades,
            min_win_rate=cfg.min_win_rate,
            min_rolling_pnl=cfg.min_rolling_pnl,
            min_trades_before_evaluation=cfg.min_trades_before_evaluation,
            min_evaluation_period=cfg.min_evaluation_period,
        )

    def _per_model_response(run_name: str) -> PerModelRetirementResponse:
        override = settings.model_overrides.get(run_name, ModelOverride()).retirement
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

    @router.get("/models/{run_name}/retirement", response_model=PerModelRetirementResponse)
    def get_per_model(run_name: str, _: None = Depends(api_key_dep)):
        return _per_model_response(run_name)

    @router.patch("/models/{run_name}/retirement", response_model=PerModelRetirementResponse)
    def patch_per_model(
        run_name: str,
        update: PerModelRetirementUpdate,
        _: None = Depends(api_key_dep),
    ):
        if run_name not in settings.model_overrides:
            settings.model_overrides[run_name] = ModelOverride()
        override = settings.model_overrides[run_name].retirement
        for field, val in update.model_dump(exclude_unset=True).items():
            setattr(override, field, val)
        _persist_retirement_overrides(settings)
        return _per_model_response(run_name)

    @router.delete("/models/{run_name}/retirement")
    def delete_per_model(run_name: str, _: None = Depends(api_key_dep)):
        if run_name in settings.model_overrides:
            settings.model_overrides[run_name].retirement = ModelRetirementOverride()
            _persist_retirement_overrides(settings)
        return {"deleted": True, "run_name": run_name}

    @router.post("/models/{run_name}/unretire")
    def unretire_model(
        run_name: str,
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        repo = ModelPerformanceRepo(session)
        perf = repo.get(run_name)
        if perf is None:
            raise HTTPException(status_code=404, detail=f"No performance record for {run_name!r}")
        perf.retired = False
        perf.retired_at = None
        perf.first_trade_at = None
        perf.trade_count = 0
        perf.win_count = 0
        perf.rolling_pnl = 0.0
        perf.rolling_trades_json = "[]"
        repo.update(perf)
        return {"unretired": True, "run_name": run_name}

    return router
```

Note: `ModelPerformanceRepo` needs a `.get(model_id)` method that returns `None` if not found (distinct from `get_or_create`). Check `repos.py` — if it only has `get_or_create`, add:

```python
def get(self, model_id: str) -> ModelPerformance | None:
    return self._s.get(ModelPerformance, model_id)
```

- [ ] **Step 4: Update `alphaTrade/api/app.py`**

Add `settings` param to `create_app` and `start_api_server`, wire the retirement router, and add `PATCH` to CORS allowed methods:

```python
from alphaTrade.config import Settings

def create_app(engine: Engine, health_state: HealthState, registry=None, backtest_scheduler=None, settings: Settings = None) -> FastAPI:
    from alphaTrade.api.routers import positions, orders, signals, pnl, models, backtest, health, settings as settings_router, equity, trades, stream, kill_switch, verify, retirement

    app = FastAPI(title="alphaTrade API", version="1.0")

    @app.middleware("http")
    async def _log_requests(request: Request, call_next):
        t0 = time.perf_counter()
        response = await call_next(request)
        ms = (time.perf_counter() - t0) * 1000
        log.info("%s %s %d %.1fms", request.method, request.url.path, response.status_code, ms)
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "PUT", "POST", "DELETE", "PATCH", "OPTIONS"],
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
    app.include_router(settings_router.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(equity.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(trades.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(stream.make_router(engine, api_key_dep), prefix="/api/v1")
    app.include_router(kill_switch.make_router(api_key_dep), prefix="/api/v1")
    app.include_router(verify.make_router(api_key_dep), prefix="/api/v1")
    if settings is not None:
        app.include_router(retirement.make_router(session_dep, api_key_dep, settings), prefix="/api/v1")

    return app


async def start_api_server(
    engine: Engine,
    health_state: HealthState,
    port: int = 8081,
    registry=None,
    backtest_scheduler=None,
    settings: Settings = None,
) -> uvicorn.Server:
    app = create_app(engine, health_state, registry, backtest_scheduler, settings)
    config = uvicorn.Config(app, host="0.0.0.0", port=port, loop="none", log_level="info")
    server = uvicorn.Server(config)
    asyncio.create_task(server.serve())
    log.info("API server listening on :%d", port)
    return server
```

- [ ] **Step 5: Update `start_api_server` call in `alphaTrade/main.py`**

Find the `start_api_server` call (around line 797) and add `settings=settings`:

```python
api_server = await start_api_server(
    engine, health_state,
    port=settings.api_port,
    registry=registry,
    backtest_scheduler=backtest_scheduler,
    settings=settings,
)
```

- [ ] **Step 6: Add `retirement` to `alphaTrade/api/routers/__init__.py` if it exists**

Check if `__init__.py` exists and imports routers:

```bash
cat /home/preeth/projects/alphaTrade/alphaTrade/api/routers/__init__.py 2>/dev/null || echo "no init"
```

If it lists routers, add `retirement`. If it's empty or doesn't exist, no change needed — the import in `app.py` is inline.

- [ ] **Step 7: Run all retirement API tests**

```bash
python -m pytest tests/unit/test_retirement_api.py -v
```

Expected: all tests PASS.

- [ ] **Step 8: Run full test suite**

```bash
python -m pytest tests/unit/ -v --tb=short
```

Expected: all tests PASS.

- [ ] **Step 9: Commit**

```bash
git add alphaTrade/api/routers/retirement.py alphaTrade/api/app.py alphaTrade/main.py alphaTrade/store/repos.py tests/unit/test_retirement_api.py
git commit -m "feat(retirement): retirement router with global config, per-model overrides, and unretire endpoint"
```

---

## Self-Review Checklist

Run after all tasks are complete:

- [ ] `python -m pytest tests/unit/ -v` — all pass
- [ ] `python -m pytest tests/unit/test_retirement_config.py tests/unit/test_model_performance.py tests/unit/test_retirement_api.py tests/unit/test_oco_monitor.py -v` — all pass
- [ ] Smoke-test migration: `python -c "from alphaTrade.store.db import run_migrations, get_engine; import tempfile, pathlib; d=tempfile.mkdtemp(); run_migrations(pathlib.Path(d)/'t.db'); print('ok')"`
- [ ] Verify `PATCH` is in CORS allowed methods in `app.py`
- [ ] Verify `_parse_period` is exported from `performance.py` (not prefixed with double underscore)
- [ ] Verify `ModelPerformanceRepo.get()` exists and returns `None` for missing records
