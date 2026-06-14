"""Bot entrypoint: load config + artifacts, wire components, start scheduler."""
from __future__ import annotations

import asyncio
import logging
import os as _os
import signal
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from alphaTrade.store.repos import TradeJournal

# third-party
from sqlmodel import Session

# first-party (alphabetical)
from alphaTrade.adapter.features import compute_features
from alphaTrade.adapter.inference import OnnxModel
from alphaTrade.adapter.manifest import Manifest
from alphaTrade.adapter.normalize import normalize
from alphaTrade.adapter.window import build_input
from alphaTrade.broker.async_broker import AsyncBroker
from alphaTrade.broker.instrument_map import InstrumentMap
from alphaTrade.broker.oco_monitor import monitor_oco
from alphaTrade.broker.order_queue import OrderRequest, OrderResult
from alphaTrade.broker.orders import make_client_order_id
from alphaTrade.broker.t212_client import T212Client
from alphaTrade.broker.throttle import EndpointThrottle
from alphaTrade.config import Settings
from alphaTrade.consensus.softmax_avg import consensus_by_ticker
from alphaTrade.data.provider import DataProvider
from alphaTrade.notify import webhook as wh
from alphaTrade.notify.alerting import AlertManager, AlertLevel
from alphaTrade.risk.gates import GateResult, run_gates
from alphaTrade.risk.performance import _effective_config, check_retirement, record_trade
from alphaTrade.risk.sizing import compute_quantity
from alphaTrade.kill_switch import is_halted
from alphaTrade.scheduler.bar_close import schedule_bar_close
from alphaTrade.store.db import get_engine, url_from_settings
from alphaTrade.store.model_sync import ModelSyncDaemon
from alphaTrade.store.repos import (
    BacktestRepo,
    BotSettings,
    BotSettingsRepo,
    EquityRepo,
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
from alphaTrade.data.provider_verify import verify_provider_credentials
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

_SECRETS_SOURCE = _os.environ.get("SECRETS_SOURCE", "db")

_INTERVAL_SECONDS: dict[str, int] = {
    "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400, "1wk": 604800,
}


def _t212_credentials(db_s) -> tuple[str, str, str]:
    """Return (api_key, secret_key, env) for the active T212 account."""
    account = db_s.t212_active_account or "demo"
    env = "demo" if account == "demo" else "live"
    if _SECRETS_SOURCE == "alphakey":
        from alphaTrade.broker.alphakey_client import get_secret
        user_id = _os.environ["ALPHAKEY_USER_ID"]
        api_key = get_secret(user_id, "t212", account, "api_key")
        secret_key = get_secret(user_id, "t212", account, "secret_key")
        return api_key, secret_key, env
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
) -> bool:
    """Returns True if the data provider was rebuilt (caller should re-probe health)."""
    from alphaTrade.broker.t212_client import T212Client, _BASE_URLS
    new_key, new_secret, new_env = _t212_credentials(db_s)
    current_auth = getattr(t212_holder[0], "_auth", None)
    current_key = getattr(current_auth, "username", None) if current_auth else getattr(t212_holder[0], "_headers", {}).get("Authorization")
    current_base = getattr(t212_holder[0], "_base", None)
    new_base = _BASE_URLS.get(new_env)
    if new_key and (current_key != new_key or current_base != new_base):
        t212_holder[0] = T212Client(api_key=new_key, secret_key=new_secret, env=new_env)
        log.info("Hot-reload: T212Client reinitialised (account=%s, env=%s)", db_s.t212_active_account, new_env)
    _provider_rebuilt = False
    if _SECRETS_SOURCE == "alphakey":
        from alphaTrade.broker.alphakey_client import get_secret
        user_id = _os.environ["ALPHAKEY_USER_ID"]
        vault_polygon_key = get_secret(user_id, "polygon", "default", "api_key")
        if vault_polygon_key and vault_polygon_key != settings.polygon_api_key:
            settings.polygon_api_key = vault_polygon_key
            provider_holder[0] = _build_data_provider(settings)
            _provider_rebuilt = True
    _key_changed = (
        _SECRETS_SOURCE == "db"
        and bool(db_s.polygon_api_key)
        and db_s.polygon_api_key != settings.polygon_api_key
    )
    _prov_changed = bool(db_s.data_provider) and db_s.data_provider != settings.data_provider
    if _key_changed:
        settings.polygon_api_key = db_s.polygon_api_key
    if _prov_changed:
        settings.data_provider = db_s.data_provider
    if _key_changed or _prov_changed:
        provider_holder[0] = _build_data_provider(settings)
        _provider_rebuilt = True
        if _prov_changed:
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
    if db_s.safe_mode is not None:
        settings.risk.safe_mode = db_s.safe_mode
    if db_s.dangerously_allow_pyramid is not None:
        settings.risk.dangerously_allow_pyramid = db_s.dangerously_allow_pyramid
    settings.alerts.slack.enabled = db_s.slack_enabled  # type: ignore[union-attr]
    if db_s.slack_webhook_url:
        settings.alerts.slack.webhook_url = db_s.slack_webhook_url  # type: ignore[union-attr]
    settings.alerts.email.enabled = db_s.email_enabled  # type: ignore[union-attr]
    if db_s.email_to_addrs:
        settings.alerts.email.to_addrs = [  # type: ignore[union-attr]
            a.strip() for a in db_s.email_to_addrs.split(",") if a.strip()
        ]
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
        settings.risk.unbalanced.sector_overrides = db_s.unbalanced_sector_overrides
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
    return _provider_rebuilt


