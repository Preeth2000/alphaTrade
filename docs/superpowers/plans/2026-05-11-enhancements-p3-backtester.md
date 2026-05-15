# Enhancements P3: Backtester (Dry-Run Simulation Engine)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bar-by-bar dry-run backtester that reuses the production adapter/consensus pipeline unchanged, simulates fills at next-bar open, checks SL/TP as price levels, and writes results to `backtest_run` + `backtest_trade` DB tables. Expose via `alphalink backtest` CLI subcommand.

**Architecture:** New `alphalink/backtest/` package. Engine fetches full OHLCV history, walks bars forward (no lookahead), runs identical features→normalize→window→ONNX→consensus chain. Fills are simulated at next-bar open ± slippage. No paper orders sent to T212.

**Tech Stack:** yfinance (historical data), onnxruntime (existing), SQLModel (existing), pytest

**Prerequisite:** Plan P1 (Foundation) must be complete — relies on `BacktestRun`, `BacktestTrade`, `BacktestRepo`, and `BacktestConfig` from that plan.

---

## File Map

| Action | File |
|---|---|
| Create | `alphalink/backtest/__init__.py` |
| Create | `alphalink/backtest/engine.py` |
| Create | `alphalink/backtest/reporter.py` |
| Modify | `alphalink/cli.py` — add `backtest` subcommand |
| Create | `tests/unit/test_backtest_engine.py` |
| Create | `tests/integration/test_backtest_integration.py` |

---

### Task 1: Backtest engine (alphalink/backtest/engine.py)

**Files:**
- Create: `alphalink/backtest/__init__.py` (empty)
- Create: `alphalink/backtest/engine.py`

- [ ] **Step 1: Write failing unit tests**

Create `tests/unit/test_backtest_engine.py`:

```python
"""Unit tests for backtest engine core logic."""
import pandas as pd
import pytest
from unittest.mock import MagicMock, patch

from alphalink.backtest.engine import _simulate_fill, _check_sl_tp, BacktestState


def test_simulate_fill_buy_adds_slippage():
    """Fill price for BUY = open * (1 + slippage_bps/10000)."""
    price = _simulate_fill("BUY", open_price=100.0, slippage_bps=10)
    assert abs(price - 100.10) < 1e-9


def test_simulate_fill_sell_subtracts_slippage():
    price = _simulate_fill("SELL", open_price=100.0, slippage_bps=10)
    assert abs(price - 99.90) < 1e-9


def test_check_sl_tp_no_position():
    """No position → nothing triggered."""
    result = _check_sl_tp(state=None, high=110.0, low=90.0)
    assert result is None


def test_check_sl_tp_sl_hit():
    state = BacktestState(
        side="BUY", entry_price=100.0, quantity=10.0,
        sl_price=95.0, tp_price=115.0, entry_bar=0, model_id="m1"
    )
    result = _check_sl_tp(state=state, high=110.0, low=93.0)
    assert result == ("SL", 95.0)


def test_check_sl_tp_tp_hit():
    state = BacktestState(
        side="BUY", entry_price=100.0, quantity=10.0,
        sl_price=95.0, tp_price=115.0, entry_bar=0, model_id="m1"
    )
    result = _check_sl_tp(state=state, high=116.0, low=98.0)
    assert result == ("TP", 115.0)


def test_check_sl_tp_no_hit():
    state = BacktestState(
        side="BUY", entry_price=100.0, quantity=10.0,
        sl_price=95.0, tp_price=115.0, entry_bar=0, model_id="m1"
    )
    result = _check_sl_tp(state=state, high=110.0, low=98.0)
    assert result is None


def test_backtest_state_pnl_long():
    state = BacktestState(
        side="BUY", entry_price=100.0, quantity=5.0,
        sl_price=95.0, tp_price=115.0, entry_bar=0, model_id="m1"
    )
    assert state.pnl(exit_price=110.0) == pytest.approx(50.0)


def test_backtest_state_pnl_short():
    state = BacktestState(
        side="SELL", entry_price=100.0, quantity=5.0,
        sl_price=105.0, tp_price=85.0, entry_bar=0, model_id="m1"
    )
    assert state.pnl(exit_price=90.0) == pytest.approx(50.0)
```

- [ ] **Step 2: Write the engine module**

Create `alphalink/backtest/__init__.py` (empty file).

