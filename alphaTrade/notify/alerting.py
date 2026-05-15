"""Non-blocking Slack + email alerts. Same bounded-queue pattern as webhook.py."""
from __future__ import annotations

import logging
import queue
import smtplib
import threading
from email.mime.text import MIMEText
from enum import IntEnum
from typing import Optional

try:
    import httpx as _httpx_module
    httpx = _httpx_module
except ImportError:
    httpx = None  # type: ignore[assignment]

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
        try:
            self._queue.put_nowait(_SENTINEL)
        except queue.Full:
            log.warning("alert queue full during shutdown — sentinel not delivered")
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
            try:
                item = self._queue.get()
                if item is _SENTINEL:
                    return
                message, level = item
                self._dispatch(message, level)
            except Exception as exc:
                log.warning("alert worker error: %s", exc)

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
        if email_cfg is None:
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
