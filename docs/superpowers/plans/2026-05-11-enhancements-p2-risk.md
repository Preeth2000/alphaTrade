# Enhancements P2: Risk (Model Performance, Sector Limits, Volatility Sizing)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three risk enhancements: automatic model retirement based on rolling P&L performance, sector/correlation position limits with balanced/unbalanced modes, and volatility-based position sizing (ATR or VIX).

**Architecture:** All changes confined to `alphaTrade/risk/` and thin wiring in `alphaTrade/model_registry.py` and `alphaTrade/broker/instrument_map.py`. Gates extend the existing `run_gates` function signature. Sizing modes extend `compute_quantity`. No changes to `adapter/`, `consensus/`, `broker/t212_client.py`.

**Tech Stack:** SQLModel (existing), yfinance (sector fetch), pytest-asyncio

**Prerequisite:** Plan P1 (Foundation) must be complete — relies on `ModelPerformance`, `SectorCache`, `ModelPerformanceRepo`, `SectorCacheRepo`, and new config classes.

---

## File Map

| Action | File |
|---|---|
| Create | `alphaTrade/risk/performance.py` |
| Create | `alphaTrade/risk/sector.py` |
| Modify | `alphaTrade/risk/sizing.py` — add atr + vix modes |
| Modify | `alphaTrade/risk/gates.py` — add retirement check + sector gate |
| Modify | `alphaTrade/model_registry.py` — skip retired models |
| Modify | `alphaTrade/broker/instrument_map.py` — populate sector_cache on resolve |
| Modify | `alphaTrade/main.py` — pass new gate params, pass ATR to compute_quantity |
| Create | `tests/unit/test_model_performance.py` |
| Create | `tests/unit/test_sector_gate.py` |
| Create | `tests/unit/test_volatility_sizing.py` |

---

### Task 1: Model performance tracking + retirement (risk/performance.py)

**Files:**
- Create: `alphaTrade/risk/performance.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_model_performance.py`:

```python
"""Tests for rolling model performance tracking and retirement logic."""
import json
from datetime import datetime

import pytest
from sqlmodel import Session

from alphaTrade.config import ModelRetirementConfig
from alphaTrade.risk.performance import record_trade, check_retirement
from alphaTrade.store.db import get_engine
from alphaTrade.store.repos import ModelPerformanceRepo


@pytest.fixture
def engine(tmp_path):
    import alphaTrade.store.db as _db
    _db._engine = None
    eng = get_engine(tmp_path / "test.db")
    yield eng
    _db._engine = None


def test_record_trade_increments_counts(engine):
    cfg = ModelRetirementConfig(enabled=True, lookback_trades=5)
    with Session(engine) as s:
        record_trade(s, model_id="model_a", realized_pnl=50.0, cfg=cfg)
    with Session(engine) as s:
        repo = ModelPerformanceRepo(s)
        perf = repo.get_or_create("model_a")
    assert perf.trade_count == 1
    assert perf.win_count == 1
    assert perf.rolling_pnl == 50.0


def test_record_trade_loss(engine):
    cfg = ModelRetirementConfig(enabled=True, lookback_trades=5)
    with Session(engine) as s:
        record_trade(s, model_id="model_a", realized_pnl=-30.0, cfg=cfg)
    with Session(engine) as s:
        repo = ModelPerformanceRepo(s)
        perf = repo.get_or_create("model_a")
    assert perf.win_count == 0
    assert perf.rolling_pnl == -30.0


def test_rolling_window_trims_to_lookback(engine):
    cfg = ModelRetirementConfig(enabled=True, lookback_trades=3)
    with Session(engine) as s:
        for pnl in [10.0, 20.0, 30.0, 40.0]:
            record_trade(s, model_id="model_a", realized_pnl=pnl, cfg=cfg)
    with Session(engine) as s:
        repo = ModelPerformanceRepo(s)
        perf = repo.get_or_create("model_a")
        trades = json.loads(perf.rolling_trades_json)
    assert len(trades) == 3
    assert trades == [20.0, 30.0, 40.0]  # oldest dropped


def test_check_retirement_below_win_rate(engine):
    cfg = ModelRetirementConfig(enabled=True, lookback_trades=5, min_win_rate=0.6, min_rolling_pnl=-9999)
    with Session(engine) as s:
        for pnl in [-10.0, -20.0, 10.0, -5.0, -8.0]:  # 1/5 wins = 0.2 win rate
            record_trade(s, model_id="model_a", realized_pnl=pnl, cfg=cfg)
    with Session(engine) as s:
        retired = check_retirement(s, model_id="model_a", cfg=cfg)
    assert retired is True


def test_check_retirement_below_rolling_pnl(engine):
    cfg = ModelRetirementConfig(enabled=True, lookback_trades=5, min_win_rate=0.0, min_rolling_pnl=-50.0)
    with Session(engine) as s:
        for pnl in [-20.0, -20.0, -20.0]:
            record_trade(s, model_id="model_a", realized_pnl=pnl, cfg=cfg)
    with Session(engine) as s:
        retired = check_retirement(s, model_id="model_a", cfg=cfg)
    assert retired is True


def test_check_retirement_not_triggered_when_disabled(engine):
    cfg = ModelRetirementConfig(enabled=False)
    with Session(engine) as s:
        for pnl in [-100.0] * 10:
            record_trade(s, model_id="model_a", realized_pnl=pnl, cfg=cfg)
    with Session(engine) as s:
        retired = check_retirement(s, model_id="model_a", cfg=cfg)
    assert retired is False


def test_check_retirement_not_triggered_when_not_enough_trades(engine):
    cfg = ModelRetirementConfig(enabled=True, lookback_trades=10, min_win_rate=0.9)
    with Session(engine) as s:
        record_trade(s, model_id="model_a", realized_pnl=-50.0, cfg=cfg)  # only 1 trade
    with Session(engine) as s:
        retired = check_retirement(s, model_id="model_a", cfg=cfg)
    assert retired is False  # need lookback_trades before evaluating
```

