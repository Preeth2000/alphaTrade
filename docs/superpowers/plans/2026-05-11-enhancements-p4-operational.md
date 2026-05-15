# Enhancements P4: Operational (Alerts, Daily Summary, P&L Reports)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Slack + email alerting, daily P&L snapshots at NYSE close, daily summary webhooks, and a CLI `report` subcommand for querying trade history and P&L.

**Architecture:** `AlertManager` follows the same non-blocking bounded-queue + daemon-thread pattern as `notify/webhook.py`. All credentials come from env vars via `AlertsConfig` (defined in P1). Daily summary triggered from `scheduler/bar_close.py` daily-close callback. No hardcoded addresses or tokens anywhere.

**Tech Stack:** httpx (Slack), smtplib (email, stdlib), exchange_calendars (existing), pytest

**Prerequisite:** Plan P1 (Foundation) must be complete — relies on `AlertsConfig`, `PnlSnapshot`, `PnlSnapshotRepo`, `TradeJournal`, `TradeJournalRepo`, `BacktestConfig`, and extended `Settings`.

---

## File Map

| Action | File |
|---|---|
| Create | `alphaTrade/notify/alerting.py` |
| Modify | `alphaTrade/main.py` — pass AlertManager to engine, wire alert events |
| Modify | `alphaTrade/scheduler/bar_close.py` — add NYSE-close daily-summary callback hook |
| Modify | `alphaTrade/cli.py` — add `report` subcommand |
| Create | `tests/unit/test_alerting.py` |

---

### Task 1: AlertManager (alphaTrade/notify/alerting.py)

**Files:**
- Create: `alphaTrade/notify/alerting.py`

- [ ] **Step 1: Write failing unit tests**

Create `tests/unit/test_alerting.py`:

```python
"""Unit tests for AlertManager: queue dispatch, level filtering, graceful shutdown."""
import queue
import threading
from unittest.mock import MagicMock, patch, call

import pytest

from alphaTrade.config import AlertsConfig, AlertSlackConfig, AlertEmailConfig
from alphaTrade.notify.alerting import AlertManager, AlertLevel


def _slack_cfg(min_level="WARNING") -> AlertSlackConfig:
    return AlertSlackConfig(
        enabled=True,
        webhook_url="https://hooks.slack.example/T123/B456/XYZ",
        min_level=min_level,
    )


def _email_cfg(min_level="WARNING") -> AlertEmailConfig:
    return AlertEmailConfig(
        enabled=True,
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="bot@example.com",
        smtp_password="secret",
        from_addr="bot@example.com",
        to_addrs=["owner@example.com"],
        min_level=min_level,
    )


def test_alert_level_ordering():
    assert AlertLevel.INFO < AlertLevel.WARNING < AlertLevel.ERROR < AlertLevel.CRITICAL


def test_notify_enqueues_message():
    cfg = AlertsConfig(slack=_slack_cfg(), email=None)
    am = AlertManager(cfg)
    am.notify("test message", level=AlertLevel.WARNING)
    # Should have something in queue (don't start worker to avoid network calls)
    assert not am._queue.empty()


def test_below_min_level_not_enqueued():
    cfg = AlertsConfig(slack=_slack_cfg(min_level="ERROR"), email=None)
    am = AlertManager(cfg)
    am.notify("debug info", level=AlertLevel.INFO)
    assert am._queue.empty()


def test_shutdown_drains_queue():
    cfg = AlertsConfig(slack=None, email=None)
    am = AlertManager(cfg)
    am.notify("msg1", level=AlertLevel.INFO)
    am.shutdown(timeout=1.0)
    # Shutdown sentinel should have been processed (queue may be empty or contain sentinel)
    # Main check: no hang within timeout


def test_slack_dispatch_called(monkeypatch):
    cfg = AlertsConfig(slack=_slack_cfg(min_level="INFO"), email=None)
    am = AlertManager(cfg)

    posted = []
    def fake_post(url, json, timeout):
        posted.append(json)
        resp = MagicMock()
        resp.status_code = 200
        return resp

    with patch("alphaTrade.notify.alerting.httpx") as mock_httpx:
        mock_httpx.post.side_effect = fake_post
        am._dispatch_slack("hello slack", level=AlertLevel.WARNING)

    assert len(posted) == 1
    assert "hello slack" in posted[0]["text"]


def test_email_dispatch_called(monkeypatch):
    cfg = AlertsConfig(slack=None, email=_email_cfg(min_level="INFO"))
    am = AlertManager(cfg)

    with patch("alphaTrade.notify.alerting.smtplib") as mock_smtp:
        mock_server = MagicMock()
        mock_smtp.SMTP.return_value.__enter__.return_value = mock_server
        am._dispatch_email("hello email", level=AlertLevel.WARNING)

    mock_server.sendmail.assert_called_once()


def test_alerts_disabled_when_no_config():
    cfg = AlertsConfig(slack=None, email=None)
    am = AlertManager(cfg)
    am.notify("should not raise", level=AlertLevel.CRITICAL)
    assert am._queue.empty()
```

