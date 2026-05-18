"""Bot entrypoint: load config + artifacts, wire components, start scheduler."""
from __future__ import annotations

import asyncio
import json
import logging
import signal
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# third-party
from sqlmodel import Session

# first-party (alphabetical)
from alphaTrade.adapter.features import compute_features
from alphaTrade.adapter.inference import OnnxModel
from alphaTrade.adapter.manifest import Manifest
from alphaTrade.adapter.normalize import normalize
from alphaTrade.adapter.window import build_input
from alphaTrade.broker.instrument_map import InstrumentMap
from alphaTrade.broker.oco_monitor import monitor_oco
from alphaTrade.broker.orders import make_client_order_id, submit_order_async
from alphaTrade.broker.t212_client import T212Client
from alphaTrade.config import Settings
from alphaTrade.consensus.softmax_avg import consensus_by_ticker
from alphaTrade.data.provider import DataProvider
from alphaTrade.notify import webhook as wh
from alphaTrade.notify.alerting import AlertManager, AlertLevel
from alphaTrade.risk.gates import GateResult, run_gates
from alphaTrade.risk.performance import _effective_config
from alphaTrade.risk.sizing import compute_quantity
from alphaTrade.kill_switch import is_halted
from alphaTrade.scheduler.bar_close import schedule_bar_close
from alphaTrade.store.db import get_engine
from alphaTrade.store.repos import (
    BotSettings,
    BotSettingsRepo,
    EquityRepo,
    InstrumentCache,
    InstrumentCacheRepo,
    ModelOverrideRecord,
    ModelOverrideRepo,
    ModelPerformanceRepo,
    Order,
    OrderRepo,
    Position,
    PositionRepo,
    SectorCacheRepo,
    Signal,
    SignalRepo,
)
from alphaTrade.health import HealthState, start_health_server
from alphaTrade.metrics import (
    daily_pnl_pct as metric_daily_pnl_pct,
    equity_total as metric_equity_total,
    inference_errors_total,
    inference_latency_seconds,
    open_positions as metric_open_positions,
    orders_total,
    signals_total,
)

log = logging.getLogger(__name__)

_INTERVAL_SECONDS: dict[str, int] = {
    "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400, "1wk": 604800,
}


def _t212_credentials(db_s) -> tuple[str, str, str]:
    """Return (api_key, secret_key, env) for the active T212 account."""
    account = db_s.t212_active_account or "demo"
    env = "demo" if account == "demo" else "live"
    if account == "demo":
        return db_s.t212_demo_api_key or "", db_s.t212_demo_secret_key or "", env
    if account == "invest":
        return db_s.t212_invest_api_key or "", db_s.t212_invest_secret_key or "", env
    if account == "isa":
        return db_s.t212_isa_api_key or "", db_s.t212_isa_secret_key or "", env
    return "", "", env


def apply_bot_settings(
    db_s: BotSettings,
    settings: Settings,
    t212_holder: list,
    provider_holder: list,
) -> None:
    from alphaTrade.broker.t212_client import T212Client, _BASE_URLS
    new_key, new_secret, new_env = _t212_credentials(db_s)
    current_auth = getattr(t212_holder[0], "_auth", None)
    current_key = getattr(current_auth, "username", None) if current_auth else getattr(t212_holder[0], "_headers", {}).get("Authorization")
    current_base = getattr(t212_holder[0], "_base", None)
    new_base = _BASE_URLS.get(new_env)
    if new_key and (current_key != new_key or current_base != new_base):
        t212_holder[0] = T212Client(api_key=new_key, secret_key=new_secret, env=new_env)
        log.info("Hot-reload: T212Client reinitialised (account=%s, env=%s)", db_s.t212_active_account, new_env)
    if db_s.data_provider and db_s.data_provider != settings.data_provider:
        settings.data_provider = db_s.data_provider
        provider_holder[0] = _build_data_provider(settings)
        log.info("Hot-reload: data provider switched to %s", db_s.data_provider)
    if db_s.size_pct:
        settings.defaults.size_pct = db_s.size_pct
    if db_s.stop_loss_pct:
        settings.defaults.stop_loss_pct = db_s.stop_loss_pct
    if db_s.take_profit_pct:
        settings.defaults.take_profit_pct = db_s.take_profit_pct
    if db_s.cooldown_bars:
        settings.defaults.cooldown_bars = db_s.cooldown_bars
    settings.defaults.extended_hours = db_s.extended_hours
    if db_s.max_positions:
        settings.risk.max_positions = db_s.max_positions
    if db_s.daily_loss_halt_pct:
        settings.risk.daily_loss_halt_pct = db_s.daily_loss_halt_pct
    settings.alerts.slack.enabled = db_s.slack_enabled
    if db_s.slack_webhook_url:
        settings.alerts.slack.webhook_url = db_s.slack_webhook_url
    settings.alerts.email.enabled = db_s.email_enabled
    if db_s.email_to_addrs:
        settings.alerts.email.to_addrs = [
            a.strip() for a in db_s.email_to_addrs.split(",") if a.strip()
        ]