Create `alphalink/backtest/engine.py`:

```python
"""Bar-by-bar backtester. Reuses production pipeline; no T212 calls."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlmodel import Session

from alphalink.adapter.features import compute_features
from alphalink.adapter.inference import OnnxModel
from alphalink.adapter.manifest import Manifest
from alphalink.adapter.normalize import normalize
from alphalink.adapter.window import build_input
from alphalink.config import BacktestConfig
from alphalink.consensus.softmax_avg import CLASS_NAMES, softmax_vote
from alphalink.data.yfinance_provider import YFinanceProvider
from alphalink.main import scan_models
from alphalink.store.repos import BacktestRepo

log = logging.getLogger(__name__)


@dataclass
class BacktestState:
    """Open simulated position."""
    side: str          # "BUY" | "SELL"
    entry_price: float
    quantity: float
    sl_price: float | None
    tp_price: float | None
    entry_bar: int
    model_id: str

    def pnl(self, exit_price: float) -> float:
        direction = 1.0 if self.side == "BUY" else -1.0
        return direction * (exit_price - self.entry_price) * self.quantity


def _simulate_fill(side: str, open_price: float, slippage_bps: int) -> float:
    slip = slippage_bps / 10_000
    return open_price * (1 + slip) if side == "BUY" else open_price * (1 - slip)


def _check_sl_tp(
    state: BacktestState | None,
    high: float,
    low: float,
) -> tuple[str, float] | None:
    """Return ("SL"|"TP", exit_price) if triggered, else None."""
    if state is None:
        return None
    if state.sl_price is not None:
        if (state.side == "BUY" and low <= state.sl_price) or \
           (state.side == "SELL" and high >= state.sl_price):
            return ("SL", state.sl_price)
    if state.tp_price is not None:
        if (state.side == "BUY" and high >= state.tp_price) or \
           (state.side == "SELL" and low <= state.tp_price):
            return ("TP", state.tp_price)
    return None


def run_backtest(
    session: Session,
    models_dir: Path,
    start: str,
    end: str,
    cfg: BacktestConfig,
) -> dict[str, Any]:
    """Run backtest for all models in models_dir. Returns summary dict."""
    models = scan_models(models_dir)
    if not models:
        raise RuntimeError(f"No models found in {models_dir}")

    provider = YFinanceProvider()
    repo = BacktestRepo(session)
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


def _run_single_model(
    manifest: Manifest,
    model: OnnxModel,
    provider: YFinanceProvider,
    start: str,
    end: str,
    cfg: BacktestConfig,
) -> list[dict]:
    """Walk forward bar-by-bar for one model. No lookahead."""
    # Fetch enough history for feature computation + window warm-up
    warmup_bars = manifest.window + 50
    df = provider.fetch_ohlcv_range(manifest.ticker, manifest.interval, start=start, end=end, extra_bars=warmup_bars)
    if df is None or len(df) < manifest.window + 2:
        log.warning("backtest: not enough data for %s", manifest.run_name)
        return []

    trades: list[dict] = []
    state: BacktestState | None = None
    equity = cfg.initial_equity

    # Walk bar index from warm-up point to end-1 (we need bar+1 for fill price)
    for i in range(warmup_bars, len(df) - 1):
        bar = df.iloc[i]
        next_bar = df.iloc[i + 1]

        # Check SL/TP on current bar's range before generating new signal
        if state is not None:
            hit = _check_sl_tp(state, high=float(bar["High"]), low=float(bar["Low"]))
            if hit is not None:
                reason, exit_price = hit
                realized = state.pnl(exit_price) - cfg.commission_per_trade
                equity += realized
                trades.append(_build_trade(
                    state=state, exit_price=exit_price, exit_bar=i,
                    exit_time=bar.name, realized_pnl=realized, exit_reason=reason,
                    model_id=manifest.run_name,
                ))
                state = None

        # Generate signal from bars[0..i] window
        window_df = df.iloc[max(0, i - warmup_bars):i + 1]
        signal = _infer(manifest, model, window_df)

        if signal in ("BUY", "SELL") and state is None:
            fill_price = _simulate_fill(signal, float(next_bar["Open"]), cfg.slippage_bps)
            size_pct = cfg.default_size_pct
            quantity = (equity * size_pct) / fill_price

            sl_price: float | None = None
            tp_price: float | None = None
            if cfg.sl_pct is not None:
                direction = 1 if signal == "BUY" else -1
                sl_price = fill_price * (1 - direction * cfg.sl_pct / 100)
            if cfg.tp_pct is not None:
                direction = 1 if signal == "BUY" else -1
                tp_price = fill_price * (1 + direction * cfg.tp_pct / 100)

            state = BacktestState(
                side=signal,
                entry_price=fill_price,
                quantity=quantity,
                sl_price=sl_price,
                tp_price=tp_price,
                entry_bar=i + 1,
                model_id=manifest.run_name,
            )

        elif signal != "HOLD" and state is not None and signal != state.side:
            # Opposing signal — close position at next open
            fill_price = _simulate_fill(signal, float(next_bar["Open"]), cfg.slippage_bps)
            realized = state.pnl(fill_price) - cfg.commission_per_trade
            equity += realized
            trades.append(_build_trade(
                state=state, exit_price=fill_price, exit_bar=i + 1,
                exit_time=next_bar.name, realized_pnl=realized, exit_reason="SIGNAL",
                model_id=manifest.run_name,
            ))
            state = None

    # Close any open position at last bar's close
    if state is not None:
        last_bar = df.iloc[-1]
        exit_price = float(last_bar["Close"])
        realized = state.pnl(exit_price) - cfg.commission_per_trade
        trades.append(_build_trade(
            state=state, exit_price=exit_price, exit_bar=len(df) - 1,
            exit_time=last_bar.name, realized_pnl=realized, exit_reason="END_OF_DATA",
            model_id=manifest.run_name,
        ))

    return trades


def _infer(manifest: Manifest, model: OnnxModel, df) -> str:
    """Run production inference pipeline on a window slice. Returns signal string."""
    try:
        features = compute_features(df, manifest.feature_names)
        features = features.dropna()
        if len(features) < manifest.window:
            return "HOLD"
        features = normalize(features, manifest)
        x = build_input(features, manifest)
        logits = model.run(x)
        return CLASS_NAMES[int(logits.argmax())]
    except Exception as exc:
        log.debug("backtest infer error: %s", exc)
        return "HOLD"


def _build_trade(
    state: BacktestState,
    exit_price: float,
    exit_bar: int,
    exit_time,
    realized_pnl: float,
    exit_reason: str,
    model_id: str,
) -> dict:
    return {
        "model_id": model_id,
        "side": state.side,
        "entry_price": state.entry_price,
        "exit_price": exit_price,
        "quantity": state.quantity,
        "entry_bar": state.entry_bar,
        "exit_bar": exit_bar,
        "exit_time": exit_time,
        "realized_pnl": realized_pnl,
        "exit_reason": exit_reason,
        "sl_price": state.sl_price,
        "tp_price": state.tp_price,
    }
```

