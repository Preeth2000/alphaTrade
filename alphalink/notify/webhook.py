from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

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
    host = urlparse(_webhook_url).hostname or ""
    payload: dict[str, str] = (
        {"text": text} if host.endswith("slack.com") else {"content": text}
    )
    try:
        resp = httpx.post(_webhook_url, json=payload, timeout=5)
        if resp.status_code >= 400:
            log.warning("Webhook delivery non-2xx: %s", resp.status_code)
    except Exception as exc:
        log.warning("Webhook delivery failed: %s", exc)


class WebhookHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        category: str = getattr(record, "category", record.name)
        try:
            msg = self.format(record)
            notify(record.levelname, msg, category)
        except Exception:
            self.handleError(record)