def _build_data_provider(settings: Settings) -> DataProvider:
    from alphaTrade.data.factory import build_data_provider
    return build_data_provider(settings)


def _precompute_passthrough_features(df, feature_names: list[str]):
    """Pre-compute cumulative VWAP over the full df before any window slicing.

    VWAP is a cumulative feature in training (alphaGen computes cumsum over the entire
    fetch). Restarting the cumsum inside each window slice produces different values.
    Pre-computing here preserves the correct distribution when window slices are taken later.
    """
    import numpy as np
    df = df.copy()
    if "VWAP" in feature_names and "VWAP" not in df.columns:
        tp = (df["High"].values + df["Low"].values + df["Close"].values) / 3.0
        vol = df["Volume"].values.astype(float)
        cum_tpv = np.cumsum(tp * vol)
        cum_vol = np.cumsum(vol)
        df["VWAP"] = np.where(cum_vol == 0, np.nan, cum_tpv / cum_vol)
    if "Transactions" in feature_names and "Transactions" not in df.columns:
        df["Transactions"] = np.nan
    return df


def scan_models(models_dir: Path) -> list[tuple[Manifest, OnnxModel]]:
    if not models_dir.exists():
        log.error("models_dir does not exist: %s", models_dir)
        return []
    results = []
    for run_dir in sorted(models_dir.iterdir()):
        if not run_dir.is_dir():
            continue
        if run_dir.name.startswith("."):
            continue
        manifest_path = run_dir / "manifest.json"
        model_path = run_dir / "model.onnx"
        if not manifest_path.exists() or not model_path.exists():
            log.error(
                "Skipping %s (in %s): missing manifest.json or model.onnx",
                run_dir.name, models_dir,
            )
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
    engine = get_engine(url_from_settings(settings))
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
    throttle: "EndpointThrottle | None" = None,
    broker: "AsyncBroker | None" = None,
):
    """Return an async tick coroutine for the given interval.

    All T212Client HTTP calls run via asyncio.to_thread so the event loop
    stays responsive to health checks and OCO monitor tasks during latency spikes.
    """
    from alphaTrade.api import stream_bus as _sb
    # Edge-triggered alert state — survives across ticks within the closure.
    _prev_halt: bool = False
    _prev_halt_warn: bool = False
    _prev_kill_switch: bool = False
    async def tick() -> None:
        nonlocal _prev_halt, _prev_halt_warn, _prev_kill_switch
        with Session(engine) as _hs:
            _db_s = BotSettingsRepo(_hs).get()
        if _db_s is not None:
            _provider_rebuilt = apply_bot_settings(_db_s, settings, t212_holder, provider_holder)
            if _provider_rebuilt:
                # Credential check — updates the connection badge.
                _cred_ok, _cred_result = await asyncio.to_thread(verify_provider_credentials, settings)
                health_state.provider_ok = _cred_ok
                health_state.provider_name = settings.data_provider
                if _cred_ok:
                    log.info("Provider credential re-check OK after key/provider change (%s)", settings.data_provider)
                else:
                    log.warning("Provider credential re-check failed after key/provider change (%s): %s",
                                settings.data_provider, _cred_result.get("error", "unknown"))
                # Data availability probe — updates the trading gate.
                try:
                    await asyncio.to_thread(provider_holder[0].health_probe)
                    health_state.provider_data_ok = True
                    health_state.provider_data_error = None
                    log.info("Provider data re-probe OK after key/provider change (%s)", settings.data_provider)
                except Exception as _probe_exc:
                    health_state.provider_data_ok = False
                    health_state.provider_data_error = str(_probe_exc)
                    log.warning("Provider data re-probe failed after key/provider change (%s): %s",
                                settings.data_provider, _probe_exc)
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
        ticker_manifests: dict[str, list[Manifest]] = defaultdict(list)
        ticker_atr: dict[str, float] = {}

        _any_fetch_ok = False
        _last_fetch_error: str | None = None

        for manifest, model in interval_models:
            try:
                t0 = time.perf_counter()
                # Request enough bars for VWAP history to approximate training distribution.
                # Providers cap at their lookback limit so large values just return max available.
                vwap_bars = 3000 if "VWAP" in manifest.feature_names else manifest.window + 100
                df = provider_holder[0].fetch_ohlcv(manifest.ticker, manifest.interval, vwap_bars)
                _any_fetch_ok = True  # fetch succeeded; mark before inference steps
                from alphaTrade.data.fundamentals import merge_fundamentals_into
                df = merge_fundamentals_into(df, manifest.ticker, manifest.feature_names)
                df = _precompute_passthrough_features(df, manifest.feature_names)
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
                ticker_manifests[manifest.ticker].append(manifest)
            except Exception as exc:
                inference_errors_total.labels(run_name=manifest.run_name).inc()
                log.error("Inference error for %s: %s", manifest.run_name, exc)
                if not _any_fetch_ok:  # track last data error only if no success yet
                    _last_fetch_error = str(exc)

        # Self-heal provider_data_ok from real inference fetches — no extra API calls.
        if _any_fetch_ok:
            health_state.provider_data_ok = True
            health_state.provider_data_error = None
        elif _last_fetch_error is not None:
            health_state.provider_data_ok = False
            health_state.provider_data_error = _last_fetch_error
        # If neither: no fetches attempted this tick (no models) — leave existing value.

        signals = consensus_by_ticker(ticker_logits)
        kill_switch_active = is_halted()
        if kill_switch_active:
            log.warning("Kill switch active — signals will be logged but orders skipped")
            if alert_manager is not None and not _prev_kill_switch:
                alert_manager.notify("Kill switch engaged — orders halted", AlertLevel.CRITICAL)
        _prev_kill_switch = kill_switch_active

        # Fresh session per tick — no long-lived session across bar closes
        with Session(engine) as session:
            inst_cache = InstrumentCacheRepo(session)
            instrument_map = InstrumentMap(t212, inst_cache, static_map, sector_repo=SectorCacheRepo(session))
            pos_repo = PositionRepo(session)
            eq_repo = EquityRepo(session)
            signal_repo = SignalRepo(session)
            order_repo = OrderRepo(session)

            try:
                if throttle is not None:
                    await throttle.acquire("account_cash")
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
            daily_loss_warn = (
                not daily_loss_halted
                and daily_loss_pct <= -settings.risk.daily_loss_halt_pct * 0.8
            )
            if daily_loss_halted and not _prev_halt:
                log.warning("Daily loss halt triggered (%.2f%%). No new orders.", daily_loss_pct * 100)
                wh.notify(
                    "WARNING",
                    f"Daily loss halt triggered ({daily_loss_pct:.2%}). No new orders.",
                    category="daily-loss-halt",
                )
                if alert_manager is not None:
                    alert_manager.notify(
                        f"Daily loss halt triggered: {abs(daily_loss_pct * today_open):.2f} / {settings.risk.daily_loss_halt_pct * today_open:.2f}",
                        AlertLevel.WARNING,
                    )
            if daily_loss_warn and not _prev_halt_warn:
                log.warning("Daily loss at 80%% of halt threshold (%.2f%%).", daily_loss_pct * 100)
                if alert_manager is not None:
                    alert_manager.notify(
                        f"Daily loss at 80% of halt threshold: {abs(daily_loss_pct * today_open):.2f} / {settings.risk.daily_loss_halt_pct * today_open:.2f}",
                        AlertLevel.WARNING,
                    )
            _prev_halt = daily_loss_halted
            _prev_halt_warn = daily_loss_warn

            current_vix: float | None = None
            if settings.risk.sizing_mode == "vix":
                current_vix = await asyncio.to_thread(provider_holder[0].fetch_vix)
                if current_vix is None:
                    log.warning("VIX fetch failed; compute_quantity will use internal fallback")

            for yf_ticker, signal in signals.items():
                manifests_for_ticker = ticker_manifests[yf_ticker]
                # Sort by run_name for deterministic primary selection
                manifests_for_ticker = sorted(manifests_for_ticker, key=lambda m: m.run_name)
                manifest = manifests_for_ticker[0]
                multi_model = len(manifests_for_ticker) > 1
                if multi_model:
                    log.info(
                        "Multi-model consensus for %s (%d models): attributing to primary %s",
                        yf_ticker, len(manifests_for_ticker), manifest.run_name,
                    )

                yaml_ov = settings.model_overrides.get(manifest.run_name)
                db_ov = db_overrides.get(manifest.run_name)
                # Per-model overrides only apply when a single model owns the ticker;
                # multi-model consensus falls back to global defaults to avoid ambiguity.
                if multi_model:
                    eff_size_pct = settings.defaults.size_pct
                    eff_stop_loss_pct = settings.defaults.stop_loss_pct
                    eff_take_profit_pct = settings.defaults.take_profit_pct
                else:
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
                    raw_json=[v.tolist() for v in ticker_logits[yf_ticker]],
                )
                signal_repo.save(sig_rec)
                signals_total.labels(ticker=yf_ticker, signal=signal).inc()
                log.info("Signal %s → %s", yf_ticker, signal)
                _sb.publish({"type": "signal_fired", "ticker": yf_ticker, "signal": signal,
                             "run_name": manifest.run_name, "ts": bar_close_iso})

                if kill_switch_active:
                    log.info("Kill switch: order skipped for %s %s", signal, yf_ticker)
                    continue

                eff_safe_mode = next(
                    (v for v in [
                        db_ov.safe_mode if db_ov else None,
                        yaml_ov.safe_mode if yaml_ov else None,
                        settings.risk.safe_mode,
                    ] if v is not None),
                    True,
                )
                eff_dangerously_allow_pyramid = (
                    (db_ov.dangerously_allow_pyramid if db_ov and db_ov.dangerously_allow_pyramid is not None else None)
                    or (yaml_ov.dangerously_allow_pyramid if yaml_ov and yaml_ov.dangerously_allow_pyramid is not None else None)
                    or settings.risk.dangerously_allow_pyramid
                )

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
                    interval=manifest.interval,
                    safe_mode=eff_safe_mode,
                    dangerously_allow_pyramid=eff_dangerously_allow_pyramid,
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

                if signal == "SELL":
                    _held = pos_repo.get(t212_ticker)
                    qty = float(_held.quantity) if _held else 0.0
                else:
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

                # DB idempotency: skip if already recorded for this bar
                existing = order_repo.find_by_client_order_id(cid)
                if existing:
                    log.info("Duplicate order skipped (cid=%s)", cid)
                    orders_total.labels(side=signal, status="skipped_duplicate").inc()
                    continue

                # Record intent before enqueue so crash between enqueue and drain is detectable
                rec = Order(
                    t212_ticker=t212_ticker,
                    side=signal,
                    quantity=qty,
                    client_order_id=cid,
                )
                order_repo.save(rec)

                if broker is not None:
                    req = OrderRequest(
                        t212_ticker=t212_ticker,
                        side=signal,
                        quantity=qty,
                        client_order_id=cid,
                        interval=manifest.interval,
                        signal_ts=datetime.utcnow(),
                        yf_ticker=yf_ticker,
                        stop_loss_pct=eff_stop_loss_pct,
                        take_profit_pct=eff_take_profit_pct,
                        entry_price=current_price,
                        run_name=manifest.run_name,
                        bar_close_iso=bar_close_iso,
                    )
                    broker.enqueue(req)
                    log.info("Enqueued %s %s qty=%s", signal, t212_ticker, qty)
                    orders_total.labels(side=signal, status="enqueued").inc()
                    _sb.publish({"type": "order_enqueued", "ticker": t212_ticker,
                                 "side": signal, "qty": qty, "ts": bar_close_iso})

        _sb.publish({"type": "tick_complete", "interval": interval, "ts": bar_close_iso})
        health_state.last_tick_at = datetime.now(timezone.utc)

    return tick