**Notes on `YFinanceProvider.fetch_ohlcv_range`:** This method does not yet exist — it must be added to `alphalink/data/yfinance_provider.py` as part of this task. Signature: `fetch_ohlcv_range(ticker, interval, start, end, extra_bars=50) -> pd.DataFrame | None`. Fetches from `start - (extra_bars × interval_duration)` through `end`. Returns bars sorted ascending by datetime index.

- [ ] **Step 3: Add `fetch_ohlcv_range` to YFinanceProvider**

Open `alphalink/data/yfinance_provider.py`. Add:

```python
def fetch_ohlcv_range(
    self,
    ticker: str,
    interval: str,
    start: str,
    end: str,
    extra_bars: int = 50,
) -> "pd.DataFrame | None":
    """Fetch OHLCV for date range [start, end] plus extra_bars warm-up before start."""
    import pandas as pd
    from datetime import timedelta

    try:
        # Compute warm-up start: walk back extra_bars × interval duration
        _SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600,
                    "1d": 86400, "1wk": 604800}
        secs = _SECONDS.get(interval, 86400)
        warmup_delta = timedelta(seconds=secs * extra_bars)
        start_dt = pd.Timestamp(start) - warmup_delta
        start_str = start_dt.strftime("%Y-%m-%d")

        import yfinance as yf
        df = yf.download(ticker, start=start_str, end=end, interval=interval,
                         auto_adjust=True, progress=False)
        if df is None or df.empty:
            return None
        df = df.sort_index()
        return df
    except Exception as exc:
        log.warning("fetch_ohlcv_range failed for %s: %s", ticker, exc)
        return None
```

