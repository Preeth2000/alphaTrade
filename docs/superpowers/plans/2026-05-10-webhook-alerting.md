# Webhook Alerting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Push critical trading bot events (daily-loss halt, order failures, startup/shutdown) to Discord or Slack via webhook with per-category rate limiting.

**Architecture:** New `alphalink/notify/webhook.py` exposes `configure(url)` + `notify(level, msg, category)`. A `WebhookHandler` logging handler bridges existing `log.*` calls into the webhook. Wire-up happens in `main.py` after `basicConfig`.

**Tech Stack:** Python 3.11, httpx (already in deps), pytest + respx for mocking.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `alphalink/notify/__init__.py` | Package marker |
| Create | `alphalink/notify/webhook.py` | `configure()`, `notify()`, `WebhookHandler` |
| Create | `tests/unit/test_webhook.py` | All unit tests |
| Modify | `alphalink/config.py` | Add `webhook_url`, `webhook_levels` fields |
| Modify | `alphalink/main.py` | Wire handler + explicit `notify()` alert calls |

---

### Task 1: Scaffold notify module with stub and failing tests

**Files:**
- Create: `alphalink/notify/__init__.py`
- Create: `alphalink/notify/webhook.py` (stub)
- Create: `tests/unit/test_webhook.py`

- [ ] **Step 1: Create the package marker**

```python
# alphalink/notify/__init__.py
```

(Empty file — just makes it a package.)

- [ ] **Step 2: Create stub webhook.py**

```python
# alphalink/notify/webhook.py
from __future__ import annotations

import logging
import time

import httpx

_webhook_url: str = ""
_rate_limits: dict[str, float] = {}
_RATE_WINDOW = 30.0

log = logging.getLogger(__name__)


def configure(webhook_url: str) -> None:
    raise NotImplementedError


def notify(level: str, msg: str, category: str = "general") -> None:
    raise NotImplementedError


class WebhookHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        raise NotImplementedError
```

- [ ] **Step 3: Write all unit tests**