def _build_data_provider(settings: Settings) -> DataProvider:
    if settings.data_provider == "polygon":
        from alphaTrade.data.polygon_provider import PolygonProvider
        return PolygonProvider(api_key=settings.polygon_api_key)
    from alphaTrade.data.yfinance_provider import YFinanceProvider
    return YFinanceProvider()


def scan_models(models_dir: Path) -> list[tuple[Manifest, OnnxModel]]:
    if not models_dir.exists():
        log.error("models_dir does not exist: %s", models_dir)
        return []
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


def build_sell_journal_entry(
    model_id: str,
    t212_ticker: str,
    exit_price: float,
    quantity: float,
    position: "Position",
) -> "TradeJournal":
    from alphaTrade.store.repos import TradeJournal
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


async def reconcile_positions(t212: T212Client, settings: Settings) -> None:
    """Sync local PositionRepo with actual T212 portfolio on startup.

    Prevents stale SQLite state from causing wrong gate decisions after downtime.
    """
    engine = get_engine(settings.state_db_path)
    try:
        t212_positions = await asyncio.to_thread(t212.get_positions)
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


def _preresolve_tickers(
    t212,
    engine,
    registry,
    static_map: dict[str, str],
) -> None:
    """Populate instrument cache for all registry tickers before tick loop starts."""
    from sqlmodel import Session
    from alphaTrade.broker.instrument_map import InstrumentMap
    from alphaTrade.store.repos import InstrumentCacheRepo

    with Session(engine) as session:
        cache = InstrumentCacheRepo(session)
        inst_map = InstrumentMap(t212, cache, static_map)
        for manifest, _ in registry.by_run_name.values():
            try:
                inst_map.resolve(manifest.ticker)
            except Exception as exc:
                log.warning("Pre-resolve failed for %s: %s", manifest.ticker, exc)


def _merge_overrides(
    yaml_overrides: dict,
    db_overrides: dict[str, ModelOverrideRecord],
) -> dict:
    """Merge DB enabled flag on top of yaml overrides for registry.refresh()."""
    from alphaTrade.config import ModelOverride
    merged = dict(yaml_overrides)
    for run_name, db_ov in db_overrides.items():
        if db_ov.enabled is not None:
            existing = merged.get(run_name, ModelOverride())
            merged[run_name] = ModelOverride(
                enabled=db_ov.enabled,
                t212_ticker=existing.t212_ticker,
                size_pct=existing.size_pct,
            )
    return merged