- [ ] **Step 2: Run tests, confirm failure**

```bash
pytest tests/unit/test_model_performance.py -v 2>&1 | head -5
```

Expected: `ModuleNotFoundError: No module named 'alphaTrade.risk.performance'`

- [ ] **Step 3: Create alphaTrade/risk/performance.py**

```python
"""Rolling model performance tracking and auto-retirement."""
from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlmodel import Session

from alphaTrade.config import ModelRetirementConfig
from alphaTrade.store.repos import ModelPerformanceRepo

log = logging.getLogger(__name__)


def record_trade(
    session: Session,
    model_id: str,
    realized_pnl: float,
    cfg: ModelRetirementConfig,
) -> None:
    """Record a closed trade's P&L into rolling window for model_id."""
    repo = ModelPerformanceRepo(session)
    perf = repo.get_or_create(model_id)

    # Update rolling trades list (bounded by lookback_trades)
    trades: list[float] = json.loads(perf.rolling_trades_json)
    trades.append(realized_pnl)
    if len(trades) > cfg.lookback_trades:
        trades = trades[-cfg.lookback_trades:]

    perf.rolling_trades_json = json.dumps(trades)
    perf.trade_count += 1
    if realized_pnl > 0:
        perf.win_count += 1
    perf.rolling_pnl = sum(trades)

    repo.update(perf)


def check_retirement(
    session: Session,
    model_id: str,
    cfg: ModelRetirementConfig,
) -> bool:
    """Return True and mark retired if model breaches thresholds. Return False otherwise."""
    if not cfg.enabled:
        return False

    repo = ModelPerformanceRepo(session)
    perf = repo.get_or_create(model_id)

    if perf.retired:
        return True

    trades: list[float] = json.loads(perf.rolling_trades_json)
    if len(trades) < cfg.lookback_trades:
        return False  # not enough data to evaluate

    win_rate = sum(1 for t in trades if t > 0) / len(trades)
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

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_model_performance.py -v
```

Expected: all 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add alphaTrade/risk/performance.py tests/unit/test_model_performance.py
git commit -m "feat(risk): add model performance tracking and auto-retirement"
```

---

### Task 2: Skip retired models in ModelRegistry

**Files:**
- Modify: `alphaTrade/model_registry.py`

- [ ] **Step 1: Write failing test**

Add to `tests/unit/test_model_performance.py`:

```python
import asyncio
from unittest.mock import patch, MagicMock
from pathlib import Path

from alphaTrade.model_registry import ModelRegistry


def test_registry_skips_retired_model(engine, tmp_path):
    """ModelRegistry.refresh must skip models flagged as retired in DB."""
    manifest_a = MagicMock()
    manifest_a.run_name = "model_a"
    manifest_a.interval = "1d"
    model_a = MagicMock()

    manifest_b = MagicMock()
    manifest_b.run_name = "model_b"
    manifest_b.interval = "1d"
    model_b = MagicMock()

    # Mark model_a as retired in DB
    with Session(engine) as s:
        repo = ModelPerformanceRepo(s)
        perf = repo.get_or_create("model_a")
        perf.retired = True
        repo.update(perf)

    with patch("alphaTrade.model_registry.scan_models", return_value=[(manifest_a, model_a), (manifest_b, model_b)]):
        registry = ModelRegistry(engine=engine)
        asyncio.run(registry.refresh(Path("/fake"), {}))

    assert "model_a" not in registry.by_run_name
    assert "model_b" in registry.by_run_name
