# DB-First Overrides Design

**Date:** 2026-05-20

## Problem

Runtime config changes via UI are inconsistent:
- Model overrides (enabled, size_pct, etc.) → DB only ✓
- Global retirement config → DB ✓ AND YAML (redundant write)
- Per-model retirement overrides → YAML only (via `_persist_retirement_overrides`)
- Per-model backtest schedule overrides → YAML only
- Risk sizing/portfolio/backtest global config → YAML only (no DB equivalent)
- `_merge_overrides` only merges `enabled` from DB, ignores all other ModelOverrideRecord fields (bug)

YAML writes destroy comments. Changes to most fields don't survive restart.

## Decision

**YAML = read-only startup seed. DB = authoritative runtime store. DB wins over YAML for any field set.**

YAML is never written to at runtime. All UI changes go to DB only.

## Architecture

### Load Order (startup)
1. Pydantic loads `.env` → `Settings`
2. `_load_overrides` reads YAML → seeds `settings.defaults`, `settings.risk`, `settings.model_overrides`, `settings.alerts`, `settings.backtest`, `settings.executors`
3. `apply_bot_settings(BotSettings)` applies DB values on top → DB wins
4. `_merge_overrides(yaml_overrides, db_overrides)` builds final per-model overrides for registry

### Per-tick (already works this way)
- `apply_bot_settings` re-applied each tick from DB
- `_merge_overrides` re-applied each tick from DB

## Components Changed

### Migration 0013 — `model_override` table
Add columns: `retirement_enabled`, `retirement_lookback_trades`, `retirement_min_win_rate`, `retirement_min_rolling_pnl`, `retirement_min_trades_before_evaluation`, `retirement_min_evaluation_period`, `backtest_disabled`, `backtest_cron`, `backtest_lookback_days`

### Migration 0014 — `botsettings` table
Add columns for missing fields:
- Risk: `sizing_mode`, `portfolio_mode`, `order_stale_window_multiplier`, `order_queue_max_depth`
- Balanced: `balanced_max_sector_pct`
- Unbalanced: `unbalanced_max_per_sector`, `unbalanced_sector_overrides` (JSON string)
- ATR: `atr_risk_pct`, `atr_multiplier`
- VIX: `vix_base_size_pct`, `vix_scalar`, `vix_max_size_pct`
- Backtest: `backtest_slippage_bps`, `backtest_commission_per_trade`, `backtest_initial_equity`, `backtest_default_size_pct`, `backtest_sl_pct`, `backtest_tp_pct`, `backtest_schedule_enabled`, `backtest_cron`, `backtest_lookback_days`, `backtest_simulate_oco_lag`, `backtest_oco_stop_gap_secs`, `backtest_oco_limit_gap_secs`

### `store/repos.py`
- Add new fields to `BotSettings` SQLModel
- Add new fields to `ModelOverrideRecord` SQLModel

### `main.py` — `apply_bot_settings`
Extend to apply all new `BotSettings` fields to `settings`:
- `settings.risk.sizing_mode`, `portfolio_mode`, `order_stale_window_multiplier`, `order_queue_max_depth`
- `settings.risk.balanced.*`, `settings.risk.unbalanced.*`
- `settings.risk.atr.*`, `settings.risk.vix.*`
- `settings.backtest.*`

### `main.py` — `_merge_overrides`
Fix to merge ALL `ModelOverrideRecord` fields (not just `enabled`):
- size_pct, sl_pct, tp_pct, cooldown_bars, safe_mode, dangerously_allow_pyramid, broker_ticker
- retirement sub-override fields
- backtest sub-override fields

### `api/routers/retirement.py`
- `patch_per_model`: write to `ModelOverrideRecord` via session instead of mutating `settings.model_overrides` + calling `_persist_retirement_overrides`
- `delete_per_model`: clear retirement fields on `ModelOverrideRecord` instead of YAML
- `_per_model_response`: read from DB `ModelOverrideRecord` instead of `settings.model_overrides`
- Remove `_persist_retirement_overrides`, `_yaml_lock`
- Global retirement (`patch_global_config`): already writes to DB ✓, just remove any yaml side-effects (none present — already clean)

### Settings router (`api/routers/settings.py`)
- Extend to expose new BotSettings fields via existing PATCH endpoint

## Error Handling
- All DB writes use existing session/repo pattern — same error surface as current code
- Startup: if DB row absent, YAML values stand (existing behaviour preserved)
- No migration downgrade support (consistent with migrations 0009+)

## Testing
- Existing unit tests for `_merge_overrides` need updating to cover all fields
- Retirement router tests need updating — session dep required for per-model endpoints
