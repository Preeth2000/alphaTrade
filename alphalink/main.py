"""Bot entrypoint: load config + artifacts, wire components, start scheduler."""
from __future__ import annotations

import asyncio
import json
import logging
import signal
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# third-party
from sqlmodel import Session, SQLModel

# first-party (alphabetical)
from alphalink.adapter.features import compute_features
from alphalink.adapter.inference import OnnxModel
from alphalink.adapter.manifest import Manifest
from alphalink.adapter.normalize import normalize
from alphalink.adapter.window import build_input
from alphalink.broker.instrument_map import InstrumentMap
from alphalink.broker.oco_monitor import monitor_oco
from alphalink.broker.orders import submit_order
from alphalink.broker.t212_client import T212Client
from alphalink.config import Settings
from alphalink.consensus.softmax_avg import consensus_by_ticker
from alphalink.data.provider import DataProvider
from alphalink.notify import webhook as wh
from alphalink.risk.gates import GateResult, run_gates
from alphalink.risk.sizing import compute_quantity
from alphalink.kill_switch import is_halted
from alphalink.scheduler.bar_close import schedule_bar_close
from alphalink.store.db import get_engine
from alphalink.store.repos import (
    EquityRepo,
    InstrumentCacheRepo,
    Order,
    OrderRepo,
    Position,
    PositionRepo,
    Signal,
    SignalRepo,
)

log = logging.getLogger(__name__)

_INTERVAL_SECONDS: dict[str, int] = {
    "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400, "1wk": 604800,
}


def _build_data_provider(settings: Settings) -> DataProvider:
    if settings.data_provider == "polygon":
        from alphalink.data.polygon_provider import PolygonProvider
        return PolygonProvider(api_key=settings.polygon_api_key)
    from alphalink.data.yfinance_provider import YFinanceProvider
    return YFinanceProvider()


def scan_models(models_dir: Path) -> list[tuple[Manifest, OnnxModel]]:
    results = []
    for run_dir in sorted(models_dir.iterdir()):
        if not run_dir.is_dir():
            continue
        manifest_path = run_dir / "manifest.json"
        model_path = run_dir / "model.onnx"
        if not manifest_path.exists() or not model_path.exists():
            log.warning("Skipping %s: missing manifest.json or model.onnx", run_dir.name)
            continue
        try:
            manifest = Manifest.load(manifest_path)
            model = OnnxModel(manifest, model_path)
            results.append((manifest, model))
            log.info("Loaded model %s (%s, %s)", manifest.run_name, manifest.ticker, manifest.interval)
        except Exception as exc:
            log.error("Failed to load model in %s: %s", run_dir.name, exc)
    return results


def reconcile_positions(t212: T212Client, settings: Settings) -> None:
    """Sync local PositionRepo with actual T212 portfolio on startup.

    Prevents stale SQLite state from causing wrong gate decisions after downtime.
    """
    engine = get_engine(settings.state_db_path)
    SQLModel.metadata.create_all(engine)
    try:
        t212_positions = t212.get_positions()
    except Exception as exc:
        log.warning("Could not fetch T212 positions for reconciliation: %s. Using local state.", exc)
        return

    with Session(engine) as session:
        repo = PositionRepo(session)

        # Build set of t212 tickers currently held per T212
        t212_held: dict[str, dict] = {}
        for p in t212_positions:
            ticker = p.get("ticker") or p.get("instrument", {}).get("ticker", "")
            if ticker:
                t212_held[ticker] = p

        # Remove local positions T212 no longer holds
        for local_pos in repo.all():
            if local_pos.quantity > 0 and local_pos.t212_ticker not in t212_held:
                log.info("Reconcile: removing stale position %s (not in T212 portfolio)", local_pos.t212_ticker)
                wh.notify(
                    "WARNING",
                    f"Reconcile: removing stale position {local_pos.t212_ticker} (not in T212 portfolio)",
                    category="reconcile-divergence",
                )
                repo.remove(local_pos.t212_ticker)

        # Add/update positions T212 holds but local state is missing or wrong
        for ticker, p in t212_held.items():
            qty = float(p.get("quantity", 0))
            avg = float(p.get("averagePricePaid", 0))
            existing = repo.get(ticker)
            if not existing or existing.quantity != qty:
                log.info("Reconcile: syncing position %s qty=%s avg=%s", ticker, qty, avg)
                repo.upsert(Position(
                    t212_ticker=ticker,
                    quantity=qty,
                    avg_entry=avg,
                    last_signal_ts=existing.last_signal_ts if existing else None,
                    cooldown_until_ts=existing.cooldown_until_ts if existing else None,
                ))