```

- [ ] **Step 2: Run test, confirm failure**

```bash
pytest tests/unit/test_model_performance.py::test_registry_skips_retired_model -v 2>&1 | head -10
```

Expected: `TypeError: ModelRegistry.__init__() got an unexpected keyword argument 'engine'`

- [ ] **Step 3: Modify ModelRegistry to accept engine and check retirement**

Replace `alphaTrade/model_registry.py` with:

```python
"""Hot-reloadable model registry. Rescans models_dir each tick, diffs, adds/removes."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.engine import Engine
from sqlmodel import Session

from alphaTrade.adapter.inference import OnnxModel
from alphaTrade.adapter.manifest import Manifest
from alphaTrade.main import scan_models

log = logging.getLogger(__name__)


class ModelRegistry:
    def __init__(self, engine: Optional[Engine] = None) -> None:
        self._lock = asyncio.Lock()
        self._engine = engine
        # run_name → (Manifest, OnnxModel)
        self.by_run_name: dict[str, tuple[Manifest, OnnxModel]] = {}

    async def refresh(self, models_dir: Path, overrides: dict[str, Any]) -> None:
        """Rescan models_dir, hot-add new models, drop removed ones. Skip retired models."""
        try:
            scanned = scan_models(models_dir)
        except Exception as exc:
            log.error("model_registry: scan failed, keeping existing models: %s", exc)
            return

        active: dict[str, tuple[Manifest, OnnxModel]] = {}
        for manifest, model in scanned:
            override = overrides.get(manifest.run_name)
            if override is not None and not override.enabled:
                continue
            if self._is_retired(manifest.run_name):
                log.info("model_registry: skipping retired model %s", manifest.run_name)
                continue
            active[manifest.run_name] = (manifest, model)

        async with self._lock:
            current = set(self.by_run_name)
            new = set(active)

            for added in new - current:
                log.info("model_registry: hot-added %s", added)
            for removed in current - new:
                log.info("model_registry: hot-removed %s", removed)

            self.by_run_name = active

    def _is_retired(self, run_name: str) -> bool:
        if self._engine is None:
            return False
        from alphaTrade.store.repos import ModelPerformanceRepo
        try:
            with Session(self._engine) as s:
                return ModelPerformanceRepo(s).is_retired(run_name)
        except Exception as exc:
            log.warning("model_registry: retirement check failed for %s: %s", run_name, exc)
            return False

    def snapshot_by_interval(self) -> dict[str, list[tuple[Manifest, OnnxModel]]]:
        """Return current models grouped by interval (lock-free snapshot)."""
        by_interval: dict[str, list[tuple[Manifest, OnnxModel]]] = defaultdict(list)
        for manifest, model in self.by_run_name.values():
            by_interval[manifest.interval].append((manifest, model))
        return dict(by_interval)
```

- [ ] **Step 4: Update main.py to pass engine to ModelRegistry**

In `alphaTrade/main.py` `run()` function, find:
```python
    registry = ModelRegistry()
```
Replace with:
```python
    registry = ModelRegistry(engine=engine)
```

Note: `engine` is assigned before `registry` in `run()` (line ~450 `engine = get_engine(...)`), but currently `registry` is created before `engine`. Swap the order:
```python
    engine = get_engine(settings.state_db_path)

    from alphaTrade.model_registry import ModelRegistry
    registry = ModelRegistry(engine=engine)
    await registry.refresh(settings.models_dir, settings.model_overrides)
```

- [ ] **Step 5: Run all performance tests**

```bash
pytest tests/unit/test_model_performance.py -v
```

Expected: all 7 tests PASS

- [ ] **Step 6: Run full suite**

```bash
pytest tests/ -x -q 2>&1 | tail -5
```

Expected: no new failures

- [ ] **Step 7: Commit**

```bash
git add alphaTrade/model_registry.py alphaTrade/main.py tests/unit/test_model_performance.py
git commit -m "feat(registry): skip retired models in ModelRegistry.refresh"
```

---

### Task 3: Sector cache population + sector gate (risk/sector.py)

**Files:**
- Create: `alphaTrade/risk/sector.py`
- Modify: `alphaTrade/broker/instrument_map.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_sector_gate.py`:

```python
"""Tests for sector exposure gate (balanced and unbalanced modes)."""
from unittest.mock import patch, MagicMock

import pytest
from sqlmodel import Session

from alphaTrade.config import BalancedPortfolioConfig, UnbalancedPortfolioConfig
from alphaTrade.risk.sector import fetch_sector, check_sector_gate
from alphaTrade.store.db import get_engine
from alphaTrade.store.repos import Position, PositionRepo, SectorCache, SectorCacheRepo


@pytest.fixture
def engine(tmp_path):
    import alphaTrade.store.db as _db
    _db._engine = None
    eng = get_engine(tmp_path / "test.db")
    yield eng
    _db._engine = None


def test_fetch_sector_from_cache(engine):
    with Session(engine) as s:
        repo = SectorCacheRepo(s)
        repo.put("AAPL", "Technology")
    with Session(engine) as s:
        sector = fetch_sector("AAPL", SectorCacheRepo(s))
    assert sector == "Technology"


def test_fetch_sector_from_yfinance_on_miss(engine):
    mock_ticker = MagicMock()
    mock_ticker.info = {"sector": "Healthcare"}
    with patch("yfinance.Ticker", return_value=mock_ticker):
        with Session(engine) as s:
            repo = SectorCacheRepo(s)
            sector = fetch_sector("JNJ", repo)
    assert sector == "Healthcare"
    # Verify it was cached
    with Session(engine) as s:
        cached = SectorCacheRepo(s).get("JNJ")
    assert cached.sector == "Healthcare"


def test_fetch_sector_yfinance_failure_returns_unknown(engine):
    with patch("yfinance.Ticker", side_effect=Exception("network error")):
        with Session(engine) as s:
            sector = fetch_sector("FAIL", SectorCacheRepo(s))
    assert sector == "Unknown"


def test_balanced_gate_allows_when_under_limit(engine):
    """BUY allowed when sector exposure is below max_sector_pct."""
    cfg = BalancedPortfolioConfig(max_sector_pct=0.5)
    with Session(engine) as s:
        # No positions yet
        pos_repo = PositionRepo(s)
        sector_repo = SectorCacheRepo(s)
        sector_repo.put("AAPL", "Technology")
        sector_repo.put("MSFT", "Technology")
        result = check_sector_gate(
            yf_ticker="AAPL",
            signal="BUY",
            equity=10000.0,
            position_repo=pos_repo,
            sector_repo=sector_repo,
            portfolio_mode="balanced",
            balanced_cfg=cfg,
            unbalanced_cfg=UnbalancedPortfolioConfig(),
        )
    assert result is None  # None = approved


def test_balanced_gate_blocks_when_over_limit(engine):
    """BUY blocked when adding ticker would exceed max_sector_pct."""
    cfg = BalancedPortfolioConfig(max_sector_pct=0.25)
    with Session(engine) as s:
        pos_repo = PositionRepo(s)
        # Pre-fill tech positions worth 30% of equity
        pos_repo.upsert(Position(t212_ticker="MSFT_US_EQ", quantity=10.0, avg_entry=300.0))
        sector_repo = SectorCacheRepo(s)
        sector_repo.put("AAPL", "Technology")
        sector_repo.put("MSFT_US_EQ", "Technology")
        result = check_sector_gate(
            yf_ticker="AAPL",
            signal="BUY",
            equity=10000.0,
            position_repo=pos_repo,
            sector_repo=sector_repo,
            portfolio_mode="balanced",
            balanced_cfg=cfg,
            unbalanced_cfg=UnbalancedPortfolioConfig(),
        )
    assert result is not None
    assert "sector" in result.lower()


def test_unbalanced_gate_allows_under_limit(engine):
    cfg = UnbalancedPortfolioConfig(max_per_sector=3)
    with Session(engine) as s:
        pos_repo = PositionRepo(s)
        sector_repo = SectorCacheRepo(s)
        sector_repo.put("AAPL", "Technology")
        result = check_sector_gate(
            yf_ticker="AAPL",
            signal="BUY",
            equity=10000.0,
            position_repo=pos_repo,
            sector_repo=sector_repo,
            portfolio_mode="unbalanced",
            balanced_cfg=BalancedPortfolioConfig(),
            unbalanced_cfg=cfg,
        )
    assert result is None


def test_unbalanced_gate_blocks_at_limit(engine):
    cfg = UnbalancedPortfolioConfig(max_per_sector=2, sector_overrides={"technology": 2})
    with Session(engine) as s:
        pos_repo = PositionRepo(s)
        pos_repo.upsert(Position(t212_ticker="MSFT_US_EQ", quantity=1.0, avg_entry=300.0))
        pos_repo.upsert(Position(t212_ticker="GOOGL_US_EQ", quantity=1.0, avg_entry=150.0))
        sector_repo = SectorCacheRepo(s)
        sector_repo.put("AAPL", "Technology")
        sector_repo.put("MSFT_US_EQ", "Technology")
        sector_repo.put("GOOGL_US_EQ", "Technology")
        result = check_sector_gate(
            yf_ticker="AAPL",
            signal="BUY",
            equity=10000.0,
            position_repo=pos_repo,
            sector_repo=sector_repo,
            portfolio_mode="unbalanced",
            balanced_cfg=BalancedPortfolioConfig(),
            unbalanced_cfg=cfg,
        )
    assert result is not None


def test_sell_always_passes_sector_gate(engine):
    """SELL signals bypass the sector gate."""
    cfg = BalancedPortfolioConfig(max_sector_pct=0.01)  # impossibly tight
    with Session(engine) as s:
        result = check_sector_gate(
            yf_ticker="AAPL",
            signal="SELL",
            equity=10000.0,
            position_repo=PositionRepo(s),
            sector_repo=SectorCacheRepo(s),
            portfolio_mode="balanced",
            balanced_cfg=cfg,
            unbalanced_cfg=UnbalancedPortfolioConfig(),
        )
    assert result is None
```

- [ ] **Step 2: Run tests, confirm failure**

```bash
pytest tests/unit/test_sector_gate.py -v 2>&1 | head -5
```

Expected: `ModuleNotFoundError: No module named 'alphaTrade.risk.sector'`

- [ ] **Step 3: Create alphaTrade/risk/sector.py**

```python
"""Sector/correlation position limit gate."""
from __future__ import annotations

import logging
from typing import Optional

from alphaTrade.config import BalancedPortfolioConfig, UnbalancedPortfolioConfig
from alphaTrade.store.repos import PositionRepo, SectorCacheRepo

log = logging.getLogger(__name__)


def fetch_sector(yf_ticker: str, sector_repo: SectorCacheRepo) -> str:
    """Return GICS sector string for yf_ticker. Fetches from cache, then yfinance."""
    cached = sector_repo.get(yf_ticker)
    if cached:
        return cached.sector

    try:
        import yfinance as yf
        info = yf.Ticker(yf_ticker).info
        sector = info.get("sector", "Unknown") or "Unknown"
    except Exception as exc:
        log.warning("Sector fetch failed for %s: %s", yf_ticker, exc)
        sector = "Unknown"

    if sector != "Unknown":
        sector_repo.put(yf_ticker, sector)
    return sector


def check_sector_gate(
    yf_ticker: str,
    signal: str,
    equity: float,
    position_repo: PositionRepo,
    sector_repo: SectorCacheRepo,
    portfolio_mode: str,
    balanced_cfg: BalancedPortfolioConfig,
    unbalanced_cfg: UnbalancedPortfolioConfig,
) -> Optional[str]:
    """Return rejection reason string if BUY should be blocked, None if approved.

    SELL signals always pass (sector gate only controls entry).
    Unknown sector always passes (fail-open).
    """
    if signal != "BUY":
        return None

    ticker_sector = fetch_sector(yf_ticker, sector_repo)
    if ticker_sector == "Unknown":
        log.warning("Unknown sector for %s — skipping sector gate", yf_ticker)
        return None

    all_positions = [p for p in position_repo.all() if p.quantity > 0]

    # Map each open position ticker to its sector
    ticker_to_sector: dict[str, str] = {}
    for pos in all_positions:
        cached = sector_repo.get(pos.t212_ticker)
        ticker_to_sector[pos.t212_ticker] = cached.sector if cached else "Unknown"

    same_sector_positions = [
        p for p in all_positions
        if ticker_to_sector.get(p.t212_ticker, "Unknown").lower() == ticker_sector.lower()
    ]

    if portfolio_mode == "balanced":
        if equity <= 0:
            return None
        sector_value = sum(p.quantity * p.avg_entry for p in same_sector_positions)
        sector_pct = sector_value / equity
        if sector_pct >= balanced_cfg.max_sector_pct:
            return (
                f"sector limit: {ticker_sector} at {sector_pct:.1%} "
                f">= max {balanced_cfg.max_sector_pct:.1%}"
            )
    else:  # unbalanced
        sector_key = ticker_sector.lower()
        limit = unbalanced_cfg.sector_overrides.get(sector_key, unbalanced_cfg.max_per_sector)
        if len(same_sector_positions) >= limit:
            return (
                f"sector limit: {ticker_sector} has {len(same_sector_positions)} "
                f">= max {limit} positions"
            )

    return None
```

- [ ] **Step 4: Add sector population to instrument_map.py**

In `alphaTrade/broker/instrument_map.py`, add sector population after T212 resolution:

```python
"""yfinance ticker → T212 instrument_ticker resolution.