```python
# tests/unit/test_webhook.py
from __future__ import annotations

import logging
import time

import pytest
import respx
import httpx

from alphalink.notify import webhook


@pytest.fixture(autouse=True)
def reset_state():
    webhook._webhook_url = ""
    webhook._rate_limits.clear()
    yield
    webhook._webhook_url = ""
    webhook._rate_limits.clear()


DISCORD_URL = "https://discord.com/api/webhooks/123/abc"
SLACK_URL = "https://hooks.slack.com/services/T123/B456/xxx"


# --- configure() ---

def test_configure_sets_webhook_url():
    webhook.configure(DISCORD_URL)
    assert webhook._webhook_url == DISCORD_URL


# --- notify(): no-op ---

def test_notify_noop_when_url_not_configured():
    with respx.mock:
        webhook.notify("WARNING", "test", "cat")
        assert len(respx.calls) == 0


# --- notify(): Discord ---

def test_notify_discord_sends_content_payload():
    webhook.configure(DISCORD_URL)
    with respx.mock(assert_all_called=True) as mock:
        mock.post(DISCORD_URL).mock(return_value=httpx.Response(204))
        webhook.notify("WARNING", "Daily loss halt", "daily-loss-halt")
    payload = mock.calls[0].request.read()
    import json
    body = json.loads(payload)
    assert body == {"content": "[WARNING] Daily loss halt"}


# --- notify(): Slack ---

def test_notify_slack_sends_text_payload():
    webhook.configure(SLACK_URL)
    with respx.mock(assert_all_called=True) as mock:
        mock.post(SLACK_URL).mock(return_value=httpx.Response(200))
        webhook.notify("ERROR", "Order failed", "order-reject")
    import json
    body = json.loads(mock.calls[0].request.read())
    assert body == {"text": "[ERROR] Order failed"}


# --- notify(): unknown URL → Discord format ---

def test_notify_unknown_url_uses_discord_format():
    url = "https://example.com/webhook"
    webhook.configure(url)
    with respx.mock(assert_all_called=True) as mock:
        mock.post(url).mock(return_value=httpx.Response(200))
        webhook.notify("INFO", "msg", "cat")
    import json
    body = json.loads(mock.calls[0].request.read())
    assert "content" in body


# --- notify(): rate limiting ---

def test_rate_limit_suppresses_second_call_same_category():
    webhook.configure(DISCORD_URL)
    with respx.mock() as mock:
        mock.post(DISCORD_URL).mock(return_value=httpx.Response(204))
        webhook.notify("WARNING", "msg1", "daily-loss-halt")
        webhook.notify("WARNING", "msg2", "daily-loss-halt")
    assert len(mock.calls) == 1


def test_rate_limit_different_categories_both_send():
    webhook.configure(DISCORD_URL)
    with respx.mock() as mock:
        mock.post(DISCORD_URL).mock(return_value=httpx.Response(204))
        webhook.notify("WARNING", "msg1", "cat-a")
        webhook.notify("WARNING", "msg2", "cat-b")
    assert len(mock.calls) == 2


def test_rate_limit_allows_send_after_window_elapsed():
    webhook.configure(DISCORD_URL)
    webhook._rate_limits["cat"] = time.monotonic() - 31.0
    with respx.mock() as mock:
        mock.post(DISCORD_URL).mock(return_value=httpx.Response(204))
        webhook.notify("WARNING", "msg", "cat")
    assert len(mock.calls) == 1


# --- notify(): error handling ---

def test_notify_delivery_failure_does_not_raise():
    webhook.configure(DISCORD_URL)
    with respx.mock() as mock:
        mock.post(DISCORD_URL).mock(side_effect=httpx.ConnectError("timeout"))
        # Must not raise
        webhook.notify("WARNING", "msg", "cat")


# --- WebhookHandler ---

def test_handler_uses_category_from_extra():
    webhook.configure(DISCORD_URL)
    handler = webhook.WebhookHandler()
    with respx.mock() as mock:
        mock.post(DISCORD_URL).mock(return_value=httpx.Response(204))
        record = logging.LogRecord(
            name="alphalink.main", level=logging.ERROR,
            pathname="", lineno=0, msg="Order failed for AAPL",
            args=(), exc_info=None,
        )
        record.category = "order-reject"  # type: ignore[attr-defined]
        handler.emit(record)
    assert len(mock.calls) == 1


def test_handler_falls_back_to_logger_name_as_category():
    webhook.configure(DISCORD_URL)
    handler = webhook.WebhookHandler()
    with respx.mock() as mock:
        mock.post(DISCORD_URL).mock(return_value=httpx.Response(204))
        record = logging.LogRecord(
            name="alphalink.broker", level=logging.ERROR,
            pathname="", lineno=0, msg="Some error",
            args=(), exc_info=None,
        )
        # No category on record — should fall back to "alphalink.broker"
        handler.emit(record)
    assert len(mock.calls) == 1
    # Second call for same logger name should be rate-limited
    with respx.mock() as mock2:
        mock2.post(DISCORD_URL).mock(return_value=httpx.Response(204))
        handler.emit(record)
    assert len(mock2.calls) == 0  # rate-limited under "alphalink.broker"
```

- [ ] **Step 4: Run tests — verify they all fail**

```bash
cd /home/preeth/projects/alphaLink
python -m pytest tests/unit/test_webhook.py -v 2>&1 | head -40
```

Expected: all tests `FAILED` or `ERROR` with `NotImplementedError`.

---

### Task 2: Implement `configure()` and `notify()`

**Files:**
- Modify: `alphalink/notify/webhook.py`

- [ ] **Step 1: Replace stubs with real implementation**

Replace the entire content of `alphalink/notify/webhook.py`:

```python
# alphalink/notify/webhook.py
from __future__ import annotations

import logging
import time

import httpx

_webhook_url: str = ""
_rate_limits: dict[str, float] = {}
_RATE_WINDOW = 30.0

log = logging.getLogger(__name__)


def configure(webhook_url: str) -> None:
    global _webhook_url
    _webhook_url = webhook_url


def notify(level: str, msg: str, category: str = "general") -> None:
    if not _webhook_url:
        return
    now = time.monotonic()
    if now - _rate_limits.get(category, 0.0) < _RATE_WINDOW:
        return
    _rate_limits[category] = now
    text = f"[{level}] {msg}"
    payload: dict[str, str] = (
        {"content": text} if "discord.com" in _webhook_url else {"text": text}
    )
    try:
        httpx.post(_webhook_url, json=payload, timeout=5)
    except Exception as exc:
        log.warning("Webhook delivery failed: %s", exc)


class WebhookHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        raise NotImplementedError
```

- [ ] **Step 2: Run notify() tests — verify they pass**

```bash
python -m pytest tests/unit/test_webhook.py -v -k "not handler" 2>&1 | tail -20
```

Expected: all non-handler tests `PASSED`.

- [ ] **Step 3: Commit**

```bash
git add alphalink/notify/__init__.py alphalink/notify/webhook.py tests/unit/test_webhook.py
git commit -m "feat: add notify() and configure() to webhook module"
```