- [ ] **Step 4: Run unit tests, fix until green**

```bash
pytest tests/unit/test_backtest_engine.py -v
```

---

### Task 2: Backtest reporter (alphalink/backtest/reporter.py)

**Files:**
- Create: `alphalink/backtest/reporter.py`

- [ ] **Step 1: Write the reporter module**

Create `alphalink/backtest/reporter.py`:

```python
"""Compute and format backtest summary statistics from trade list."""
from __future__ import annotations

import math
from typing import Any


def compute_summary(trades: list[dict], initial_equity: float) -> dict[str, Any]:
    """Return summary stats dict from a list of trade dicts."""
    if not trades:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "total_pnl": 0.0,
            "total_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "sharpe": None,
        }

    pnls = [t["realized_pnl"] for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    total_pnl = sum(pnls)
    win_rate = wins / len(pnls)
    total_return_pct = total_pnl / initial_equity * 100

    # Equity curve for drawdown
    equity = initial_equity
    peak = equity
    max_dd = 0.0
    for p in pnls:
        equity += p
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100
        if dd > max_dd:
            max_dd = dd

    # Sharpe: mean/std of trade P&Ls * sqrt(252) approximation
    sharpe: float | None = None
    if len(pnls) >= 2:
        mean_p = total_pnl / len(pnls)
        variance = sum((p - mean_p) ** 2 for p in pnls) / (len(pnls) - 1)
        std_p = math.sqrt(variance) if variance > 0 else 0.0
        if std_p > 0:
            sharpe = round(mean_p / std_p * math.sqrt(252), 3)

    # Per-model breakdown
    by_model: dict[str, dict] = {}
    for t in trades:
        mid = t["model_id"]
        if mid not in by_model:
            by_model[mid] = {"trades": 0, "wins": 0, "pnl": 0.0}
        by_model[mid]["trades"] += 1
        by_model[mid]["pnl"] += t["realized_pnl"]
        if t["realized_pnl"] > 0:
            by_model[mid]["wins"] += 1
    for mid, stats in by_model.items():
        stats["win_rate"] = round(stats["wins"] / stats["trades"], 3) if stats["trades"] else 0.0

    return {
        "total_trades": len(trades),
        "win_rate": round(win_rate, 3),
        "total_pnl": round(total_pnl, 2),
        "total_return_pct": round(total_return_pct, 3),
        "max_drawdown_pct": round(max_dd, 3),
        "sharpe": sharpe,
        "by_model": by_model,
    }


def format_text(summary: dict[str, Any]) -> str:
    lines = [
        "=== Backtest Summary ===",
        f"Total trades    : {summary['total_trades']}",
        f"Win rate        : {summary['win_rate']:.1%}",
        f"Total P&L       : {summary['total_pnl']:+.2f}",
        f"Total return    : {summary['total_return_pct']:+.3f}%",
        f"Max drawdown    : {summary['max_drawdown_pct']:.3f}%",
        f"Sharpe (proxy)  : {summary['sharpe'] if summary['sharpe'] is not None else 'N/A'}",
        "",
        "--- Per-model breakdown ---",
    ]
    for mid, stats in summary.get("by_model", {}).items():
        lines.append(
            f"  {mid}: {stats['trades']} trades, "
            f"win_rate={stats['win_rate']:.1%}, pnl={stats['pnl']:+.2f}"
        )
    return "\n".join(lines)
```

- [ ] **Step 2: Write unit tests for reporter**

Add to `tests/unit/test_backtest_engine.py`:

```python
from alphalink.backtest.reporter import compute_summary, format_text


def test_compute_summary_empty():
    s = compute_summary([], initial_equity=10_000)
    assert s["total_trades"] == 0
    assert s["win_rate"] == 0.0


def test_compute_summary_basic():
    trades = [
        {"realized_pnl": 100.0, "model_id": "m1"},
        {"realized_pnl": -50.0, "model_id": "m1"},
        {"realized_pnl": 200.0, "model_id": "m2"},
    ]
    s = compute_summary(trades, initial_equity=10_000)
    assert s["total_trades"] == 3
    assert s["total_pnl"] == pytest.approx(250.0)
    assert s["win_rate"] == pytest.approx(2/3, rel=1e-3)
    assert "m1" in s["by_model"]
    assert "m2" in s["by_model"]


def test_compute_summary_max_drawdown():
    # Gains 100, then loses 200: peak=10100, trough=9900 → dd = 200/10100
    trades = [
        {"realized_pnl": 100.0, "model_id": "m1"},
        {"realized_pnl": -200.0, "model_id": "m1"},
    ]
    s = compute_summary(trades, initial_equity=10_000)
    expected_dd = 200 / 10_100 * 100
    assert abs(s["max_drawdown_pct"] - round(expected_dd, 3)) < 0.001


def test_format_text_contains_summary():
    trades = [{"realized_pnl": 50.0, "model_id": "m1"}]
    s = compute_summary(trades, initial_equity=10_000)
    text = format_text(s)
    assert "Total trades" in text
    assert "Win rate" in text
```