async def run(settings: Settings) -> None:
    import os as _os
    from alphaTrade.telemetry import setup_telemetry as _setup_telemetry
    _setup_telemetry(
        service_name=_os.environ.get("OTEL_SERVICE_NAME", "alphatrade"),
        otlp_endpoint=_os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"),
    )
    from alphaTrade.logging_config import configure_logging
    configure_logging(log_file=settings.log_file)

    from alphaTrade.kill_switch import SENTINEL_FILE as _SENTINEL
    Path(_SENTINEL).touch()
    log.info("Trading halted on startup — call POST /resume to begin trading")

    _oco_tasks: set[asyncio.Task] = set()
    _oco_task_by_ticker: dict[str, asyncio.Task] = {}
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

    throttle = EndpointThrottle.from_t212_config(settings.executors.trading212.throttle)
    broker = AsyncBroker(
        t212=t212,
        throttle=throttle,
        stale_window_multiplier=settings.risk.order_stale_window_multiplier,
        max_queue_depth=settings.risk.order_queue_max_depth,
        t212_holder=t212_holder,
    )

    async def _on_order_fill(result: OrderResult) -> None:
        from sqlmodel import Session as _Session
        from alphaTrade.store.repos import (
            OrderRepo, PositionRepo, TradeJournalRepo, Position,
        )
        from alphaTrade.notify.alerting import AlertLevel
        from alphaTrade.config import ModelOverride
        from alphaTrade.metrics import open_positions as metric_open_positions

        req = result.request
        orders_total.labels(side=req.side, status=result.status).inc()

        if result.status not in ("filled",):
            if result.status == "stale_dropped":
                log.info("Order stale-dropped: %s %s", req.side, req.t212_ticker)
            elif result.status == "failed":
                log.error("Order failed: %s %s — %s", req.side, req.t212_ticker, result.error)
                if alert_manager is not None:
                    alert_manager.notify(
                        f"Order error for {req.t212_ticker}: {result.error}",
                        AlertLevel.ERROR,
                    )
            return

        fill_price = result.fill_price or req.entry_price

        with _Session(engine) as session:
            order_repo = OrderRepo(session)
            pos_repo = PositionRepo(session)

            saved = order_repo.find_by_client_order_id(req.client_order_id)
            if saved:
                order_repo.update_fill(saved.id, "filled", fill_price, result.t212_order_id)  # type: ignore[arg-type]

            cooldown_secs = _INTERVAL_SECONDS.get(req.interval, 86400) * settings.defaults.cooldown_bars
            cooldown_td = timedelta(seconds=cooldown_secs)

            if req.side == "BUY":
                sl_price = fill_price * (1 - req.stop_loss_pct)
                tp_price = fill_price * (1 + req.take_profit_pct)

                existing_pos = pos_repo.get(req.t212_ticker)
                if existing_pos and existing_pos.quantity > 0:
                    # Accumulate: volume-weighted avg_entry for pyramid fills
                    total_qty = existing_pos.quantity + req.quantity
                    new_avg = (
                        (existing_pos.avg_entry * existing_pos.quantity + fill_price * req.quantity)
                        / total_qty
                    )
                    pos_repo.upsert(Position(
                        t212_ticker=req.t212_ticker,
                        quantity=total_qty,
                        avg_entry=new_avg,
                        opened_at=existing_pos.opened_at,
                        last_signal_ts=datetime.utcnow(),
                        sl_price=sl_price,
                        tp_price=tp_price,
                        model_id=req.run_name,
                        interval=req.interval,
                        cooldown_secs=int(cooldown_secs),
                    ))
                else:
                    pos_repo.upsert(Position(
                        t212_ticker=req.t212_ticker,
                        quantity=req.quantity,
                        avg_entry=fill_price,
                        last_signal_ts=datetime.utcnow(),
                        sl_price=sl_price,
                        tp_price=tp_price,
                        model_id=req.run_name,
                        interval=req.interval,
                        cooldown_secs=int(cooldown_secs),
                    ))
                metric_open_positions.set(len(pos_repo.all()))

                if result.stop_order_id and result.limit_order_id:
                    pos_repo.update_oco_ids(req.t212_ticker, result.stop_order_id, result.limit_order_id)

                yaml_ov = settings.model_overrides.get(req.run_name, ModelOverride())
                per_model_ret = yaml_ov.retirement
                _ret_cfg = _effective_config(settings.risk.model_retirement, per_model_ret)

                if result.stop_order_id and result.limit_order_id:
                    _task = asyncio.create_task(monitor_oco(
                        t212=t212_holder[0],
                        t212_ticker=req.t212_ticker,
                        stop_order_id=result.stop_order_id,
                        limit_order_id=result.limit_order_id,
                        engine=engine,
                        cooldown_td=cooldown_td,
                        entry_price=fill_price,
                        sl_price=sl_price,
                        tp_price=tp_price,
                        quantity=req.quantity,
                        model_id=req.run_name,
                        entry_time=result.submitted_at or datetime.utcnow(),
                        retirement_cfg=_ret_cfg,
                    ))
                    _oco_tasks.add(_task)
                    _oco_task_by_ticker[req.t212_ticker] = _task
                    def _on_oco_done(t, ticker=req.t212_ticker):
                        _oco_tasks.discard(t)
                        _oco_task_by_ticker.pop(ticker, None)
                    _task.add_done_callback(_on_oco_done)

                if alert_manager is not None:
                    alert_manager.notify(
                        f"Order filled: BUY {req.quantity:.2f}x {req.t212_ticker} @ {fill_price:.4f}",
                        AlertLevel.INFO,
                    )

            elif req.side == "SELL":
                pos = pos_repo.get(req.t212_ticker)

                # Cancel any active OCO monitor task and broker legs for this ticker
                _oco_task = _oco_task_by_ticker.pop(req.t212_ticker, None)
                if _oco_task is not None:
                    _oco_task.cancel()
                    _oco_tasks.discard(_oco_task)
                if pos and pos.stop_order_id:
                    try:
                        await asyncio.to_thread(t212_holder[0].cancel_order, pos.stop_order_id)
                    except Exception as _ce:
                        log.warning("Could not cancel stop leg %s on SELL: %s", pos.stop_order_id, _ce)
                if pos and pos.limit_order_id:
                    try:
                        await asyncio.to_thread(t212_holder[0].cancel_order, pos.limit_order_id)
                    except Exception as _ce:
                        log.warning("Could not cancel limit leg %s on SELL: %s", pos.limit_order_id, _ce)

                pos_repo.remove(req.t212_ticker)
                pos_repo.upsert(Position(
                    t212_ticker=req.t212_ticker,
                    quantity=0,
                    avg_entry=0,
                    cooldown_until_ts=datetime.utcnow() + cooldown_td,
                ))
                metric_open_positions.set(len(pos_repo.all()))

                if pos:
                    journal_repo = TradeJournalRepo(session)
                    journal_repo.save(build_sell_journal_entry(
                        model_id=req.run_name,
                        t212_ticker=req.t212_ticker,
                        exit_price=fill_price,
                        quantity=req.quantity,
                        position=pos,
                    ))
                    yaml_ov = settings.model_overrides.get(req.run_name, ModelOverride())
                    per_model_ret = yaml_ov.retirement
                    _ret_cfg = _effective_config(settings.risk.model_retirement, per_model_ret)
                    pnl = (fill_price - pos.avg_entry) * req.quantity
                    try:
                        record_trade(session, model_id=req.run_name, realized_pnl=pnl, cfg=_ret_cfg)
                        if check_retirement(session, model_id=req.run_name, cfg=_ret_cfg):
                            msg = f"Model {req.run_name} auto-retired"
                            wh.notify("WARNING", msg, category="model-retirement")
                            if alert_manager is not None:
                                alert_manager.notify(msg, AlertLevel.WARNING)
                    except Exception as perf_exc:
                        log.warning("Retirement tracking failed: %s", perf_exc)

                if alert_manager is not None:
                    alert_manager.notify(
                        f"Order filled: SELL {req.quantity:.2f}x {req.t212_ticker} @ {fill_price:.4f}",
                        AlertLevel.INFO,
                    )

    broker.set_callback(_on_order_fill)

    health_state = HealthState()

    engine = get_engine(url_from_settings(settings))

    from alphaTrade.cache.redis_client import create_redis
    from alphaTrade.api import stream_bus as _stream_bus
    _redis_client = await create_redis(settings.redis)
    _stream_bus.configure(_redis_client)

    # Apply DB-persisted settings BEFORE the health probe so the probe uses DB credentials.
    with Session(engine) as _s0:
        _startup_db_s = BotSettingsRepo(_s0).get()
    if _startup_db_s is not None:
        apply_bot_settings(_startup_db_s, settings, t212_holder, provider_holder)
        # return value intentionally ignored — startup probes below cover provider re-check
        log.info("Startup: DB settings applied (data_provider=%s)", settings.data_provider)
        _startup_creds = _t212_credentials(_startup_db_s)
        health_state.t212_configured = bool(_startup_creds[0])
    else:
        health_state.t212_configured = bool(
            {
                "demo": settings.t212_demo_api_key,
                "invest": settings.t212_invest_api_key,
                "isa": settings.t212_isa_api_key,
            }.get(settings.t212_active_account or "demo", "")
        )
    try:
        await asyncio.to_thread(t212_holder[0].get_total_equity)  # probe only; result discarded
        health_state.t212_ok = True
    except Exception as exc:
        log.warning("T212 startup probe failed: %s", exc)
        health_state.t212_ok = False

    # Credential check — same lightweight call as the account-page 'Connected' badge.
    ok, _cred_result = await asyncio.to_thread(verify_provider_credentials, settings)
    health_state.provider_ok = ok
    health_state.provider_name = settings.data_provider
    if ok:
        log.info("Data provider credentials OK (%s)", settings.data_provider)
    else:
        log.warning("Data provider credentials check failed (%s): %s",
                    settings.data_provider, _cred_result.get("error", "unknown"))

    # Data availability probe — cheap narrow-window call via health_probe().
    # Failures are non-fatal (don't prevent startup); self-heals on first tick.
    try:
        await asyncio.to_thread(provider_holder[0].health_probe)
        health_state.provider_data_ok = True
        health_state.provider_data_error = None
        log.info("Data provider fetch probe OK (%s)", settings.data_provider)
    except Exception as exc:
        health_state.provider_data_ok = False
        health_state.provider_data_error = str(exc)
        log.warning("Data provider fetch probe failed (%s): %s", settings.data_provider, exc)

    alert_manager = AlertManager(settings.alerts)

    from alphaTrade.model_registry import ModelRegistry
    registry = ModelRegistry(engine=engine)
    await registry.refresh(settings.models_dir, settings.model_overrides)
    if not registry.by_run_name:
        if settings.model_sync.enabled:
            log.warning(
                "No models loaded from %s — model_sync daemon will deliver models when available.",
                settings.models_dir,
            )
        else:
            log.warning(
                "No models loaded from %s and model_sync is disabled. "
                "Bot will not trade until models are available. UI remains accessible.",
                settings.models_dir,
            )

    health_state.models_loaded = bool(registry.by_run_name)
    health_state.longest_interval_seconds = max(
        (_INTERVAL_SECONDS.get(i, 3600) for i in registry.snapshot_by_interval()),
        default=3600,
    )

    # Reconcile positions with T212 before first tick
    await reconcile_positions(t212_holder[0], settings)

    await broker.start_drain()
    log.info("AsyncBroker drain task started")

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
            # Use stored entry-time prices; fall back to current defaults only if not persisted
            _sl = _pos.sl_price if _pos.sl_price else _pos.avg_entry * (1 - settings.defaults.stop_loss_pct)
            _tp = _pos.tp_price if _pos.tp_price else _pos.avg_entry * (1 + settings.defaults.take_profit_pct)
            _cd_secs = _pos.cooldown_secs if _pos.cooldown_secs else 86400
            _cd = timedelta(seconds=_cd_secs)
            _model_id = _pos.model_id or ""
            if not _model_id:
                log.warning(
                    "Re-attaching OCO for %s with no model_id — journal/retirement tracking unavailable",
                    _pos.t212_ticker,
                )
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
                model_id=_model_id,
                entry_time=_pos.opened_at,
            ))
            _oco_tasks.add(_t)
            _oco_task_by_ticker[_pos.t212_ticker] = _t
            def _on_reattach_done(t, ticker=_pos.t212_ticker):
                _oco_tasks.discard(t)
                _oco_task_by_ticker.pop(ticker, None)
            _t.add_done_callback(_on_reattach_done)
            log.info(
                "Re-attached OCO monitor for %s (stop=%s limit=%s model=%s sl=%.4f tp=%.4f cd=%ds)",
                _pos.t212_ticker, _pos.stop_order_id, _pos.limit_order_id,
                _model_id, _sl, _tp, _cd_secs,
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

    by_interval = registry.snapshot_by_interval()

    health_runner = None
    try:
        health_runner = await start_health_server(health_state)
    except Exception as exc:
        log.error("Health server failed to start on :8080: %s", exc)

    with Session(engine) as _btr_s:
        _interrupted = BacktestRepo(_btr_s).reset_interrupted()
    if _interrupted:
        log.warning("Startup: reset %d interrupted backtest run(s) to failed", _interrupted)

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
            settings=settings,
            t212_holder=t212_holder,
            provider_holder=provider_holder,
        )
    except Exception as exc:
        log.error("API server failed to start on :%d: %s", settings.api_port, exc)

    _ALL_INTERVALS = list(_INTERVAL_SECONDS.keys())
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
                    throttle=throttle,
                    broker=broker,
                ),
                stop_event,
                extended_hours=settings.defaults.extended_hours,
            )
        )
        for interval in _ALL_INTERVALS
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
                _provider = provider_holder[0]
                for _op in open_positions:
                    if _op.quantity <= 0:
                        continue
                    _cache = inst_repo.get_by_t212(_op.t212_ticker)
                    if _cache:
                        try:
                            _df = await asyncio.to_thread(
                                _provider.fetch_ohlcv, _cache.yf_ticker, "1d", 2
                            )
                            if not _df.empty:
                                _last_close = float(_df["Close"].iloc[-1])
                                unrealized_pnl += (_last_close - _op.avg_entry) * _op.quantity
                        except Exception as _exc:
                            log.warning("Unrealized P&L fetch failed for %s: %s", _op.t212_ticker, _exc)

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

    model_sync_daemon: Optional[ModelSyncDaemon] = None
    if settings.model_sync.enabled:
        from sqlmodel import Session as _SyncSession
        def _sync_session_factory():
            return _SyncSession(engine)
        async def _on_promote(promoted: list[str]) -> None:
            with Session(engine) as _s:
                db_ovs = {r.run_name: r for r in ModelOverrideRepo(_s).all()}
            await registry.refresh(
                settings.models_dir,
                _merge_overrides(settings.model_overrides, db_ovs),
            )
            log.info("model_sync: registry refreshed after promoting %s", promoted)
        model_sync_daemon = ModelSyncDaemon(
            sync_cfg=settings.model_sync,
            models_dir=settings.models_dir,
            session_factory=_sync_session_factory,
            redis_client=_redis_client,
            on_promote=_on_promote,
        )
        tasks.append(asyncio.create_task(model_sync_daemon.run(stop_event=stop_event)))
        log.info("model_sync: daemon enabled, polling MLflow every %ds", settings.model_sync.poll_interval)
    else:
        log.info("model_sync: daemon disabled (MODEL_SYNC__ENABLED=false)")

    log.info("Scheduler running. Active intervals at boot: %s. All intervals pre-spawned: %s", list(by_interval.keys()), _ALL_INTERVALS)
    await asyncio.gather(*tasks)

    await broker.stop_drain()
    log.info("AsyncBroker drain task stopped")

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
    await _stream_bus.shutdown()
    if _redis_client is not None:
        await _redis_client.aclose()
    alert_manager.shutdown(timeout=5.0)
    log.info("Graceful shutdown complete.")