---

### Task 3: Implement `WebhookHandler`

**Files:**
- Modify: `alphalink/notify/webhook.py`

- [ ] **Step 1: Replace the WebhookHandler stub**

Replace only the `WebhookHandler` class in `alphalink/notify/webhook.py`:

```python
class WebhookHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        category: str = getattr(record, "category", record.name)
        try:
            msg = self.format(record)
            notify(record.levelname, msg, category)
        except Exception:
            self.handleError(record)
```

- [ ] **Step 2: Run handler tests — verify they pass**

```bash
python -m pytest tests/unit/test_webhook.py -v -k "handler" 2>&1 | tail -20
```

Expected: both handler tests `PASSED`.

- [ ] **Step 3: Run full test suite — verify no regressions**

```bash
python -m pytest tests/unit/ -v 2>&1 | tail -20
```

Expected: all tests `PASSED`.

- [ ] **Step 4: Commit**

```bash
git add alphalink/notify/webhook.py
git commit -m "feat: add WebhookHandler logging handler"
```

---

### Task 4: Config fields and main.py wire-up

**Files:**
- Modify: `alphalink/config.py:41-56` (Settings class)
- Modify: `alphalink/main.py`

- [ ] **Step 1: Add webhook fields to Settings**

In `alphalink/config.py`, add two fields to the `Settings` class after `overrides_path`:

```python
    webhook_url: str = ""
    webhook_levels: str = "WARNING"
```

The full `Settings` class field block should look like:

```python
    t212_api_key: str
    t212_env: str = "demo"
    data_provider: str = "yfinance"
    polygon_api_key: str = ""
    models_dir: Path = Path("./models")
    state_db_path: Path = Path("./state.db")
    overrides_path: Path = Path("./overrides.yaml")
    webhook_url: str = ""
    webhook_levels: str = "WARNING"
```

- [ ] **Step 2: Add webhook import to main.py**

At the top of `alphalink/main.py`, add after the existing imports:

```python
from alphalink.notify import webhook as wh
```

- [ ] **Step 3: Wire up handler + send startup alert**

In `alphalink/main.py`, find the `run()` function. Replace:

```python
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
```

With:

```python
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if settings.webhook_url:
        wh.configure(settings.webhook_url)
        _wh = wh.WebhookHandler()
        _wh.setLevel(settings.webhook_levels)
        logging.getLogger().addHandler(_wh)
        wh.notify("INFO", "alphaLink bot started", category="startup")
```

- [ ] **Step 4: Add daily-loss-halt explicit alert**

In `alphalink/main.py`, find inside `tick()`:

```python
                if daily_loss_halted:
                    log.warning("Daily loss halt active (%.2f%%). No new orders.", daily_loss_pct * 100)
```

Replace with:

```python
                if daily_loss_halted:
                    log.warning("Daily loss halt active (%.2f%%). No new orders.", daily_loss_pct * 100)
                    wh.notify(
                        "WARNING",
                        f"Daily loss halt active ({daily_loss_pct:.2%}). No new orders.",
                        category="daily-loss-halt",
                    )
```

- [ ] **Step 5: Add reconcile-divergence explicit alert**

In `alphalink/main.py`, inside `reconcile_positions()`, find:

```python
                log.info("Reconcile: removing stale position %s (not in T212 portfolio)", local_pos.t212_ticker)
```

Replace with:

```python
                log.info("Reconcile: removing stale position %s (not in T212 portfolio)", local_pos.t212_ticker)
                wh.notify(
                    "WARNING",
                    f"Reconcile: removing stale position {local_pos.t212_ticker} (not in T212 portfolio)",
                    category="reconcile-divergence",
                )
```

- [ ] **Step 6: Run full test suite**

```bash
python -m pytest tests/unit/ -v 2>&1 | tail -20
```

Expected: all tests `PASSED`.

- [ ] **Step 7: Verify import is clean**

```bash
python -c "from alphalink.notify import webhook; print('OK')"
```

Expected: `OK`

- [ ] **Step 8: Commit**

```bash
git add alphalink/config.py alphalink/main.py
git commit -m "feat: wire WebhookHandler into main loop with startup and alert calls (alphaLink-dac)"
```

> **Note:** Shutdown alert (`category="shutdown"`) is deferred to alphaLink-55n (graceful shutdown / kill switch issue), which will add SIGTERM/SIGINT handling where the `wh.notify("INFO", "alphaLink bot stopped", category="shutdown")` call belongs.