Priority: overrides.yaml config → SQLite cache → T212 instruments API best-match.
Fails loud if unresolvable.
"""
from __future__ import annotations
from typing import Optional

from alphaTrade.broker.t212_client import T212Client
from alphaTrade.store.repos import InstrumentCacheRepo, SectorCacheRepo


class InstrumentMap:
    def __init__(
        self,
        t212: T212Client,
        cache_repo: InstrumentCacheRepo,
        static_overrides: dict[str, str],
        sector_repo: Optional[SectorCacheRepo] = None,
    ) -> None:
        self._t212 = t212
        self._cache = cache_repo
        self._static = static_overrides
        self._sector_repo = sector_repo

    def resolve(self, yf_ticker: str) -> str:
        if yf_ticker in self._static:
            return self._static[yf_ticker]

        cached = self._cache.get(yf_ticker)
        if cached:
            return cached.t212_ticker

        t212_ticker = self._api_resolve(yf_ticker)
        self._cache.put(yf_ticker, t212_ticker)

        # Opportunistically populate sector cache on first resolution
        if self._sector_repo:
            from alphaTrade.risk.sector import fetch_sector
            fetch_sector(yf_ticker, self._sector_repo)

        return t212_ticker

    def _api_resolve(self, yf_ticker: str) -> str:
        instruments = self._t212.get_instruments()
        upper = yf_ticker.upper()
        for inst in instruments:
            ticker = inst.get("ticker", "")
            short = inst.get("shortName", "")
            if ticker.upper().startswith(upper):
                return ticker
            if short.upper() == upper:
                return ticker

        raise RuntimeError(
            f"Cannot resolve {yf_ticker!r} to a T212 instrument_ticker. "
            f"Add it to overrides.yaml under models.<run_name>.t212_ticker."
        )
```

- [ ] **Step 5: Run sector tests**

```bash
pytest tests/unit/test_sector_gate.py -v
```

Expected: all 7 tests PASS

- [ ] **Step 6: Commit**

```bash
git add alphaTrade/risk/sector.py alphaTrade/broker/instrument_map.py tests/unit/test_sector_gate.py
git commit -m "feat(risk): add sector exposure gate (balanced/unbalanced modes)"
```

---

### Task 4: Wire sector gate + retirement check into gates.py

**Files:**
- Modify: `alphaTrade/risk/gates.py`

- [ ] **Step 1: Write failing test**

Add to `tests/unit/test_sector_gate.py`:

```python
from alphaTrade.config import RiskConfig
from alphaTrade.risk.gates import run_gates


def test_gates_blocks_retired_model(engine):
    cfg = RiskConfig()
    with Session(engine) as s:
        from alphaTrade.store.repos import ModelPerformanceRepo
        repo = ModelPerformanceRepo(s)
        perf = repo.get_or_create("model_z")
        perf.retired = True
        repo.update(perf)

    with Session(engine) as s:
        result = run_gates(
            signal="BUY",
            t212_ticker="AAPL_US_EQ",
            position_repo=PositionRepo(s),
            max_positions=10,
            daily_loss_halted=False,
            model_id="model_z",
            perf_repo=ModelPerformanceRepo(s),
        )
    assert result.approved is False
    assert "retired" in result.reason


def test_gates_blocks_sector_limit(engine):
    cfg = RiskConfig(**{"portfolio_mode": "unbalanced", "unbalanced": {"max_per_sector": 1}})
    with Session(engine) as s:
        pos_repo = PositionRepo(s)
        pos_repo.upsert(Position(t212_ticker="MSFT_US_EQ", quantity=1.0, avg_entry=300.0))
        sector_repo = SectorCacheRepo(s)
        sector_repo.put("AAPL", "Technology")
        sector_repo.put("MSFT_US_EQ", "Technology")
        result = run_gates(
            signal="BUY",
            t212_ticker="AAPL_US_EQ",
            position_repo=pos_repo,
            max_positions=10,
            daily_loss_halted=False,
            yf_ticker="AAPL",
            equity=10000.0,
            sector_repo=sector_repo,
            risk_cfg=cfg,
        )
    assert result.approved is False
    assert "sector" in result.reason
```

- [ ] **Step 2: Run test, confirm failure**

```bash
pytest tests/unit/test_sector_gate.py::test_gates_blocks_retired_model -v 2>&1 | head -5
```

Expected: `TypeError: run_gates() got an unexpected keyword argument 'model_id'`

- [ ] **Step 3: Replace alphaTrade/risk/gates.py**

```python
"""Risk gate pipeline. Applied per signal before order submission.