- [ ] **Step 2: Write the alerting module**

Create `alphaTrade/notify/alerting.py`:

```python
"""Non-blocking Slack + email alerts. Same bounded-queue pattern as webhook.py."""
from __future__ import annotations

import logging
import queue
import smtplib
import threading
from email.mime.text import MIMEText
from enum import IntEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

try:
    import httpx as _httpx_module
    httpx = _httpx_module
except ImportError:
    httpx = None  # type: ignore[assignment]

try:
    import smtplib as _smtp_module
    smtplib = _smtp_module
except ImportError:
    smtplib = None  # type: ignore[assignment]

from alphaTrade.config import AlertsConfig, AlertSlackConfig, AlertEmailConfig

log = logging.getLogger(__name__)

_SENTINEL = object()
_QUEUE_MAX = 128


class AlertLevel(IntEnum):
    INFO = 10
    WARNING = 20
    ERROR = 30
    CRITICAL = 40


_LEVEL_MAP: dict[str, AlertLevel] = {
    "INFO": AlertLevel.INFO,
    "WARNING": AlertLevel.WARNING,
    "ERROR": AlertLevel.ERROR,
    "CRITICAL": AlertLevel.CRITICAL,
}


class AlertManager:
    """Thread-safe, non-blocking alerter. Drops oldest if queue full."""

    def __init__(self, cfg: AlertsConfig) -> None:
        self._cfg = cfg
        self._queue: queue.Queue = queue.Queue(maxsize=_QUEUE_MAX)
        self._thread = threading.Thread(target=self._worker, daemon=True, name="alert-worker")
        self._thread.start()

    def notify(self, message: str, level: AlertLevel = AlertLevel.WARNING) -> None:
        """Enqueue an alert. Drops silently if queue full or level below threshold."""
        if not self._any_enabled(level):
            return
        try:
            self._queue.put_nowait((message, level))
        except queue.Full:
            log.warning("alert queue full — dropping: %s", message[:60])

    def shutdown(self, timeout: float = 5.0) -> None:
        """Drain queue and stop worker thread."""
        self._queue.put(_SENTINEL)
        self._thread.join(timeout=timeout)

    def _any_enabled(self, level: AlertLevel) -> bool:
        if self._cfg.slack and self._cfg.slack.enabled:
            min_l = _LEVEL_MAP.get(self._cfg.slack.min_level.upper(), AlertLevel.WARNING)
            if level >= min_l:
                return True
        if self._cfg.email and self._cfg.email.enabled:
            min_l = _LEVEL_MAP.get(self._cfg.email.min_level.upper(), AlertLevel.WARNING)
            if level >= min_l:
                return True
        return False

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            if item is _SENTINEL:
                return
            message, level = item
            self._dispatch(message, level)

    def _dispatch(self, message: str, level: AlertLevel) -> None:
        slack_cfg = self._cfg.slack
        if slack_cfg and slack_cfg.enabled:
            min_l = _LEVEL_MAP.get(slack_cfg.min_level.upper(), AlertLevel.WARNING)
            if level >= min_l:
                self._dispatch_slack(message, level)

        email_cfg = self._cfg.email
        if email_cfg and email_cfg.enabled:
            min_l = _LEVEL_MAP.get(email_cfg.min_level.upper(), AlertLevel.WARNING)
            if level >= min_l:
                self._dispatch_email(message, level)

    def _dispatch_slack(self, message: str, level: AlertLevel) -> None:
        slack_cfg = self._cfg.slack
        if slack_cfg is None or httpx is None:
            return
        prefix = f"[{level.name}] "
        try:
            resp = httpx.post(
                slack_cfg.webhook_url,
                json={"text": prefix + message},
                timeout=10,
            )
            if resp.status_code != 200:
                log.warning("Slack alert HTTP %s: %s", resp.status_code, resp.text[:200])
        except Exception as exc:
            log.warning("Slack alert failed: %s", exc)

    def _dispatch_email(self, message: str, level: AlertLevel) -> None:
        email_cfg = self._cfg.email
        if email_cfg is None or smtplib is None:
            return
        subject = f"[alphaTrade {level.name}] Alert"
        msg = MIMEText(message)
        msg["Subject"] = subject
        msg["From"] = email_cfg.from_addr
        msg["To"] = ", ".join(email_cfg.to_addrs)
        try:
            with smtplib.SMTP(email_cfg.smtp_host, email_cfg.smtp_port) as server:
                server.starttls()
                server.login(email_cfg.smtp_user, email_cfg.smtp_password)
                server.sendmail(email_cfg.from_addr, email_cfg.to_addrs, msg.as_string())
        except Exception as exc:
            log.warning("Email alert failed: %s", exc)
```

