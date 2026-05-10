# Webhook Alerting Design

**Date:** 2026-05-10  
**Issue:** alphaLink-dac  
**Status:** Approved

## Overview

Push critical trading bot events to Discord or Slack via webhook. Operators get immediate visibility into halts, failures, and lifecycle events without watching logs.

## Architecture

### New module: `alphalink/notify/webhook.py`

**`notify(level: str, msg: str, category: str = "general") -> None`**
- Reads `WEBHOOK_URL` from settings; no-ops if unset
- Checks `_rate_limits: dict[str, float]` — skips if category sent within 30s
- Auto-detects platform from URL:
  - `discord.com` → `POST {"content": "[LEVEL] msg"}`
  - `hooks.slack.com` → `POST {"text": "[LEVEL] msg"}`
  - Unknown → tries Discord format
- On HTTP error: logs warning locally, does not raise

**`WebhookHandler(logging.Handler)`**
- `emit()` extracts `category` from `record.category` (set via `extra={"category": ...}`) else falls back to `record.name` (logger name)
- Calls `notify(record.levelname, formatted_msg, category)`
- Attached to root logger only if `webhook_url` is set

### Config additions (`config.py` → `Settings`)

```python
webhook_url: str = ""
webhook_levels: str = "WARNING"  # minimum log level for WebhookHandler
```

Both read from env: `WEBHOOK_URL`, `WEBHOOK_LEVELS`.

### Wire-up (`main.py` → `run()`)

After `logging.basicConfig(...)`:
```python
if settings.webhook_url:
    handler = WebhookHandler(settings)
    handler.setLevel(settings.webhook_levels)
    logging.getLogger().addHandler(handler)
```

## Alert Sites

| Event | Mechanism | Category |
|---|---|---|
| Bot startup | explicit `notify()` | `startup` |
| Bot shutdown (SIGTERM/SIGINT) | explicit `notify()` | `shutdown` |
| Daily-loss halt active | explicit `notify()` | `daily-loss-halt` |
| Reconcile divergence | explicit `notify()` | `reconcile-divergence` |
| Order reject / fill error | `WebhookHandler` catches `log.error` | `alphalink.main` |
| Kill switch flip | explicit `notify()` (when implemented) | `kill-switch` |
| General ERROR+ | `WebhookHandler` auto-catches | logger name |

## Rate Limiting

- Module-level `_rate_limits: dict[str, float]` maps category → last sent epoch
- `notify()` skips send if `time.monotonic() - last < 30.0`
- Updates `_rate_limits[category]` on successful send
- Thread-safe enough for single-process bot (GIL sufficient; no async required)

## Data Flow

```
log.error("Order failed", ...) 
    → WebhookHandler.emit()
    → notify("ERROR", msg, category="alphalink.main")
    → rate limit check
    → POST to WEBHOOK_URL

notify("WARNING", "Daily loss halt", category="daily-loss-halt")  # explicit call
    → rate limit check
    → POST to WEBHOOK_URL
```

## Error Handling

- `notify()` wraps POST in try/except — webhook failure never crashes bot
- Logs warning locally on webhook error: `"Webhook delivery failed: {exc}"`
- Rate limit still updates on send attempt to prevent retry flood on bad URL

## Testing

- Unit: mock `requests.post`; assert payload format for Discord and Slack URLs
- Unit: assert rate limit suppresses second call within 30s, sends after 30s
- Unit: assert `WebhookHandler` extracts `category` from `extra` correctly
- No integration tests against live webhook URLs