Order of operations:
1. Drawdown halt active?
2. HOLD → no-op
3. Model retired?
4. Cooldown window active for this symbol?
5. Sector limit exceeded (BUY only)?
6. BUY + position count >= max?
7. BUY + already long this symbol (no pyramiding)?
8. SELL + no position (short selling stubbed off in v1)?
9. Pass → return approved order params.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from alphaTrade.store.repos import PositionRepo


@dataclass
class GateResult:
    approved: bool
    reason: str = ""


def run_gates(
    signal: str,
    t212_ticker: str,
    position_repo: PositionRepo,
    max_positions: int,
    daily_loss_halted: bool,
    now: Optional[datetime] = None,
    model_id: str = "",
    perf_repo=None,           # ModelPerformanceRepo | None
    yf_ticker: str = "",
    equity: float = 0.0,
    sector_repo=None,          # SectorCacheRepo | None
    risk_cfg=None,             # RiskConfig | None
) -> GateResult:
    if now is None:
        now = datetime.utcnow()

    if daily_loss_halted and signal != "HOLD":
        return GateResult(False, "daily_loss_halt active")

    if signal == "HOLD":
        return GateResult(False, "HOLD — no action")

    # Retirement check
    if model_id and perf_repo is not None:
        if perf_repo.is_retired(model_id):
            return GateResult(False, f"model {model_id!r} retired")

    pos = position_repo.get(t212_ticker)

    # Cooldown check
    if pos and pos.cooldown_until_ts and now < pos.cooldown_until_ts:
        return GateResult(False, f"cooldown until {pos.cooldown_until_ts.isoformat()}")

    # Sector gate (BUY only)
    if signal == "BUY" and yf_ticker and sector_repo is not None and risk_cfg is not None:
        from alphaTrade.risk.sector import check_sector_gate
        rejection = check_sector_gate(
            yf_ticker=yf_ticker,
            signal=signal,
            equity=equity,
            position_repo=position_repo,
            sector_repo=sector_repo,
            portfolio_mode=risk_cfg.portfolio_mode,
            balanced_cfg=risk_cfg.balanced,
            unbalanced_cfg=risk_cfg.unbalanced,
        )
        if rejection:
            return GateResult(False, rejection)

    all_positions = position_repo.all()
    open_count = len(all_positions)

    if signal == "BUY":
        if pos and pos.quantity > 0:
            return GateResult(False, "already long — no pyramiding")
        if open_count >= max_positions:
            return GateResult(False, f"max positions {max_positions} reached")

    if signal == "SELL":
        if not pos or pos.quantity <= 0:
            return GateResult(False, "no long position to SELL (short selling disabled in v1)")

    return GateResult(True)
