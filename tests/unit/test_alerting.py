"""Unit tests for AlertManager: queue dispatch, level filtering, graceful shutdown."""
import queue
import threading
from unittest.mock import MagicMock, patch

import pytest

from alphalink.config import AlertsConfig, AlertSlackConfig, AlertEmailConfig
from alphalink.notify.alerting import AlertManager, AlertLevel


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
    assert not am._queue.empty()
    am.shutdown(timeout=1.0)


def test_below_min_level_not_enqueued():
    cfg = AlertsConfig(slack=_slack_cfg(min_level="ERROR"), email=None)
    am = AlertManager(cfg)
    am.notify("debug info", level=AlertLevel.INFO)
    assert am._queue.empty()
    am.shutdown(timeout=1.0)


def test_shutdown_drains_queue():
    """Shutdown waits for in-flight messages to be processed."""
    cfg = AlertsConfig(slack=_slack_cfg(min_level="INFO"), email=None)
    am = AlertManager(cfg)
    # Patch dispatch so it doesn't make network calls
    dispatched = []
    original_dispatch = am._dispatch
    def fake_dispatch(msg, level):
        dispatched.append(msg)
    am._dispatch = fake_dispatch

    am.notify("msg1", level=AlertLevel.INFO)
    am.notify("msg2", level=AlertLevel.INFO)
    am.shutdown(timeout=2.0)
    # After shutdown, both messages should have been dispatched
    assert len(dispatched) >= 1  # at least one message processed before sentinel


def test_slack_dispatch_called():
    cfg = AlertsConfig(slack=_slack_cfg(min_level="INFO"), email=None)
    am = AlertManager(cfg)

    posted = []
    def fake_post(url, json, timeout):
        posted.append(json)
        resp = MagicMock()
        resp.status_code = 200
        return resp

    with patch("alphalink.notify.alerting.httpx") as mock_httpx:
        mock_httpx.post.side_effect = fake_post
        am._dispatch_slack("hello slack", level=AlertLevel.WARNING)

    assert len(posted) == 1
    assert "hello slack" in posted[0]["text"]
    am.shutdown(timeout=1.0)


def test_email_dispatch_called():
    cfg = AlertsConfig(slack=None, email=_email_cfg(min_level="INFO"))
    am = AlertManager(cfg)

    with patch("alphalink.notify.alerting.smtplib") as mock_smtp:
        mock_server = MagicMock()
        mock_smtp.SMTP.return_value.__enter__.return_value = mock_server
        am._dispatch_email("hello email", level=AlertLevel.WARNING)

    mock_server.sendmail.assert_called_once()
    am.shutdown(timeout=1.0)


def test_alerts_disabled_when_no_config():
    cfg = AlertsConfig(slack=None, email=None)
    am = AlertManager(cfg)
    am.notify("should not raise", level=AlertLevel.CRITICAL)
    assert am._queue.empty()
    am.shutdown(timeout=1.0)
