from __future__ import annotations

import json
import logging
import time

import pytest
import respx
import httpx

from alphaTrade.notify import webhook


@pytest.fixture(autouse=True)
def reset_state():
    webhook._webhook_url = ""
    webhook._rate_limits.clear()
    yield
    webhook._drain()
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
    with respx.mock() as mock:
        webhook.notify("WARNING", "test", "cat")
        webhook._drain()
        assert len(mock.calls) == 0


# --- notify(): Discord ---

def test_notify_discord_sends_content_payload():
    webhook.configure(DISCORD_URL)
    with respx.mock(assert_all_called=True) as mock:
        mock.post(DISCORD_URL).mock(return_value=httpx.Response(204))
        webhook.notify("WARNING", "Daily loss halt", "daily-loss-halt")
        webhook._drain()
        payload = mock.calls[0].request.read()
        body = json.loads(payload)
        assert body == {"content": "[WARNING] Daily loss halt"}


# --- notify(): Slack ---

def test_notify_slack_sends_text_payload():
    webhook.configure(SLACK_URL)
    with respx.mock(assert_all_called=True) as mock:
        mock.post(SLACK_URL).mock(return_value=httpx.Response(200))
        webhook.notify("ERROR", "Order failed", "order-reject")
        webhook._drain()
        body = json.loads(mock.calls[0].request.read())
        assert body == {"text": "[ERROR] Order failed"}


# --- notify(): unknown URL → Discord format ---

def test_notify_unknown_url_uses_discord_format():
    url = "https://example.com/webhook"
    webhook.configure(url)
    with respx.mock(assert_all_called=True) as mock:
        mock.post(url).mock(return_value=httpx.Response(200))
        webhook.notify("INFO", "msg", "cat")
        webhook._drain()
        body = json.loads(mock.calls[0].request.read())
        assert "content" in body


# --- notify(): rate limiting ---

def test_rate_limit_suppresses_second_call_same_category():
    webhook.configure(DISCORD_URL)
    with respx.mock() as mock:
        mock.post(DISCORD_URL).mock(return_value=httpx.Response(204))
        webhook.notify("WARNING", "msg1", "daily-loss-halt")
        webhook.notify("WARNING", "msg2", "daily-loss-halt")
        webhook._drain()
        assert len(mock.calls) == 1


def test_rate_limit_different_categories_both_send():
    webhook.configure(DISCORD_URL)
    with respx.mock() as mock:
        mock.post(DISCORD_URL).mock(return_value=httpx.Response(204))
        webhook.notify("WARNING", "msg1", "cat-a")
        webhook.notify("WARNING", "msg2", "cat-b")
        webhook._drain()
        assert len(mock.calls) == 2


def test_rate_limit_allows_send_after_window_elapsed(monkeypatch):
    webhook.configure(DISCORD_URL)
    monkeypatch.setattr(webhook, "_RATE_WINDOW", 0.0)
    with respx.mock() as mock:
        mock.post(DISCORD_URL).mock(return_value=httpx.Response(204))
        webhook.notify("WARNING", "msg1", "cat")
        webhook.notify("WARNING", "msg2", "cat")
        webhook._drain()
        assert len(mock.calls) == 2


# --- notify(): error handling ---

def test_notify_delivery_failure_does_not_raise():
    webhook.configure(DISCORD_URL)
    with respx.mock() as mock:
        mock.post(DISCORD_URL).mock(side_effect=httpx.ConnectError("timeout"))
        # Must not raise
        webhook.notify("WARNING", "msg", "cat")
        webhook._drain()


def test_notify_non_2xx_response_does_not_raise():
    webhook.configure(DISCORD_URL)
    with respx.mock() as mock:
        mock.post(DISCORD_URL).mock(return_value=httpx.Response(429))
        # Must not raise even on 429
        webhook.notify("WARNING", "msg", "cat")
        webhook._drain()


# --- WebhookHandler ---

def test_handler_uses_category_from_extra():
    webhook.configure(DISCORD_URL)
    handler = webhook.WebhookHandler()
    with respx.mock() as mock:
        mock.post(DISCORD_URL).mock(return_value=httpx.Response(204))
        record = logging.LogRecord(
            name="alphaTrade.main", level=logging.ERROR,
            pathname="", lineno=0, msg="Order failed for AAPL",
            args=(), exc_info=None,
        )
        record.category = "order-reject"  # type: ignore[attr-defined]
        handler.emit(record)
        webhook._drain()
        assert len(mock.calls) == 1


def test_handler_falls_back_to_logger_name_as_category():
    webhook.configure(DISCORD_URL)
    handler = webhook.WebhookHandler()
    with respx.mock() as mock:
        mock.post(DISCORD_URL).mock(return_value=httpx.Response(204))
        record = logging.LogRecord(
            name="alphaTrade.broker", level=logging.ERROR,
            pathname="", lineno=0, msg="Some error",
            args=(), exc_info=None,
        )
        # No category on record — should fall back to "alphaTrade.broker"
        handler.emit(record)
        webhook._drain()
        assert len(mock.calls) == 1
    # Second call for same logger name should be rate-limited
    with respx.mock() as mock2:
        # Don't set up a route since the request should be rate-limited
        handler.emit(record)
        webhook._drain()
        assert len(mock2.calls) == 0  # rate-limited under "alphaTrade.broker"