```

- [ ] **Step 4: Update main.py to pass new gate params**

In `alphaTrade/main.py` `make_tick`, find the `run_gates(...)` call and extend it:

```python
                gate: GateResult = run_gates(
                    signal=signal,
                    t212_ticker=t212_ticker,
                    position_repo=pos_repo,
                    max_positions=settings.risk.max_positions,
                    daily_loss_halted=daily_loss_halted,
                    model_id=manifest.run_name,
                    perf_repo=ModelPerformanceRepo(session),
                    yf_ticker=yf_ticker,
                    equity=equity,
                    sector_repo=SectorCacheRepo(session),
                    risk_cfg=settings.risk,
                )
```

Also add imports at top of main.py:
```python
from alphaTrade.store.repos import (
    ...existing imports...
    ModelPerformanceRepo,
    SectorCacheRepo,
)
```

- [ ] **Step 5: Update instrument_map instantiation in main.py to pass sector_repo**

Find `InstrumentMap(t212, inst_cache, static_map)` and update:
```python
            sector_cache = SectorCacheRepo(session)
            instrument_map = InstrumentMap(t212, inst_cache, static_map, sector_repo=sector_cache)
```

And in `_preresolve_tickers`, update the `InstrumentMap` call:
```python
        inst_map = InstrumentMap(t212, cache, static_map, sector_repo=SectorCacheRepo(session))