- [ ] **Step 3: Run tests, fix until green**

```bash
pytest tests/unit/test_backtest_engine.py -v -k "reporter or summary or format"
```

---

### Task 3: CLI `backtest` subcommand

**Files:**
- Modify: `alphalink/cli.py`

- [ ] **Step 1: Add `backtest` command to cli.py**

After the `resume()` function and before `if __name__ == "__main__":`, insert:

```python
@app.command()
def backtest(
    start: str = typer.Argument(..., help="Start date YYYY-MM-DD"),
    end: str = typer.Argument(..., help="End date YYYY-MM-DD"),
    models_dir: Path = typer.Option(None, "--models-dir", help="Override default models directory"),
    output: str = typer.Option("text", "--output", "-o", help="Output format: text | json"),
):
    """Run dry-run backtester over historical data for all loaded models."""
    import json as _json
    from alphalink.config import Settings
    from alphalink.backtest.engine import run_backtest
    from alphalink.backtest.reporter import compute_summary, format_text
    from alphalink.store.db import get_session

    settings = Settings()
    mdir = models_dir or settings.models_dir

    console.print(f"[bold]Running backtest[/bold] {start} → {end} from {mdir}")

    with get_session(settings.state_db_path) as session:
        result = run_backtest(
            session=session,
            models_dir=mdir,
            start=start,
            end=end,
            cfg=settings.backtest,
        )

    trades = result["trades"]
    summary = compute_summary(trades, initial_equity=settings.backtest.initial_equity)

    if output == "json":
        console.print(_json.dumps(summary, indent=2))
    else:
        console.print(format_text(summary))
```

- [ ] **Step 2: Verify CLI wires correctly**

```bash
python -m alphalink.cli backtest --help
```

Expected: shows `start`, `end`, `--models-dir`, `--output` args with no import errors.

---

### Task 4: Integration test

**Files:**
- Create: `tests/integration/test_backtest_integration.py`

- [ ] **Step 1: Write integration test**

Create `tests/integration/test_backtest_integration.py`:

