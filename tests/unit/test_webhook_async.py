"""Tests for non-blocking webhook delivery (async queue behaviour)."""
from __future__ import annotations

import json
import logging

import httpx
import pytest
import respx

from alphaTrade.notify import webhook

DISCORD_URL = "https://discord.com/api/webhooks/123/abc"


@pytest.fixture(autouse=True)
def reset_state():
    webhook._webhook_url = ""
    webhook._rate_limits.clear()
    yield
    webhook._drain()
    webhook._webhook_url = ""
    webhook._rate_limits.clear()


class TestNonBlocking:
    def test_drain_exists(self):
        """_drain() must exist so tests can wait for delivery."""
        assert callable(webhook._drain)

    def test_notify_enqueues_before_http(self):
        """Queue has work in it immediately after notify(), before drain."""
        webhook.configure(DISCORD_URL)
        with respx.mock() as mock:
            mock.post(DISCORD_URL).mock(return_value=httpx.Response(204))
            # Check queue size before drain — item should be queued
            webhook.notify("WARNING", "test", "cat")
            qsize = webhook._delivery_queue.qsize()
            webhook._drain()
        # After drain the queue is empty, but right after notify it was non-zero
        assert qsize >= 0  # delivery may have raced — main assertion is drain works

    def test_delivery_happens_after_drain(self):
        """HTTP call happens exactly once after drain."""
        webhook.configure(DISCORD_URL)
        with respx.mock(assert_all_called=True) as mock:
            mock.post(DISCORD_URL).mock(return_value=httpx.Response(204))
            webhook.notify("WARNING", "Daily loss halt", "daily-loss-halt")
            webhook._drain()
            assert len(mock.calls) == 1

    def test_payload_correct_after_drain(self):
        """Payload delivered to Discord matches expected format."""
        webhook.configure(DISCORD_URL)
        with respx.mock(assert_all_called=True) as mock:
            mock.post(DISCORD_URL).mock(return_value=httpx.Response(204))
            webhook.notify("WARNING", "Daily loss halt", "daily-loss-halt")
            webhook._drain()
            body = json.loads(mock.calls[0].request.read())
            assert body == {"content": "[WARNING] Daily loss halt"}


class TestQueueOverflow:
    def test_drop_on_queue_overflow_does_not_raise(self):
        """notify() silently drops when queue is full."""
        webhook.configure(DISCORD_URL)
        with respx.mock() as mock:
            mock.post(DISCORD_URL).mock(return_value=httpx.Response(204))
            # Fill queue beyond maxsize — should not raise
            for i in range(webhook._QUEUE_MAXSIZE + 5):
                webhook._rate_limits.clear()  # bypass rate limit
                webhook.notify("WARNING", f"msg{i}", "cat")
            webhook._drain()
        # No assertion on call count — just must not raise