def make_tick(
    interval: str,
    *,
    registry,
    settings: Settings,
    engine,
    t212_holder: list,
    provider_holder: list,
    health_state,
    oco_tasks: set,
    static_map: dict[str, str],
    alert_manager=None,
):
    """Return an async tick coroutine for the given interval.

    All T212Client HTTP calls run via asyncio.to_thread so the event loop
    stays responsive to health checks and OCO monitor tasks during latency spikes.
    """
    from alphaTrade.api import stream_bus as _sb
    # Tracks halt state for edge-triggered alerting across ticks.
    # Survives UTC day boundaries; relies on today_open reset producing
    # a non-halted tick before any re-halt for next-day re-entry alert.
    _prev_halt: bool = False
    async def tick() -> None:
        nonlocal _prev_halt
        with Session(engine) as _hs:
            _db_s = BotSettingsRepo(_hs).get()
        if _db_s is not None:
            apply_bot_settings(_db_s, settings, t212_holder, provider_holder)
            _tick_creds = _t212_credentials(_db_s)
            health_state.t212_configured = bool(_tick_creds[0])
            if not health_state.t212_configured:
                log.warning("No API key for active T212 account — skipping tick")
                health_state.t212_ok = False
                health_state.last_tick_at = datetime.now(timezone.utc)
                return
        t212 = t212_holder[0]
        bar_close_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        with Session(engine) as _ov_s:
            db_overrides: dict[str, ModelOverrideRecord] = {
                r.run_name: r for r in ModelOverrideRepo(_ov_s).all()
            }
        await registry.refresh(settings.models_dir, _merge_overrides(settings.model_overrides, db_overrides))
        snap = registry.snapshot_by_interval()
        interval_models = snap.get(interval, [])
        health_state.models_loaded = bool(registry.by_run_name)
        health_state.longest_interval_seconds = max(
            (_INTERVAL_SECONDS.get(i, 3600) for i in snap), default=3600,
        )
        if not interval_models:
            log.debug("No active models for interval %s this tick", interval)
            return

        ticker_logits: dict[str, list] = defaultdict(list)
        ticker_manifest: dict[str, Manifest] = {}
        ticker_atr: dict[str, float] = {}

        for manifest, model in interval_models:
            try:
                t0 = time.perf_counter()
                df = provider_holder[0].fetch_ohlcv(manifest.ticker, manifest.interval, manifest.window)
                features = compute_features(df, manifest.feature_names)
                features = features.dropna()
                # Save raw ATR before normalization (ATR sizing uses price units)
                if "ATR" in features.columns and not features.empty:
                    ticker_atr[manifest.ticker] = float(features["ATR"].iloc[-1])
                features = normalize(features, manifest)
                x = build_input(features, manifest)
                logits = model.run(x)
                inference_latency_seconds.labels(run_name=manifest.run_name).observe(
                    time.perf_counter() - t0
                )
                ticker_logits[manifest.ticker].append(logits)
                ticker_manifest[manifest.ticker] = manifest
            except Exception as exc:
                inference_errors_total.labels(run_name=manifest.run_name).inc()
                log.error("Inference error for %s: %s", manifest.run_name, exc)

        signals = consensus_by_ticker(ticker_logits)
        kill_switch_active = is_halted()
        if kill_switch_active:
            log.warning("Kill switch active — signals will be logged but orders skipped")
            if alert_manager is not None:
                alert_manager.notify("Kill switch engaged — orders halted", AlertLevel.CRITICAL)

        # Fresh session per tick — no long-lived session across bar closes
        with Session(engine) as session:
            inst_cache = InstrumentCacheRepo(session)
            instrument_map = InstrumentMap(t212, inst_cache, static_map, sector_repo=SectorCacheRepo(session))
            pos_repo = PositionRepo(session)
            eq_repo = EquityRepo(session)
            signal_repo = SignalRepo(session)
            order_repo = OrderRepo(session)

            try:
                equity = await asyncio.to_thread(t212.get_total_equity)
                health_state.t212_ok = True
            except Exception as exc:
                log.error("Cannot fetch equity: %s. Skipping tick.", exc)
                health_state.t212_ok = False
                health_state.last_tick_at = datetime.now(timezone.utc)
                return
            eq_repo.record(equity)
            metric_equity_total.set(equity)

            today_open = eq_repo.today_open() or equity
            daily_loss_pct = (equity - today_open) / (today_open or 1)
            metric_daily_pnl_pct.set(daily_loss_pct)
            daily_loss_halted = daily_loss_pct <= -settings.risk.daily_loss_halt_pct
            if daily_loss_halted and not _prev_halt:
                log.warning("Daily loss halt active (%.2f%%). No new orders.", daily_loss_pct * 100)
                wh.notify(
                    "WARNING",
                    f"Daily loss halt active ({daily_loss_pct:.2%}). No new orders.",
                    category="daily-loss-halt",
                )
                if alert_manager is not None:
                    alert_manager.notify(
                        f"Daily loss at 80% of limit: {abs(daily_loss_pct * today_open):.2f} / {settings.risk.daily_loss_halt_pct * today_open:.2f}",
                        AlertLevel.WARNING,
                    )
            _prev_halt = daily_loss_halted

            current_vix: float | None = None
            if settings.risk.sizing_mode == "vix":
                current_vix = await asyncio.to_thread(provider_holder[0].fetch_vix)
                if current_vix is None:
                    log.warning("VIX fetch failed; compute_quantity will use internal fallback")

            for yf_ticker, signal in signals.items():
                manifest = ticker_manifest[yf_ticker]
                yaml_ov = settings.model_overrides.get(manifest.run_name)
                db_ov = db_overrides.get(manifest.run_name)
                eff_size_pct = (
                    (db_ov.size_pct if db_ov and db_ov.size_pct else None)
                    or (yaml_ov.size_pct if yaml_ov and yaml_ov.size_pct else None)
                    or settings.defaults.size_pct
                )
                eff_stop_loss_pct = (
                    (db_ov.stop_loss_pct if db_ov and db_ov.stop_loss_pct else None)
                    or settings.defaults.stop_loss_pct
                )
                eff_take_profit_pct = (
                    (db_ov.take_profit_pct if db_ov and db_ov.take_profit_pct else None)
                    or settings.defaults.take_profit_pct
                )
                eff_cooldown_bars = (
                    (db_ov.cooldown_bars if db_ov and db_ov.cooldown_bars else None)
                    or settings.defaults.cooldown_bars
                )
                broker_ticker_ov = (
                    (db_ov.broker_ticker if db_ov and db_ov.broker_ticker else None)
                    or (yaml_ov.t212_ticker if yaml_ov and yaml_ov.t212_ticker else None)
                )
                if broker_ticker_ov:
                    t212_ticker = broker_ticker_ov
                else:
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
                signals_total.labels(ticker=yf_ticker, signal=signal).inc()
                log.info("Signal %s → %s", yf_ticker, signal)
                _sb.publish({"type": "signal_fired", "ticker": yf_ticker, "signal": signal,
                             "run_name": manifest.run_name, "ts": bar_close_iso})

                if kill_switch_active:
                    log.info("Kill switch: order skipped for %s %s", signal, yf_ticker)
                    continue

                pos = pos_repo.get(t212_ticker)

                gate: GateResult = run_gates(
                    signal=signal,
                    t212_ticker=t212_ticker,
                    position_repo=pos_repo,
                    max_positions=settings.risk.max_positions,
                    daily_loss_halted=daily_loss_halted,
                    model_id=manifest.run_name,
                    perf_repo=ModelPerformanceRepo(session),
                    yf_ticker=manifest.ticker,
                    equity=equity,
                    sector_repo=SectorCacheRepo(session),
                    risk_cfg=settings.risk,
                )

                if not gate.approved:
                    log.info("Gate rejected %s %s: %s", signal, t212_ticker, gate.reason)
                    continue

                size_pct = eff_size_pct

                try:
                    df2 = provider_holder[0].fetch_ohlcv(yf_ticker, manifest.interval, 1)
                    current_price = float(df2["Close"].iloc[-1])
                except Exception as exc:
                    log.error("Cannot get current price for %s: %s", yf_ticker, exc)
                    continue

                raw_atr = ticker_atr.get(manifest.ticker, 0.0)
                qty = compute_quantity(
                    equity=equity,
                    current_price=current_price,
                    size_pct=size_pct,
                    mode=settings.risk.sizing_mode,
                    atr=raw_atr,
                    atr_risk_pct=settings.risk.atr.risk_pct,
                    atr_multiplier=settings.risk.atr.atr_multiplier,
                    current_vix=current_vix,
                    vix_base_size_pct=settings.risk.vix.base_size_pct,
                    vix_scalar=settings.risk.vix.vix_scalar,
                    vix_max_size_pct=settings.risk.vix.max_size_pct,
                )
                if qty <= 0:
                    log.warning("Computed quantity 0 for %s, skipping", t212_ticker)
                    continue

                cid = make_client_order_id(manifest.run_name, t212_ticker, bar_close_iso, signal)

                try:
                    resp = await submit_order_async(
                        t212=t212,
                        instrument_ticker=t212_ticker,
                        side=signal,
                        quantity=qty,
                        order_repo=order_repo,
                        client_order_id=cid,
                    )
                    if resp.get("skipped_duplicate"):
                        log.info("Duplicate order skipped (cid=%s)", cid)
                        orders_total.labels(side=signal, status="skipped_duplicate").inc()
                        continue
                    fill_price = resp.get("fillPrice")
                    t212_id = str(resp.get("id", ""))
                    saved_rec = order_repo.find_by_client_order_id(cid)
                    if saved_rec:
                        order_repo.update_fill(saved_rec.id, "filled", fill_price, t212_id)
                    log.info("Filled %s %s qty=%s", signal, t212_ticker, qty)
                    _sb.publish({"type": "order_filled", "ticker": t212_ticker, "side": signal,
                                 "qty": qty, "fill_price": fill_price, "ts": bar_close_iso})
                    fill_price_alert = resp.get("fillPrice") or 0.0
                    if alert_manager is not None:
                        alert_manager.notify(
                            f"Order filled: {signal} {qty:.2f}x {t212_ticker} @ {float(fill_price_alert):.4f}",
                            AlertLevel.INFO,
                        )
                    orders_total.labels(side=signal, status="filled").inc()

                    cooldown_td = timedelta(
                        seconds=_INTERVAL_SECONDS.get(manifest.interval, 86400)
                        * eff_cooldown_bars
                    )

                    if signal == "BUY":
                        pos_repo.upsert(Position(
                            t212_ticker=t212_ticker,
                            quantity=qty,
                            avg_entry=current_price,
                            last_signal_ts=datetime.utcnow(),
                        ))
                        metric_open_positions.set(len(pos_repo.all()))
                        raw_fill = resp.get("fillPrice")
                        entry_price = float(raw_fill) if raw_fill else current_price
                        sl_price = entry_price * (1 - eff_stop_loss_pct)
                        tp_price = entry_price * (1 + eff_take_profit_pct)
                        stop_resp = None
                        try:
                            stop_resp = await asyncio.to_thread(
                                t212.place_stop_order, t212_ticker, qty, sl_price
                            )
                            limit_resp = await asyncio.to_thread(
                                t212.place_limit_order, t212_ticker, qty, tp_price
                            )
                            stop_id = str(stop_resp["id"])
                            limit_id = str(limit_resp["id"])
                            pos_repo.update_oco_ids(t212_ticker, stop_id, limit_id)
                            from alphaTrade.config import ModelOverride
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
                            oco_tasks.add(_task)
                            _task.add_done_callback(oco_tasks.discard)
                            log.info(
                                "OCO submitted for %s: SL=%.4f TP=%.4f",
                                t212_ticker, sl_price, tp_price,
                            )
                        except Exception as exc:
                            log.error("OCO setup failed for %s: %s", t212_ticker, exc)
                            if stop_resp is not None:
                                try:
                                    await asyncio.to_thread(
                                        t212.cancel_order, str(stop_resp["id"])
                                    )
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
                        metric_open_positions.set(len(pos_repo.all()))
                        if pos:
                            from alphaTrade.store.repos import TradeJournalRepo
                            fill_price_raw = resp.get("fillPrice") if isinstance(resp, dict) else None
                            exit_p = float(fill_price_raw) if fill_price_raw else current_price
                            journal_repo = TradeJournalRepo(session)
                            journal_repo.save(build_sell_journal_entry(
                                model_id=manifest.run_name,
                                t212_ticker=t212_ticker,
                                exit_price=exit_p,
                                quantity=qty,
                                position=pos,
                            ))
                            # Update model performance tracking
                            from alphaTrade.config import ModelOverride
                            from alphaTrade.risk.performance import record_trade, check_retirement
                            per_model_ret = settings.model_overrides.get(manifest.run_name, ModelOverride()).retirement
                            _ret_cfg = _effective_config(settings.risk.model_retirement, per_model_ret)
                            pnl_for_perf = (exit_p - pos.avg_entry) * qty
                            record_trade(session, model_id=manifest.run_name,
                                         realized_pnl=pnl_for_perf,
                                         cfg=_ret_cfg)
                            if check_retirement(session, model_id=manifest.run_name,
                                                cfg=_ret_cfg):
                                msg = f"Model {manifest.run_name} auto-retired: performance below threshold"
                                wh.notify("WARNING", msg, category="model-retirement")
                                if alert_manager is not None:
                                    alert_manager.notify(msg, AlertLevel.WARNING)

                except Exception as exc:
                    err_rec = order_repo.find_by_client_order_id(cid)
                    if err_rec:
                        order_repo.update_fill(err_rec.id, "error", None, "")
                    orders_total.labels(side=signal, status="error").inc()
                    log.error("Order failed for %s: %s", t212_ticker, exc)
                    if alert_manager is not None:
                        alert_manager.notify(
                            f"Order error for {t212_ticker}: {exc}",
                            AlertLevel.ERROR,
                        )

        _sb.publish({"type": "tick_complete", "interval": interval, "ts": bar_close_iso})
        health_state.last_tick_at = datetime.now(timezone.utc)

    return tick