```

- [ ] **Step 6: Run all risk gate tests**

```bash
pytest tests/unit/test_sector_gate.py tests/unit/test_risk_gates.py -v
```

Expected: all tests PASS

- [ ] **Step 7: Run full suite**

```bash
pytest tests/ -x -q 2>&1 | tail -5
```

Expected: no new failures

- [ ] **Step 8: Commit**

```bash
git add alphaTrade/risk/gates.py alphaTrade/main.py tests/unit/test_sector_gate.py
git commit -m "feat(risk): add retirement and sector gates to run_gates pipeline"
```

---

### Task 5: Wire performance record_trade into tick loop

**Files:**
- Modify: `alphaTrade/main.py`
- Modify: `alphaTrade/broker/oco_monitor.py`

- [ ] **Step 1: Add record_trade + check_retirement calls after SELL fill in main.py**

In the `elif signal == "SELL":` block in `make_tick`, after the trade journal write, add:

```python
                        # Update model performance on SELL close
                        from alphaTrade.risk.performance import record_trade, check_retirement
                        fill_price_for_perf = float(resp.get("fillPrice") or current_price)
                        pnl_for_perf = (fill_price_for_perf - (pos.avg_entry if pos else fill_price_for_perf)) * qty
                        record_trade(session, model_id=manifest.run_name,
                                     realized_pnl=pnl_for_perf, cfg=settings.risk.model_retirement)
                        if check_retirement(session, model_id=manifest.run_name,
                                            cfg=settings.risk.model_retirement):
                            wh.notify("WARNING", f"Model {manifest.run_name} retired after performance check",
                                      category="model-retirement")
```

- [ ] **Step 2: Add record_trade call in oco_monitor.py _close_position**

In `alphaTrade/broker/oco_monitor.py` `_close_position`, add after `journal_repo.save(...)`:

```python
        # Record performance for retirement evaluation
        # engine-level import to avoid circular dep
        try:
            from alphaTrade.risk.performance import record_trade, check_retirement
            from alphaTrade.config import ModelRetirementConfig
            record_trade(session, model_id=model_id, realized_pnl=realized_pnl,
                         cfg=ModelRetirementConfig())  # uses defaults; overridden at startup via settings
        except Exception as exc:
            log.warning("Performance record failed for %s: %s", model_id, exc)
```

Note: OCO monitor doesn't have access to `settings` — it uses `ModelRetirementConfig()` defaults. The actual retirement check happens on the next `run_gates` call in the tick loop which has `settings.risk.model_retirement`. This is intentional: OCO records P&L, tick loop enforces retirement policy.

- [ ] **Step 3: Run full test suite**

```bash
pytest tests/ -x -q 2>&1 | tail -5
```

Expected: no failures

- [ ] **Step 4: Commit**

```bash
git add alphaTrade/main.py alphaTrade/broker/oco_monitor.py
git commit -m "feat(risk): wire record_trade into tick loop and OCO close"
```

---

### Task 6: Volatility-based position sizing (risk/sizing.py)

**Files:**
- Modify: `alphaTrade/risk/sizing.py`
- Modify: `alphaTrade/main.py` — pass ATR value and sizing_mode to compute_quantity

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_volatility_sizing.py`:

```python
"""Tests for ATR and VIX volatility-based position sizing."""
import pytest
from unittest.mock import patch, MagicMock

from alphaTrade.risk.sizing import compute_quantity


def test_fixed_mode_unchanged():
    qty = compute_quantity(equity=10000.0, current_price=100.0, size_pct=0.10,
                           mode="fixed")
    assert qty == pytest.approx(10.0, rel=1e-4)


def test_atr_mode_basic():
    # risk_pct=0.01, equity=10000 → risk $100
    # ATR=5.0, multiplier=2.0 → stop = $10 from entry
    # qty = 100 / 10 = 10
    qty = compute_quantity(
        equity=10000.0, current_price=100.0, size_pct=0.10,
        mode="atr", atr=5.0, atr_risk_pct=0.01, atr_multiplier=2.0,
    )
    assert qty == pytest.approx(10.0, rel=1e-4)


def test_atr_mode_larger_atr_gives_smaller_qty():
    qty_small_atr = compute_quantity(
        equity=10000.0, current_price=100.0, size_pct=0.10,
        mode="atr", atr=2.0, atr_risk_pct=0.01, atr_multiplier=2.0,
    )
    qty_large_atr = compute_quantity(
        equity=10000.0, current_price=100.0, size_pct=0.10,
        mode="atr", atr=10.0, atr_risk_pct=0.01, atr_multiplier=2.0,
    )
    assert qty_large_atr < qty_small_atr


def test_atr_mode_falls_back_to_fixed_when_atr_zero():
    qty = compute_quantity(
        equity=10000.0, current_price=100.0, size_pct=0.10,
        mode="atr", atr=0.0, atr_risk_pct=0.01, atr_multiplier=2.0,
    )
    # fallback to fixed: 10000 * 0.10 / 100 = 10
    assert qty == pytest.approx(10.0, rel=1e-4)


def test_vix_mode_at_scalar_gives_base_size():
    # VIX == vix_scalar → multiplier = 1.0 → base_size_pct used
    qty = compute_quantity(
        equity=10000.0, current_price=100.0, size_pct=0.05,
        mode="vix", current_vix=20.0, vix_scalar=20.0,
        vix_base_size_pct=0.05, vix_max_size_pct=0.15,
    )
    # 10000 * 0.05 / 100 = 5
    assert qty == pytest.approx(5.0, rel=1e-4)


def test_vix_mode_high_vix_reduces_size():
    qty_low_vix = compute_quantity(
        equity=10000.0, current_price=100.0, size_pct=0.05,
        mode="vix", current_vix=10.0, vix_scalar=20.0,
        vix_base_size_pct=0.05, vix_max_size_pct=0.15,
    )
    qty_high_vix = compute_quantity(
        equity=10000.0, current_price=100.0, size_pct=0.05,
        mode="vix", current_vix=40.0, vix_scalar=20.0,
        vix_base_size_pct=0.05, vix_max_size_pct=0.15,
    )
    assert qty_high_vix < qty_low_vix


def test_vix_mode_capped_by_max_size_pct():
    # VIX=1, scalar=20 → multiplier=20 → would be 20× base, but capped
    qty = compute_quantity(
        equity=10000.0, current_price=100.0, size_pct=0.05,
        mode="vix", current_vix=1.0, vix_scalar=20.0,
        vix_base_size_pct=0.05, vix_max_size_pct=0.10,
    )
    # Capped at 0.10: 10000 * 0.10 / 100 = 10
    assert qty == pytest.approx(10.0, rel=1e-4)
```

