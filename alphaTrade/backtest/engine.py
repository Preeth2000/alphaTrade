"""Bar-by-bar backtester. Reuses production pipeline; no T212 calls."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlmodel import Session

from alphaTrade.adapter.features import compute_features
from alphaTrade.adapter.inference import OnnxModel
from alphaTrade.adapter.manifest import Manifest
from alphaTrade.adapter.normalize import normalize
from alphaTrade.adapter.window import build_input
from alphaTrade.config import BacktestConfig
from alphaTrade.consensus.softmax_avg import CLASS_NAMES
from alphaTrade.consensus.softmax_avg import consensus as softmax_vote
from alphaTrade.data.provider import DataProvider
from alphaTrade.data.yfinance_provider import YFinanceProvider
from alphaTrade.main import scan_models
from alphaTrade.store.repos import BacktestRepo

log = logging.getLogger(__name__)

_INTERVAL_SECONDS: dict[str, int] = {
    "1m": 60, "5m": 300, "15m": 900, "1h": 3600,
    "4h": 14400, "1d": 86400, "1wk": 604800,
}


@dataclass
class BacktestState:
    """Open simulated position."""
    side: str          # "BUY" | "SELL"
    entry_price: float
    quantity: float
    sl_price: float | None
    tp_price: float | None
    entry_bar: int
    entry_time: Any
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


def _compute_protected_fraction(
    queue_position: int,
    stop_gap: float,
    limit_gap: float,
    bar_duration_secs: float,
) -> float:
    """Fraction of bar range protected by OCO, accounting for queue position lag.

    Queue position K means (K * (stop_gap + limit_gap)) seconds pass before OCO is placed.
    During that window, price moves are unprotected. We model the protected portion
    as the fraction of bar duration remaining after the lag.
    """
    lag_secs = queue_position * (stop_gap + limit_gap)
    return max(0.0, 1.0 - lag_secs / bar_duration_secs)


def _check_sl_tp_with_lag(
    state: "BacktestState",
    high: float,
    low: float,
    protected_fraction: float,
    bar_open: float | None = None,
) -> tuple[str, float] | None:
    """Check SL/TP with OCO lag applied. protected_fraction=1.0 is identical to _check_sl_tp.

    When fraction < 1.0, scales the bar's high/low range around bar_open (or entry_price)
    to approximate only the portion of the move that occurred after OCO was placed.
    """
    if protected_fraction >= 1.0:
        return _check_sl_tp(state, high=high, low=low)

    if protected_fraction <= 0.0:
        return None

    # Scale range around open (or entry_price as fallback)
    origin = bar_open if bar_open is not None else state.entry_price
    adj_high = origin + (high - origin) * protected_fraction
    adj_low = origin - (origin - low) * protected_fraction
    return _check_sl_tp(state, high=adj_high, low=adj_low)


def run_backtest(
    session: Session,
    models_dir: Path,
    start: str,
    end: str,
    cfg: BacktestConfig,
    run_id: int | None = None,
    model_filter: str | None = None,
    provider: DataProvider | None = None,
) -> dict[str, Any]:
    """Run backtest for all (or one filtered) model in models_dir. Returns summary dict."""
    models = scan_models(models_dir)
    if model_filter is not None:
        models = [(m, mo) for m, mo in models if m.run_name == model_filter]
    if not models:
        raise RuntimeError(
            f"No models found in {models_dir}" + (f" matching {model_filter!r}" if model_filter else "")
        )

    if provider is None:
        provider = YFinanceProvider()
    repo = BacktestRepo(session)
    if run_id is None:
        run_id = repo.create_run(start=start, end=end, config_json=cfg.model_dump_json())

    all_trades: list[dict] = []

    for manifest, model in models:
        log.info("backtest: running %s (%s, %s)", manifest.run_name, manifest.ticker, manifest.interval)
        error_msg = ""
        try:
            if cfg.simulate_oco_lag and len(models) > 1:
                trades, status = _run_single_model_with_lag(
                    manifest=manifest,
                    model=model,
                    all_models=models,
                    provider=provider,  # type: ignore[arg-type]
                    start=start,
                    end=end,
                    cfg=cfg,
                )
            else:
                trades, status = _run_single_model(
                    manifest=manifest,
                    model=model,
                    provider=provider,  # type: ignore[arg-type]
                    start=start,
                    end=end,
                    cfg=cfg,
                )
            for t in trades:
                repo.record_trade(run_id=run_id, **t)
        except Exception as exc:
            log.exception("backtest: model %s raised: %s", manifest.run_name, exc)
            trades, status, error_msg = [], "failed", str(exc)
        try:
            repo.record_model_run(
                run_id=run_id,
                model_id=manifest.run_name,
                ticker=manifest.ticker,
                interval=manifest.interval,
                trade_count=len(trades),
                status=status,
                error_msg=error_msg,
            )
        except Exception as exc:
            log.exception("backtest: failed to record model run for %s: %s", manifest.run_name, exc)
        all_trades.extend(trades)
        log.info("backtest: %s → %d trades", manifest.run_name, len(trades))

    return {"run_id": run_id, "trades": all_trades}


def _run_single_model(
    manifest: Manifest,
    model: OnnxModel,
    provider: DataProvider,
    start: str,
    end: str,
    cfg: BacktestConfig,
) -> tuple[list[dict], str]:
    """Walk forward bar-by-bar for one model. Returns (trades, status)."""
    # Fetch enough history for feature computation + window warm-up
    warmup_bars = manifest.window + 50
    df = provider.fetch_ohlcv_range(manifest.ticker, manifest.interval, start=start, end=end, extra_bars=warmup_bars)
    if df is None or len(df) < manifest.window + 2:
        log.warning("backtest: not enough data for %s", manifest.run_name)
        return [], "no_data"

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
                entry_time=next_bar.name,
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

    return trades, "ran"


def _run_single_model_with_lag(
    manifest: Manifest,
    model: OnnxModel,
    all_models: list[tuple[Manifest, OnnxModel]],
    provider: DataProvider,
    start: str,
    end: str,
    cfg: BacktestConfig,
) -> tuple[list[dict], str]:
    """Same as _run_single_model but applies OCO lag based on how many same-interval
    models also signalled BUY on each bar.

    Lag per queue position = (orders_stop_min_gap + orders_limit_min_gap) = 4.0s default.
    """
    same_interval = [
        (m, mo) for m, mo in all_models
        if m.interval == manifest.interval and m.run_name != manifest.run_name
    ]

    warmup_bars = manifest.window + 50
    df = provider.fetch_ohlcv_range(manifest.ticker, manifest.interval, start=start, end=end, extra_bars=warmup_bars)
    if df is None or len(df) < manifest.window + 2:
        log.warning("backtest: not enough data for %s", manifest.run_name)
        return [], "no_data"

    # Pre-compute peer signals for every bar to determine queue positions
    peer_signals: dict[int, list[str]] = {}  # bar_index -> list of peer sides
    for peer_manifest, peer_model in same_interval:
        try:
            peer_df = provider.fetch_ohlcv_range(
                peer_manifest.ticker, peer_manifest.interval, start=start, end=end, extra_bars=warmup_bars
            )
            if peer_df is None or len(peer_df) < len(df):
                continue
            for i in range(warmup_bars, min(len(df), len(peer_df)) - 1):
                window_df = peer_df.iloc[max(0, i - warmup_bars):i + 1]
                sig = _infer(peer_manifest, peer_model, window_df)
                if sig == "BUY":
                    peer_signals.setdefault(i, []).append("BUY")
        except Exception as exc:
            log.warning("backtest lag: peer inference failed for %s: %s", peer_manifest.run_name, exc)

    _interval_secs = _INTERVAL_SECONDS.get(manifest.interval, 3600)
    trades: list[dict] = []
    state: BacktestState | None = None
    equity = cfg.initial_equity

    for i in range(warmup_bars, len(df) - 1):
        bar = df.iloc[i]
        next_bar = df.iloc[i + 1]

        window_df = df.iloc[max(0, i - warmup_bars):i + 1]
        signal = _infer(manifest, model, window_df)

        if state is not None:
            peers_buying = len(peer_signals.get(i, []))
            queue_pos = peers_buying
            protected = _compute_protected_fraction(
                queue_position=queue_pos,
                stop_gap=cfg.oco_stop_gap_secs,
                limit_gap=cfg.oco_limit_gap_secs,
                bar_duration_secs=_interval_secs,
            )
            hit = _check_sl_tp_with_lag(
                state,
                high=float(bar["High"]),
                low=float(bar["Low"]),
                protected_fraction=protected,
                bar_open=float(bar["Open"]),
            )
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

        if signal in ("BUY", "SELL") and state is None:
            fill_price = _simulate_fill(signal, float(next_bar["Open"]), cfg.slippage_bps)
            size_pct = cfg.default_size_pct
            quantity = (equity * size_pct) / fill_price
            direction = 1 if signal == "BUY" else -1
            sl_price = fill_price * (1 - direction * cfg.sl_pct / 100) if cfg.sl_pct is not None else None
            tp_price = fill_price * (1 + direction * cfg.tp_pct / 100) if cfg.tp_pct is not None else None
            state = BacktestState(
                side=signal,
                entry_price=fill_price,
                quantity=quantity,
                sl_price=sl_price,
                tp_price=tp_price,
                entry_bar=i + 1,
                entry_time=next_bar.name,
                model_id=manifest.run_name,
            )
        elif signal != "HOLD" and state is not None and signal != state.side:
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

    return trades, "ran"


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
        "entry_time": state.entry_time,
        "exit_bar": exit_bar,
        "exit_time": exit_time,
        "realized_pnl": realized_pnl,
        "exit_reason": exit_reason,
        "sl_price": state.sl_price,
        "tp_price": state.tp_price,
    }
