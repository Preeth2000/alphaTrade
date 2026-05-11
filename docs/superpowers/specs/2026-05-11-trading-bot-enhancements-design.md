# alphaLink Enhancement Design: Backtester, Risk, Operational

**Date:** 2026-05-11  
**Status:** Approved  
**Scope:** Dry-run backtester, risk enhancements (A/B/C), alerts, trade journaling, P&L reporting, daily summary webhooks

---

## 1. Overview

Six capability areas added to alphaLink. All follow existing DB-centric (Approach A) pattern — no new abstractions, no separate processes. New features plug in as CLI subcommands, post-tick hooks, or gate extensions. Existing `adapter/`, `consensus/`, `broker/`, and `main.py` tick loop structure are untouched.

---

## 2. Data Layer

New SQLite tables (via Alembic migration):

| Table | Purpose |
|---|---|
| `backtest_runs` | Backtester run metadata: params, date range, summary metrics |
| `backtest_trades` | Per-trade simulated fills within a backtest run |
| `pnl_snapshots` | End-of-day P&L snapshot: realized, unrealized, per-position JSON blob |
| `trade_journal` | Enriched per-trade record written on position close |
| `model_performance` | Rolling per-model metrics, retirement flag |
| `sector_cache` | Ticker → GICS sector, populated at startup alongside `instrument_cache` |

Existing tables (`Signal`, `Order`, `Position`, `EquityCurve`, `InstrumentCache`) unchanged. New tables join on `ticker` and `model_id`.

### trade_journal schema

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `model_id` | str | manifest model identifier |
| `ticker` | str | |
| `signal` | str | BUY \| SELL |
| `entry_price` | float | |
| `exit_price` | float | |
| `quantity` | float | |
| `entry_time` | datetime | |
| `exit_time` | datetime | |
| `hold_bars` | int | |
| `exit_reason` | str | OCO_SL \| OCO_TP \| SIGNAL_SELL \| HALT \| MANUAL |
| `sl_price` | float | set at entry |
| `tp_price` | float | set at entry |
| `realized_pnl` | float | USD |
| `pnl_pct` | float | |

---

## 3. Dry-Run Backtester

### Entry point

```bash
alphalink backtest --start 2024-01-01 --end 2024-12-31 --models-dir ./models
```

### Design

- Loads model artifacts via existing `ModelRegistry` + manifest loader
- Fetches full OHLCV history for each ticker from configured provider (yfinance/Polygon)
- Walks forward bar-by-bar — strict no-lookahead: each bar sees only data up to that point
- Runs identical pipeline: `adapter/features` → `normalize` → `window` → ONNX inference → `consensus`
- Runs identical risk gates against simulated equity and simulated positions
- Simulated fills at next-bar open (conservative)
- Simulated SL/TP as price-level checks on subsequent bars
- Writes to `backtest_runs` + `backtest_trades`

### Config (overrides.yaml)

```yaml
backtest:
  slippage_bps: 5
  commission_per_trade: 0
  initial_equity: 10000
```

### CLI output

```
Backtest: 2024-01-01 → 2024-12-31 | 4 models | 12 tickers
Total return:   +14.3%
Max drawdown:   -6.2%
Win rate:       58.4%
Sharpe (proxy): 1.41
Trades:         247

Per-model:
  model_a  +9.1%  win_rate=62%
  model_b  +3.8%  win_rate=54%
  ...
```

Full results written to `backtest_runs` + `backtest_trades` for UI consumption.

### New files

- `alphalink/backtest/engine.py` — bar-by-bar simulation harness
- `alphalink/backtest/reporter.py` — summary stats + DB writer

---

## 4. Risk Enhancement A: Model Performance Tracking & Auto-Retirement

### Design

Post-OCO-close and post-SELL, realized P&L attributed to triggering model and written to `model_performance`. Rolling window recalculated each tick.

`ModelRegistry` skips models where `model_performance.retired = true`. Retirement reversed when new artifact deployed (manifest hash changes).

### Config

```yaml
risk:
  model_retirement:
    enabled: true
    lookback_trades: 20
    min_win_rate: 0.4
    min_rolling_pnl: -500
    auto_reload: true
```

### New files

- `alphalink/risk/performance.py` — rolling metrics calculator + retirement logic

### Changes

- `alphalink/risk/gates.py` — add retirement check gate
- `alphalink/model_registry.py` — skip retired models

---

## 5. Risk Enhancement B: Sector / Correlation Limits

### Design

Sector (GICS) fetched via yfinance on instrument pre-resolve, stored in `sector_cache`. Pre-BUY gate checks current sector exposure against limits.

Two modes controlled by `portfolio_mode`:

**balanced** — auto-caps each sector equally as percentage of portfolio equity.

**unbalanced** — manual integer cap per sector, with optional per-sector overrides.

### Config

```yaml
risk:
  portfolio_mode: balanced     # balanced | unbalanced

  balanced:
    max_sector_pct: 0.33

  unbalanced:
    max_per_sector: 3
    sector_overrides:
      technology: 5
      healthcare: 2
```

### Sector fetch failure

If yfinance sector lookup fails: log warning, skip sector gate for that ticker (trade proceeds), alert fires.

### New files

- `alphalink/risk/sector.py` — sector lookup, exposure calculator, gate check

### Changes

- `alphalink/risk/gates.py` — add sector gate
- `alphalink/store/repos.py` — `SectorCache` repo
- `alphalink/broker/instrument_map.py` — populate `sector_cache` at pre-resolve