async def run(settings: Settings) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _oco_tasks: set[asyncio.Task] = set()
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)
    if settings.webhook_url:
        wh.configure(settings.webhook_url)
        _wh = wh.WebhookHandler()
        _wh.setLevel(settings.webhook_level)
        root = logging.getLogger()
        if not any(isinstance(h, wh.WebhookHandler) for h in root.handlers):
            root.addHandler(_wh)
        wh.notify("INFO", "alphaLink bot started", category="startup")

    provider = _build_data_provider(settings)
    t212 = T212Client(api_key=settings.t212_api_key, env=settings.t212_env)

    models = scan_models(settings.models_dir)
    if not models:
        log.error("No models loaded from %s. Exiting.", settings.models_dir)
        return

    # Reconcile positions with T212 before first tick
    reconcile_positions(t212, settings)

    # Build static t212_ticker overrides from overrides.yaml
    static_map: dict[str, str] = {}
    for run_name, override in settings.model_overrides.items():
        if override.t212_ticker:
            manifest_match = next((m for m, _ in models if m.run_name == run_name), None)
            if manifest_match:
                static_map[manifest_match.ticker] = override.t212_ticker

    engine = get_engine(settings.state_db_path)

    # Group models by interval for co-scheduling
    by_interval: dict[str, list[tuple[Manifest, OnnxModel]]] = defaultdict(list)
    for manifest, model in models:
        override = settings.model_overrides.get(manifest.run_name)
        if override and not override.enabled:
            continue
        by_interval[manifest.interval].append((manifest, model))

    def make_tick(interval: str, interval_models: list[tuple[Manifest, OnnxModel]]):
        async def tick() -> None:
            ticker_logits: dict[str, list] = defaultdict(list)
            ticker_manifest: dict[str, Manifest] = {}

            for manifest, model in interval_models:
                try:
                    df = provider.fetch_ohlcv(manifest.ticker, manifest.interval, manifest.window)
                    features = compute_features(df, manifest.feature_names)
                    features = features.dropna()
                    features = normalize(features, manifest)
                    x = build_input(features, manifest)
                    logits = model.run(x)
                    ticker_logits[manifest.ticker].append(logits)
                    ticker_manifest[manifest.ticker] = manifest
                except Exception as exc:
                    log.error("Inference error for %s: %s", manifest.run_name, exc)

            signals = consensus_by_ticker(ticker_logits)
            kill_switch_active = is_halted()
            if kill_switch_active:
                log.warning("Kill switch active — signals will be logged but orders skipped")

            # Fresh session per tick — no long-lived session across bar closes
            with Session(engine) as session:
                inst_cache = InstrumentCacheRepo(session)
                instrument_map = InstrumentMap(t212, inst_cache, static_map)
                pos_repo = PositionRepo(session)
                eq_repo = EquityRepo(session)
                signal_repo = SignalRepo(session)
                order_repo = OrderRepo(session)

                try:
                    equity = t212.get_total_equity()
                except Exception as exc:
                    log.error("Cannot fetch equity: %s. Skipping tick.", exc)
                    return
                eq_repo.record(equity)

                today_open = eq_repo.today_open() or equity
                daily_loss_pct = (equity - today_open) / (today_open or 1)
                daily_loss_halted = daily_loss_pct <= -settings.risk.daily_loss_halt_pct
                if daily_loss_halted:
                    log.warning("Daily loss halt active (%.2f%%). No new orders.", daily_loss_pct * 100)
                    wh.notify(
                        "WARNING",
                        f"Daily loss halt active ({daily_loss_pct:.2%}). No new orders.",
                        category="daily-loss-halt",
                    )

                for yf_ticker, signal in signals.items():
                    manifest = ticker_manifest[yf_ticker]
                    override = settings.model_overrides.get(manifest.run_name)

                    try:
                        t212_ticker = instrument_map.resolve(yf_ticker)
                    except RuntimeError as exc:
                        log.error("Cannot resolve %s: %s", yf_ticker, exc)
                        continue

                    sig_rec = Signal(
                        run_name=manifest.run_name,
                        ticker=yf_ticker,
                        signal=signal,
                        model_count=len(ticker_logits[yf_ticker]),
                        raw_json=json.dumps([l.tolist() for l in ticker_logits[yf_ticker]]),
                    )
                    signal_repo.save(sig_rec)
                    log.info("Signal %s → %s", yf_ticker, signal)

                    if kill_switch_active:
                        log.info("Kill switch: order skipped for %s %s", signal, yf_ticker)
                        continue

                    gate: GateResult = run_gates(
                        signal=signal,
                        t212_ticker=t212_ticker,
                        position_repo=pos_repo,
                        max_positions=settings.risk.max_positions,
                        daily_loss_halted=daily_loss_halted,
                    )

                    if not gate.approved:
                        log.info("Gate rejected %s %s: %s", signal, t212_ticker, gate.reason)
                        continue

                    size_pct = (
                        override.size_pct if override and override.size_pct else None
                    ) or settings.defaults.size_pct

                    try:
                        df2 = provider.fetch_ohlcv(yf_ticker, manifest.interval, 1)
                        current_price = float(df2["Close"].iloc[-1])
                    except Exception as exc:
                        log.error("Cannot get current price for %s: %s", yf_ticker, exc)
                        continue

                    qty = compute_quantity(equity, current_price, size_pct)
                    if qty <= 0:
                        log.warning("Computed quantity 0 for %s, skipping", t212_ticker)
                        continue

                    order_rec = Order(
                        signal_id=sig_rec.id,
                        t212_ticker=t212_ticker,
                        side=signal,
                        quantity=qty,
                    )
                    order_repo.save(order_rec)

                    try:
                        resp = submit_order(
                            t212=t212,
                            instrument_ticker=t212_ticker,
                            side=signal,
                            quantity=qty,
                        )
                        fill_price = resp.get("fillPrice") or resp.get("filledQuantity")
                        t212_id = str(resp.get("id", ""))
                        order_repo.update_fill(order_rec.id, "filled", fill_price, t212_id)
                        log.info("Filled %s %s qty=%s", signal, t212_ticker, qty)

                        cooldown_td = timedelta(
                            seconds=_INTERVAL_SECONDS.get(manifest.interval, 86400)
                            * settings.defaults.cooldown_bars
                        )

                        if signal == "BUY":
                            pos_repo.upsert(Position(
                                t212_ticker=t212_ticker,
                                quantity=qty,
                                avg_entry=current_price,
                                last_signal_ts=datetime.utcnow(),
                            ))
                            raw_fill = resp.get("fillPrice")
                            entry_price = float(raw_fill) if raw_fill else current_price
                            sl_price = entry_price * (1 - settings.defaults.stop_loss_pct)
                            tp_price = entry_price * (1 + settings.defaults.take_profit_pct)
                            stop_resp = None
                            try:
                                stop_resp = t212.place_stop_order(t212_ticker, qty, sl_price)
                                limit_resp = t212.place_limit_order(t212_ticker, qty, tp_price)
                                _task = asyncio.create_task(monitor_oco(
                                    t212=t212,
                                    t212_ticker=t212_ticker,
                                    stop_order_id=str(stop_resp["id"]),
                                    limit_order_id=str(limit_resp["id"]),
                                    engine=engine,
                                    cooldown_td=cooldown_td,
                                ))
                                _oco_tasks.add(_task)
                                _task.add_done_callback(_oco_tasks.discard)
                                log.info(
                                    "OCO submitted for %s: SL=%.4f TP=%.4f",
                                    t212_ticker, sl_price, tp_price,
                                )
                            except Exception as exc:
                                log.error("OCO setup failed for %s: %s", t212_ticker, exc)
                                if stop_resp is not None:
                                    try:
                                        t212.cancel_order(str(stop_resp["id"]))
                                        log.info("Cancelled orphaned stop leg %s for %s", stop_resp["id"], t212_ticker)
                                    except Exception as cancel_exc:
                                        log.warning("Could not cancel orphaned stop leg for %s: %s", t212_ticker, cancel_exc)
                        elif signal == "SELL":
                            pos_repo.remove(t212_ticker)
                            pos_repo.upsert(Position(
                                t212_ticker=t212_ticker,
                                quantity=0,
                                avg_entry=0,
                                cooldown_until_ts=datetime.utcnow() + cooldown_td,
                            ))

                    except Exception as exc:
                        order_repo.update_fill(order_rec.id, "error", None, "")
                        log.error("Order failed for %s: %s", t212_ticker, exc)

        return tick

    tasks = [
        asyncio.create_task(
            schedule_bar_close(
                interval,
                make_tick(interval, interval_models),
                stop_event,
                extended_hours=settings.defaults.extended_hours,
            )
        )
        for interval, interval_models in by_interval.items()
    ]
    log.info("Scheduler running. Intervals: %s", list(by_interval.keys()))
    await asyncio.gather(*tasks)

    # Drain OCO monitor tasks before exit
    for task in list(_oco_tasks):
        task.cancel()
    if _oco_tasks:
        await asyncio.gather(*_oco_tasks, return_exceptions=True)
    log.info("Graceful shutdown complete.")
