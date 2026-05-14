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
from alphalink.consensus.softmax_avg import CLASS_NAMES
from alphalink.consensus.softmax_avg import consensus as softmax_vote
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
        equity += realized
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
        log.warning("backtest infer error: %s", exc)
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