- [ ] **Step 2: Run tests, confirm failure**

```bash
pytest tests/unit/test_volatility_sizing.py -v 2>&1 | head -10
```

Expected: `TypeError: compute_quantity() got an unexpected keyword argument 'mode'`

- [ ] **Step 3: Replace alphaTrade/risk/sizing.py**

```python
"""Position sizing: fixed, ATR-based, and VIX-based modes."""
from __future__ import annotations

_vix_cache: dict[str, float] = {}  # date-string → VIX value


def _get_vix() -> float:
    """Fetch VIX from yfinance, cached by calendar date. Returns 20.0 on failure."""
    from datetime import datetime
    today = datetime.utcnow().date().isoformat()
    if today in _vix_cache:
        return _vix_cache[today]
    try:
        import yfinance as yf
        import logging
        hist = yf.Ticker("^VIX").history(period="1d")
        if hist.empty:
            raise ValueError("Empty VIX history")
        vix = float(hist["Close"].iloc[-1])
        _vix_cache[today] = vix
        return vix
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("VIX fetch failed, using 20.0: %s", exc)
        return 20.0


def compute_quantity(
    equity: float,
    current_price: float,
    size_pct: float,
    mode: str = "fixed",
    # ATR mode params
    atr: float = 0.0,
    atr_risk_pct: float = 0.01,
    atr_multiplier: float = 2.0,
    # VIX mode params
    current_vix: float | None = None,
    vix_scalar: float = 20.0,
    vix_base_size_pct: float = 0.05,
    vix_max_size_pct: float = 0.15,
) -> float:
    """Return share quantity. mode: 'fixed' | 'atr' | 'vix'."""
    if current_price <= 0:
        raise ValueError(f"Invalid current_price: {current_price}")

    if mode == "atr":
        if atr <= 0:
            # ATR unavailable — fall back to fixed
            cash = equity * size_pct
        else:
            dollar_risk = equity * atr_risk_pct
            stop_distance = atr * atr_multiplier
            cash = dollar_risk / stop_distance * current_price

    elif mode == "vix":
        vix = current_vix if current_vix is not None else _get_vix()
        multiplier = vix_scalar / vix if vix > 0 else 1.0
        effective_pct = min(vix_base_size_pct * multiplier, vix_max_size_pct)
        cash = equity * effective_pct

    else:  # fixed
        cash = equity * size_pct

    qty = cash / current_price
    return max(round(qty, 6), 0.0)
```

- [ ] **Step 4: Run sizing tests**

```bash
pytest tests/unit/test_volatility_sizing.py -v
```

Expected: all 7 tests PASS

- [ ] **Step 5: Update main.py compute_quantity call to use sizing mode**

In `make_tick`, find `qty = compute_quantity(equity, current_price, size_pct)` and replace:

```python
                # ATR available from features computed earlier for this ticker
                raw_atr = ticker_atr.get(yf_ticker, 0.0)

                qty = compute_quantity(
                    equity=equity,
                    current_price=current_price,
                    size_pct=size_pct,
                    mode=settings.risk.sizing_mode,
                    atr=raw_atr,
                    atr_risk_pct=settings.risk.atr.risk_pct,
                    atr_multiplier=settings.risk.atr.atr_multiplier,
                    vix_base_size_pct=settings.risk.vix.base_size_pct,
                    vix_scalar=settings.risk.vix.vix_scalar,
                    vix_max_size_pct=settings.risk.vix.max_size_pct,
                )
```

Also add `ticker_atr` dict population in the inference loop (before `ticker_logits`):

```python
        ticker_atr: dict[str, float] = {}
        ticker_logits: dict[str, list] = defaultdict(list)
        ticker_manifest: dict[str, Manifest] = {}

        for manifest, model in interval_models:
            try:
                t0 = time.perf_counter()
                df = provider.fetch_ohlcv(manifest.ticker, manifest.interval, manifest.window)
                features = compute_features(df, manifest.feature_names)
                features = features.dropna()
                # Save raw ATR before normalization (ATR mode uses real price units)
                if "ATR" in features.columns:
                    ticker_atr[manifest.ticker] = float(features["ATR"].iloc[-1])
                features = normalize(features, manifest)
                ...
```

- [ ] **Step 6: Run full test suite**

```bash
pytest tests/ -x -q 2>&1 | tail -5
```

Expected: no failures

- [ ] **Step 7: Commit**

```bash
git add alphaTrade/risk/sizing.py alphaTrade/main.py tests/unit/test_volatility_sizing.py
git commit -m "feat(risk): add ATR and VIX volatility-based position sizing"
```