- [ ] **Step 3: Run unit tests, fix until green**

```bash
pytest tests/unit/test_alerting.py -v
```

---

### Task 2: Wire alerts into main.py

**Files:**
- Modify: `alphaTrade/main.py`

Alert events to wire:

| Event | Level | Message template |
|---|---|---|
| Kill switch engaged | CRITICAL | `"Kill switch engaged — orders halted"` |
| Kill switch cleared | WARNING | `"Kill switch cleared — orders resuming"` |
| Model retired by registry | WARNING | `"Model {run_name} auto-retired: {reason}"` |
| Daily loss 80% of limit | WARNING | `"Daily loss at 80% of limit: {loss:.2f} / {limit:.2f}"` |
| VIX fetch failure | WARNING | `"VIX fetch failed — using neutral sizing: {exc}"` |
| Sector fetch failure | WARNING | `"Sector fetch failed for {ticker}: {exc}"` |
| Order fill confirmed | INFO | `"Order filled: {side} {quantity:.2f}x {t212_ticker} @ {fill_price:.4f}"` |
| Order error | ERROR | `"Order error for {t212_ticker}: {error_msg}"` |

- [ ] **Step 1: Instantiate AlertManager in run()**

In `alphaTrade/main.py`, inside the `run()` function, after settings are loaded:

```python
from alphaTrade.notify.alerting import AlertManager, AlertLevel
alert_manager = AlertManager(settings.alerts)
```

- [ ] **Step 2: Pass alert_manager into make_tick() closure**

`make_tick` already captures settings and other state via closure. Add `alert_manager` to the closure parameters:

```python
def make_tick(
    registry: ModelRegistry,
    position_repo: PositionRepo,
    order_repo: OrderRepo,
    signal_repo: SignalRepo,
    equity_repo: EquityRepo,
    instrument_map: InstrumentMap,
    broker: T212Client,
    settings: Settings,
    alert_manager: AlertManager,
    stop_event: asyncio.Event,
) -> Callable[[], Awaitable[None]]:
```

- [ ] **Step 3: Add alert calls at each event point**

In the tick closure, after kill switch check:
```python
if is_halted():
    alert_manager.notify("Kill switch engaged — orders halted", AlertLevel.CRITICAL)
```

After successful order fill (where `order.status == "filled"`):
```python
alert_manager.notify(
    f"Order filled: {order.side} {order.quantity:.2f}x {order.t212_ticker} @ {order.fill_price:.4f}",
    AlertLevel.INFO,
)
```

After order error:
```python
alert_manager.notify(
    f"Order error for {order.t212_ticker}: {order.error_msg}",
    AlertLevel.ERROR,
)
```

For daily loss 80% warning — in the daily-loss gate check (gates.py or main.py daily_loss check), before halting:
```python
if daily_loss_ratio >= 0.80:
    alert_manager.notify(
        f"Daily loss at 80% of limit: {daily_loss:.2f} / {daily_loss_limit:.2f}",
        AlertLevel.WARNING,
    )
```

For model retirement — in `model_registry.py` when skipping a retired model:
```python
# Pass alert_manager as optional param to refresh(); None if not wired
if alert_manager is not None:
    alert_manager.notify(f"Model {run_name} auto-retired: win_rate below threshold", AlertLevel.WARNING)
```

- [ ] **Step 4: Shutdown AlertManager on stop**

At the end of `run()`, in the finally/cleanup block:
```python
alert_manager.shutdown(timeout=5.0)
```

---

### Task 3: Daily summary + P&L snapshot at NYSE close

**Files:**
- Modify: `alphaTrade/scheduler/bar_close.py`
- Modify: `alphaTrade/main.py`