```python
"""Integration test: full backtest run with stub ONNX model + synthetic OHLCV."""
import numpy as np
import pandas as pd
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from sqlmodel import Session

from alphalink.backtest.engine import run_backtest
from alphalink.backtest.reporter import compute_summary
from alphalink.config import BacktestConfig
from alphalink.store.db import get_engine


@pytest.fixture
def engine(tmp_path):
    import alphalink.store.db as _db
    _db._engine = None
    eng = get_engine(tmp_path / "test.db")
    yield eng
    _db._engine = None


def _make_synthetic_ohlcv(n_bars: int = 200) -> pd.DataFrame:
    """Synthetic random-walk OHLCV with proper column names."""
    rng = np.random.default_rng(42)
    closes = 100.0 + np.cumsum(rng.normal(0, 0.5, n_bars))
    highs = closes + rng.uniform(0.1, 1.0, n_bars)
    lows = closes - rng.uniform(0.1, 1.0, n_bars)
    opens = closes + rng.normal(0, 0.2, n_bars)
    volumes = rng.integers(100_000, 1_000_000, n_bars).astype(float)
    idx = pd.date_range("2024-01-01", periods=n_bars, freq="1D", tz="UTC")
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": volumes},
        index=idx,
    )


def _stub_manifest(tmp_path: Path) -> MagicMock:
    m = MagicMock()
    m.run_name = "test_model"
    m.ticker = "AAPL"
    m.interval = "1d"
    m.window = 10
    m.n_features = 5
    m.feature_names = ["Close", "Volume", "RSI", "ATR", "MACD"]
    return m


def test_backtest_runs_without_error(engine, tmp_path):
    """Engine runs end-to-end with mocked data and model, writes to DB."""
    synthetic_df = _make_synthetic_ohlcv(200)
    stub_manifest = _stub_manifest(tmp_path)
    stub_model = MagicMock()
    # Alternating BUY/HOLD signals
    call_count = [0]
    def fake_run(x):
        call_count[0] += 1
        # BUY on even calls, HOLD on odd
        return np.array([1.0, -1.0, -1.0]) if call_count[0] % 2 == 0 else np.array([-1.0, -1.0, 1.0])
    stub_model.run = fake_run

    cfg = BacktestConfig(
        initial_equity=10_000.0,
        slippage_bps=5,
        commission_per_trade=1.0,
        default_size_pct=0.1,
        sl_pct=5.0,
        tp_pct=10.0,
    )

    with patch("alphalink.backtest.engine.scan_models", return_value=[(stub_manifest, stub_model)]), \
         patch("alphalink.backtest.engine.YFinanceProvider") as MockProvider:
        mock_provider = MockProvider.return_value
        mock_provider.fetch_ohlcv_range.return_value = synthetic_df

        with Session(engine) as session:
            result = run_backtest(
                session=session,
                models_dir=tmp_path,
                start="2024-01-01",
                end="2024-12-31",
                cfg=cfg,
            )

    assert "run_id" in result
    assert isinstance(result["trades"], list)
    # Should have generated some trades given alternating signals
    assert len(result["trades"]) > 0


def test_backtest_summary_from_integration(engine, tmp_path):
    """Summary stats computed from integration run are valid."""
    synthetic_df = _make_synthetic_ohlcv(150)
    stub_manifest = _stub_manifest(tmp_path)
    stub_model = MagicMock()
    stub_model.run.return_value = np.array([1.0, -1.0, -1.0])  # always BUY

    cfg = BacktestConfig(initial_equity=10_000.0, slippage_bps=5, commission_per_trade=1.0,
                         default_size_pct=0.1, sl_pct=None, tp_pct=None)

    with patch("alphalink.backtest.engine.scan_models", return_value=[(stub_manifest, stub_model)]), \
         patch("alphalink.backtest.engine.YFinanceProvider") as MockProvider:
        mock_provider = MockProvider.return_value
        mock_provider.fetch_ohlcv_range.return_value = synthetic_df

        with Session(engine) as session:
            result = run_backtest(session=session, models_dir=tmp_path,
                                  start="2024-01-01", end="2024-12-31", cfg=cfg)

    summary = compute_summary(result["trades"], initial_equity=10_000.0)
    assert 0.0 <= summary["win_rate"] <= 1.0
    assert summary["max_drawdown_pct"] >= 0.0
```

- [ ] **Step 2: Run integration tests**

```bash
pytest tests/integration/test_backtest_integration.py -v
```

---

### Task 5: BacktestRepo — add `fetch_ohlcv_range` to YFinanceProvider + wire BacktestRepo

**Note:** `BacktestRepo.create_run` and `BacktestRepo.record_trade` are defined in Plan P1. Confirm they exist before running engine tests. If P1 is not yet merged, stub them in tests with `unittest.mock.MagicMock`.

- [ ] **Step 1: Verify BacktestRepo methods exist**

```bash
grep -n "create_run\|record_trade" alphalink/store/repos.py
```

If missing, P1 is not complete. Block this plan on P1.

---

## Acceptance Criteria

- [ ] `alphalink backtest 2024-01-01 2024-12-31` runs without error (may need real ONNX models present)
- [ ] `alphalink backtest 2024-01-01 2024-12-31 --output json` prints valid JSON
- [ ] Unit tests all pass: `pytest tests/unit/test_backtest_engine.py -v`
- [ ] Integration tests all pass: `pytest tests/integration/test_backtest_integration.py -v`
- [ ] No lookahead: inference at bar `i` only uses bars `0..i` (enforced by `window_df = df.iloc[...:i+1]`)
- [ ] Slippage applied correctly: BUY fills at `open*(1+bps/10000)`, SELL fills at `open*(1-bps/10000)`
- [ ] Commission deducted from every closed trade's realized P&L
- [ ] Open position force-closed at last bar with `exit_reason="END_OF_DATA"`