async def run(settings: Settings) -> None:
    from alphaTrade.logging_config import configure_logging
    configure_logging(log_file=settings.log_file)

    from alphaTrade.kill_switch import SENTINEL_FILE as _SENTINEL
    Path(_SENTINEL).touch()
    log.info("Trading halted on startup — call POST /resume to begin trading")

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
        wh.notify("INFO", "alphaTrade bot started", category="startup")

    from prometheus_client import start_http_server as _start_metrics
    try:
        _start_metrics(9090)
        log.info("Metrics server listening on :9090")
    except OSError as exc:
        log.error("Metrics server failed to start on :9090: %s", exc)

    provider_holder: list = [_build_data_provider(settings)]

    def _t212_from_settings(s) -> "T212Client":
        account = s.t212_active_account or "demo"
        env = "demo" if account == "demo" else "live"
        key_map = {
            "demo": (s.t212_demo_api_key, s.t212_demo_secret_key),
            "invest": (s.t212_invest_api_key, s.t212_invest_secret_key),
            "isa": (s.t212_isa_api_key, s.t212_isa_secret_key),
        }
        api_key, secret_key = key_map.get(account, ("", ""))
        return T212Client(api_key=api_key or "", secret_key=secret_key or "", env=env)

    t212 = _t212_from_settings(settings)
    t212_holder: list = [t212]

    health_state = HealthState()
    _startup_key_map = {
        "demo": settings.t212_demo_api_key,
        "invest": settings.t212_invest_api_key,
        "isa": settings.t212_isa_api_key,
    }
    health_state.t212_configured = bool(
        _startup_key_map.get(settings.t212_active_account or "demo", "")
    )
    try:
        await asyncio.to_thread(t212.get_total_equity)  # probe only; result discarded
        health_state.t212_ok = True
    except Exception as exc:
        log.warning("T212 startup probe failed: %s", exc)
        health_state.t212_ok = False

    engine = get_engine(settings.state_db_path)

    alert_manager = AlertManager(settings.alerts)

    from alphaTrade.model_registry import ModelRegistry
    registry = ModelRegistry(engine=engine)
    await registry.refresh(settings.models_dir, settings.model_overrides)
    if not registry.by_run_name:
        log.error("No models loaded from %s. Exiting.", settings.models_dir)
        return

    health_state.models_loaded = bool(registry.by_run_name)
    health_state.longest_interval_seconds = max(
        (_INTERVAL_SECONDS.get(i, 3600) for i in registry.snapshot_by_interval()),
        default=3600,
    )

    # Reconcile positions with T212 before first tick
    await reconcile_positions(t212_holder[0], settings)

    # Initialize open_positions gauge from reconciled DB state
    with Session(engine) as _session:
        _pos_repo = PositionRepo(_session)
        metric_open_positions.set(len([p for p in _pos_repo.all() if p.quantity > 0]))

    # Re-attach OCO monitors for positions that had active SL/TP orders before restart
    with Session(engine) as _oco_session:
        _oco_pos_repo = PositionRepo(_oco_session)
        for _pos in _oco_pos_repo.all_with_oco():
            if not (_pos.stop_order_id and _pos.limit_order_id):
                continue
            _sl = _pos.avg_entry * (1 - settings.defaults.stop_loss_pct)
            _tp = _pos.avg_entry * (1 + settings.defaults.take_profit_pct)
            _cd = timedelta(seconds=86400)
            _t = asyncio.create_task(monitor_oco(
                t212=t212_holder[0],
                t212_ticker=_pos.t212_ticker,
                stop_order_id=_pos.stop_order_id,
                limit_order_id=_pos.limit_order_id,
                engine=engine,
                cooldown_td=_cd,
                entry_price=_pos.avg_entry,
                sl_price=_sl,
                tp_price=_tp,
                quantity=_pos.quantity,
                model_id="",
                entry_time=_pos.opened_at,
            ))
            _oco_tasks.add(_t)
            _t.add_done_callback(_oco_tasks.discard)
            log.info(
                "Re-attached OCO monitor for %s (stop=%s limit=%s)",
                _pos.t212_ticker, _pos.stop_order_id, _pos.limit_order_id,
            )

    # Build static t212_ticker overrides from overrides.yaml
    static_map: dict[str, str] = {}
    for run_name, override in settings.model_overrides.items():
        if override.t212_ticker:
            manifest_match = next(
                (m for m, _ in registry.by_run_name.values() if m.run_name == run_name), None
            )
            if manifest_match:
                static_map[manifest_match.ticker] = override.t212_ticker

    # Pre-resolve all model tickers to populate instrument cache before tick loop.
    _preresolve_tickers(t212_holder[0], engine, registry, static_map)

    # Snapshot initial intervals to determine which scheduler tasks to spawn.
    # New models on existing intervals are hot-reloaded each tick.
    # Models introducing a new interval require restart.
    by_interval = registry.snapshot_by_interval()

    health_runner = None
    try:
        health_runner = await start_health_server(health_state)
    except Exception as exc:
        log.error("Health server failed to start on :8080: %s", exc)

    backtest_scheduler = None
    try:
        from alphaTrade.scheduler.backtest_scheduler import BacktestScheduler  # deferred: avoids circular import
        models_list = list(registry.by_run_name.values()) if registry else []
        backtest_scheduler = BacktestScheduler(
            engine=engine,
            settings=settings,
            models=models_list,
            models_dir=settings.models_dir,
        )
        backtest_scheduler.start()
        log.info("BacktestScheduler started (enabled=%s)", settings.backtest.schedule_enabled)
    except Exception as exc:
        log.error("BacktestScheduler failed to start: %s", exc)

    api_server = None
    try:
        from alphaTrade.api.app import start_api_server
        api_server = await start_api_server(
            engine, health_state,
            port=settings.api_port,
            registry=registry,
            backtest_scheduler=backtest_scheduler,
        )
    except Exception as exc:
        log.error("API server failed to start on :%d: %s", settings.api_port, exc)

    tasks = [
        asyncio.create_task(
            schedule_bar_close(
                interval,
                make_tick(
                    interval,
                    registry=registry,
                    settings=settings,
                    engine=engine,
                    t212_holder=t212_holder,
                    provider_holder=provider_holder,
                    health_state=health_state,
                    oco_tasks=_oco_tasks,
                    static_map=static_map,
                    alert_manager=alert_manager,
                ),
                stop_event,
                extended_hours=settings.defaults.extended_hours,
            )
        )
        for interval, interval_models in by_interval.items()
    ]

    async def daily_close_callback() -> None:
        """Fires at NYSE close: write PnlSnapshot + send daily summary alert."""
        from datetime import date
        from alphaTrade.store.repos import PnlSnapshotRepo, TradeJournalRepo, PositionRepo, PnlSnapshot, EquityRepo

        today_str = date.today().isoformat()
        try:
            with Session(engine) as session:
                journal_repo = TradeJournalRepo(session)
                pos_repo = PositionRepo(session)
                snapshot_repo = PnlSnapshotRepo(session)
                eq_repo = EquityRepo(session)

                today_trades = journal_repo.today()
                realized_pnl = sum(t.realized_pnl for t in today_trades)
                trade_count = len(today_trades)
                open_positions = pos_repo.all()
                today_open = eq_repo.today_open() or 0.0

                # Compute unrealized P&L from live closing prices for open positions
                unrealized_pnl = 0.0
                inst_repo = InstrumentCacheRepo(session)
                try:
                    import yfinance as yf
                    for _op in open_positions:
                        if _op.quantity <= 0:
                            continue
                        _cache = inst_repo.get_by_t212(_op.t212_ticker)
                        if _cache:
                            _hist = yf.download(_cache.yf_ticker, period="2d", interval="1d", progress=False, auto_adjust=True)
                            if not _hist.empty:
                                _last_close = float(_hist["Close"].iloc[-1])
                                unrealized_pnl += (_last_close - _op.avg_entry) * _op.quantity
                except Exception as _exc:
                    log.warning("Unrealized P&L fetch failed: %s — using 0.0", _exc)

                total_equity = today_open + realized_pnl + unrealized_pnl
                day_pnl_pct = ((realized_pnl + unrealized_pnl) / today_open * 100) if today_open > 0 else 0.0

                snapshot_repo.upsert(PnlSnapshot(
                    date=today_str,
                    total_equity=total_equity,
                    day_pnl=realized_pnl + unrealized_pnl,
                    day_pnl_pct=day_pnl_pct,
                    realized_pnl=realized_pnl,
                    unrealized_pnl=unrealized_pnl,
                    open_positions=len(open_positions),
                    trade_count=trade_count,
                ))

                summary = (
                    f"Daily summary {today_str}: "
                    f"realized P&L={realized_pnl:+.2f} ({day_pnl_pct:+.2f}%), "
                    f"{trade_count} trade(s) closed, "
                    f"{len(open_positions)} position(s) open"
                )
                log.info(summary)
                alert_manager.notify(summary, AlertLevel.INFO)

        except Exception as exc:
            log.error("daily_close_callback failed: %s", exc)

    tasks.append(asyncio.create_task(
        schedule_bar_close("1d", daily_close_callback, stop_event=stop_event)
    ))

    log.info("Scheduler running. Intervals: %s", list(by_interval.keys()))
    await asyncio.gather(*tasks)

    # Drain OCO monitor tasks before exit
    for task in list(_oco_tasks):
        task.cancel()
    if _oco_tasks:
        await asyncio.gather(*_oco_tasks, return_exceptions=True)
    if backtest_scheduler is not None:
        backtest_scheduler.shutdown()
    if api_server is not None:
        api_server.should_exit = True
    if health_runner is not None:
        await health_runner.cleanup()
    alert_manager.shutdown(timeout=5.0)
    log.info("Graceful shutdown complete.")