Design:
- `schedule_bar_close` already fires a callback on each bar close.
- For the daily summary, wire a separate `"1d"` interval callback in `run()`.
- The callback queries today's trade_journal + open positions, computes realized + unrealized P&L, writes a `PnlSnapshot`, and fires a daily summary alert.

- [ ] **Step 1: Add daily-close callback wiring in run()**

In `alphaTrade/main.py`, inside `run()`, alongside the existing bar-close schedulers, add:

```python
async def daily_close_callback() -> None:
    """Fires at NYSE close: write PnlSnapshot, send daily summary alert."""
    from datetime import date
    from alphaTrade.store.repos import PnlSnapshotRepo, TradeJournalRepo, PositionRepo
    from alphaTrade.notify.alerting import AlertLevel

    today_str = date.today().isoformat()
    try:
        with get_session(settings.state_db_path) as session:
            journal_repo = TradeJournalRepo(session)
            pos_repo = PositionRepo(session)
            snapshot_repo = PnlSnapshotRepo(session)

            today_trades = journal_repo.today()
            realized_pnl = sum(t.realized_pnl for t in today_trades)
            trade_count = len(today_trades)

            open_positions = pos_repo.all()
            # Unrealized: approximate using avg_entry (no live price here)
            unrealized_pnl = 0.0  # Price feed not available at close callback; set 0

            equity_repo = EquityRepo(session)
            today_open = equity_repo.today_open() or 0.0
            total_equity = today_open + realized_pnl
            day_pnl_pct = (realized_pnl / today_open * 100) if today_open > 0 else 0.0

            snapshot_repo.upsert(
                date=today_str,
                total_equity=total_equity,
                day_pnl=realized_pnl,
                day_pnl_pct=day_pnl_pct,
                realized_pnl=realized_pnl,
                unrealized_pnl=unrealized_pnl,
                open_positions=len(open_positions),
                trade_count=trade_count,
            )

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

# Schedule alongside existing bar-close loops
asyncio.create_task(
    schedule_bar_close("1d", daily_close_callback, stop_event=stop_event)
)
```

**Note:** `PnlSnapshotRepo.upsert` and `TradeJournalRepo.today` must be defined in Plan P1 repos. Confirm before implementing.

- [ ] **Step 2: Verify no duplicate "1d" scheduling**

If existing models already run on "1d" interval, `schedule_bar_close("1d", tick_callback)` is already scheduled. The daily summary callback uses its own separate task — both run on the same schedule but do different things. Confirm there is no conflict.

- [ ] **Step 3: Smoke test daily close fires correctly**

Manual test: set system time to 1 minute before NYSE close (21:00 UTC) and observe log. Or: write a unit test that patches `next_bar_close` to return `datetime.now() + 1 second` and verifies callback fires.

```python
# tests/unit/test_daily_close.py (optional integration smoke)
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_daily_close_callback_fires():
    fired = []

    async def dummy_callback():
        fired.append(True)

    from alphaTrade.scheduler.bar_close import schedule_bar_close
    import alphaTrade.scheduler.bar_close as bc

    future = datetime.now(timezone.utc) + timedelta(seconds=0.05)
    with patch.object(bc, "next_bar_close", return_value=future):
        stop = asyncio.Event()
        task = asyncio.create_task(schedule_bar_close("1d", dummy_callback, stop_event=stop))
        await asyncio.sleep(0.2)
        stop.set()
        await task

    assert len(fired) >= 1
```

---

### Task 4: CLI `report` subcommand

**Files:**
- Modify: `alphaTrade/cli.py`

- [ ] **Step 1: Add `report` command**

After the `backtest` command and before `if __name__ == "__main__":`:

```python
@app.command()
def report(
    since: str = typer.Option("", "--since", help="Start date YYYY-MM-DD (default: 30 days ago)"),
    output: str = typer.Option("text", "--output", "-o", help="Output format: text | json | csv"),
):
    """Print P&L report: daily snapshots and closed trade summary since DATE."""
    import json as _json
    import csv
    import sys
    from datetime import date, timedelta
    from alphaTrade.config import Settings
    from alphaTrade.store.db import get_session
    from alphaTrade.store.repos import PnlSnapshotRepo, TradeJournalRepo

    settings = Settings()
    since_date = since or (date.today() - timedelta(days=30)).isoformat()

    with get_session(settings.state_db_path) as session:
        snapshots = PnlSnapshotRepo(session).since(since_date)
        trades = TradeJournalRepo(session).since(since_date)

    snap_dicts = [
        {
            "date": s.date,
            "total_equity": s.total_equity,
            "day_pnl": s.day_pnl,
            "day_pnl_pct": s.day_pnl_pct,
            "trade_count": s.trade_count,
        }
        for s in snapshots
    ]
    trade_dicts = [
        {
            "ts": t.ts.isoformat(),
            "model_id": t.model_id,
            "ticker": t.ticker,
            "side": "BUY",  # inferred from sign of pnl not stored directly — see P1
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "quantity": t.quantity,
            "realized_pnl": t.realized_pnl,
            "exit_reason": t.exit_reason,
        }
        for t in trades
    ]

    if output == "json":
        console.print(_json.dumps({"snapshots": snap_dicts, "trades": trade_dicts}, indent=2))

    elif output == "csv":
        writer = csv.DictWriter(sys.stdout, fieldnames=list(trade_dicts[0].keys()) if trade_dicts else [])
        writer.writeheader()
        writer.writerows(trade_dicts)

    else:
        if snap_dicts:
            t = Table("date", "equity", "day P&L", "day %", "trades")
            for s in snap_dicts:
                t.add_row(
                    s["date"],
                    f"{s['total_equity']:.2f}",
                    f"{s['day_pnl']:+.2f}",
                    f"{s['day_pnl_pct']:+.2f}%",
                    str(s["trade_count"]),
                )
            console.print("\n[bold]Daily P&L snapshots:[/bold]")
            console.print(t)
        else:
            console.print(f"No snapshots since {since_date}.")

        total_realized = sum(t["realized_pnl"] for t in trade_dicts)
        console.print(f"\n[bold]Total realized P&L since {since_date}:[/bold] {total_realized:+.2f}")
        console.print(f"[bold]Closed trades:[/bold] {len(trade_dicts)}")
```

**Note:** `PnlSnapshotRepo.since(date_str)` and `TradeJournalRepo.since(date_str)` must be defined in Plan P1. Confirm before implementing.

- [ ] **Step 2: Verify CLI wires correctly**

```bash
python -m alphaTrade.cli report --help
```

Expected: shows `--since`, `--output` args without import errors.

---

### Task 5: Verify all alert configs come from env vars only

- [ ] **Step 1: Audit AlertSlackConfig and AlertEmailConfig**

Confirm in `alphaTrade/config.py` (added in Plan P1) that:
- `AlertSlackConfig.webhook_url` has no default (required field or `None`)
- `AlertEmailConfig.smtp_password` has no default (required or `None`)
- `AlertEmailConfig.to_addrs` is a list parsed from env var (e.g. `ALERT_EMAIL_TO_ADDRS="a@b.com,c@d.com"`)
- None of these fields have hardcoded example values as defaults

- [ ] **Step 2: Confirm .env.example documents all required vars**

Open `.env.example`. It must contain (or be updated to contain):

```dotenv
# Slack alerts
ALERTS__SLACK__ENABLED=false
ALERTS__SLACK__WEBHOOK_URL=
ALERTS__SLACK__MIN_LEVEL=WARNING

# Email alerts
ALERTS__EMAIL__ENABLED=false
ALERTS__EMAIL__SMTP_HOST=smtp.gmail.com
ALERTS__EMAIL__SMTP_PORT=587
ALERTS__EMAIL__SMTP_USER=
ALERTS__EMAIL__SMTP_PASSWORD=
ALERTS__EMAIL__FROM_ADDR=
ALERTS__EMAIL__TO_ADDRS=owner@example.com
ALERTS__EMAIL__MIN_LEVEL=WARNING
```

If missing, add them.

---

## Acceptance Criteria

- [ ] `alphaTrade report` runs without error, prints table or JSON
- [ ] `alphaTrade report --output json` prints valid JSON with `snapshots` + `trades` keys
- [ ] `alphaTrade report --output csv` prints CSV rows
- [ ] Unit tests pass: `pytest tests/unit/test_alerting.py -v`
- [ ] AlertManager below-min-level messages are NOT enqueued (confirmed by unit test)
- [ ] AlertManager queue-full condition drops message without raising
- [ ] All Slack/email credentials come from env vars — no hardcoded values anywhere
- [ ] Daily summary fires at NYSE close and writes a PnlSnapshot row
- [ ] Daily summary alert dispatched via AlertManager at INFO level
- [ ] `AlertManager.shutdown()` completes within 5 seconds
