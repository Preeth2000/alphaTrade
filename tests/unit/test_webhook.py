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