---

## 6. Risk Enhancement C: Volatility-Based Sizing

### Design

Replaces fixed `size_pct` with dynamic calculation. Three modes:

**fixed** (existing behaviour, default)

**atr** — `quantity = (equity × risk_pct) / (ATR × atr_multiplier)`. ATR reused from `adapter/features.py` — no extra fetch.

**vix** — `size_pct` scaled inversely with VIX level. VIX fetched via yfinance (`^VIX`) once per day, cached in memory.

### Config

```yaml
risk:
  sizing_mode: atr             # fixed | atr | vix

  atr:
    risk_pct: 0.01
    atr_multiplier: 2.0

  vix:
    base_size_pct: 0.05
    vix_scalar: 20
```

### VIX fetch failure

Fall back to `fixed` sizing for that tick. Alert fires.

### Changes

- `alphalink/risk/sizing.py` — extend with `atr` and `vix` modes

---

## 7. Alerts: Slack + Email

### Design

`notify/alerting.py` — new module alongside existing `notify/webhook.py`. Same non-blocking bounded queue pattern. Registered at startup, called wherever existing webhook notifier is called.

### Config

```yaml
alerts:
  slack:
    enabled: true
    webhook_url: "${SLACK_WEBHOOK_URL}"
    min_level: warning         # debug | info | warning | critical

  email:
    enabled: true
    smtp_host: "${SMTP_HOST}"
    smtp_port: 587
    smtp_user: "${SMTP_USER}"
    smtp_password: "${SMTP_PASSWORD}"
    to: ["${ALERT_EMAIL_TO}"]
    min_level: critical
```

All values from env vars or overrides.yaml. No hardcoded addresses.

### Alert triggers

| Event | Level |
|---|---|
| Halt triggered | critical |
| Halt resumed | info |
| Model retired | warning |
| OCO leg filled | info |
| Daily loss at 80% of limit | warning |
| VIX/sector fetch failure | warning |
| Daily summary | info |

### New files

- `alphalink/notify/alerting.py` — Slack + email dispatchers, queue wrapper

---

## 8. Trade Journaling

`trade_journal` row written on every position close. Populated by:
- `broker/oco_monitor.py` on OCO fill (knows SL/TP hit, entry price from DB)
- `broker/orders.py` on SELL signal execution
- `main.py` halt handler for HALT exits

Exit reason enum: `OCO_SL | OCO_TP | SIGNAL_SELL | HALT | MANUAL`

---

## 9. Structured P&L Reports to DB

`pnl_snapshots` written at market close (NYSE calendar trigger in `scheduler/bar_close.py`):

- `date`, `total_equity`, `day_pnl`, `day_pnl_pct`
- `realized_pnl` (sum of `trade_journal.realized_pnl` for today)
- `unrealized_pnl` (open positions mark-to-market at close price)
- `positions_json` (per-position breakdown, parsed by UI)

---

## 10. Daily Summary Webhook

Fires at NYSE market close. Sent to all enabled notifiers (existing webhooks + Slack + email).

Payload:
```json
{
  "date": "2026-05-11",
  "equity": 10543.21,
  "day_pnl": 43.21,
  "day_pnl_pct": 0.41,
  "trades_closed": 3,
  "signals_generated": 12,
  "models_active": 4,
  "models_retired_today": 0,
  "top_winner": {"ticker": "AAPL", "pnl": 28.50},
  "top_loser": {"ticker": "MSFT", "pnl": -12.30}
}
```

---

## 11. Performance Attribution Export

```bash
alphalink report --format json|csv --since 2024-01-01
```

Queries `trade_journal` + `model_performance`. Output per model and per ticker:
- Win rate, trade count, avg P&L, total P&L, Sharpe proxy (annualised Sortino if enough data)

UI can call CLI or query DB directly.

### Changes

- `alphalink/cli.py` — add `report` subcommand

---

## 12. New Module Summary

```
alphalink/
  backtest/
    __init__.py
    engine.py
    reporter.py
  risk/
    sizing.py          # extend: atr + vix modes
    gates.py           # extend: sector gate + retirement check
    sector.py          # new
    performance.py     # new
  notify/
    alerting.py        # new
  store/
    repos.py           # extend: new table repos
  cli.py               # extend: backtest + report subcommands
  scheduler/
    bar_close.py       # extend: daily summary + pnl snapshot trigger
```

**Unchanged:** `adapter/`, `consensus/`, `broker/t212_client.py`, `main.py` tick loop structure.

---

## 13. Error Handling

| Failure | Behaviour |
|---|---|
| Sector fetch fails | Skip sector gate for ticker, alert fires, trade proceeds |
| VIX fetch fails | Fall back to `fixed` sizing, alert fires |
| Model retired | Webhook + alert, model skipped next tick |
| Backtest data gap | Log + skip bar, flag in run summary |
| SMTP/Slack delivery fails | Log warning, non-blocking (same pattern as webhook) |

---

## 14. Testing

- **Unit:** each new gate (sector, retirement), sizing modes (ATR, VIX), alert formatters, journal writer, P&L snapshot calculator
- **Integration:** backtest engine against fixture OHLCV in `tests/integration/fixtures/`
- **Daily summary:** freeze time to NYSE close, assert webhook + Slack payload shape
- **Retirement:** simulate N losing trades, assert model flagged + skipped next tick
- **Sector:** mock yfinance sector response, assert balanced/unbalanced gate blocks/allows correctly
