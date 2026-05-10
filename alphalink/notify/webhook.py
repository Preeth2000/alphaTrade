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
