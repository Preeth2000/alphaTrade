"""Tests for webhook wire-up in main.py: startup notify and handler idempotency."""
from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


from alphaTrade.notify import webhook as wh


def _settings(tmp_path: Path, webhook_url: str = "https://discord.com/api/webhooks/123/abc"):
    from alphaTrade.config import Settings, ModelSyncConfig
    return Settings(
        t212_demo_api_key="test-key",
        state_db_path=tmp_path / "state.db",
        models_dir=tmp_path / "models",
        overrides_path=tmp_path / "overrides.yaml",
        webhook_url=webhook_url,
        model_sync=ModelSyncConfig(enabled=False),
    )


def _mock_registry():
    manifest = MagicMock()
    manifest.ticker = "AAPL"
    manifest.run_name = "test_run"
    manifest.interval = "1d"
    model = MagicMock()
    reg = MagicMock()
    reg.refresh = AsyncMock()
    reg.by_run_name = {"test_run": (manifest, model)}
    reg.snapshot_by_interval.return_value = {}  # no tasks → gather() exits immediately
    return reg


def _mock_t212():
    t212 = MagicMock()
    t212.get_total_equity.return_value = 10_000.0
    t212.get_positions.return_value = []
    return t212


def _base_patches():
    """Minimal patches to make run() exit cleanly with no real I/O."""
    return [
        patch("alphaTrade.model_registry.ModelRegistry", return_value=_mock_registry()),
        patch("alphaTrade.main.T212Client", return_value=_mock_t212()),
        patch("alphaTrade.main._build_data_provider", return_value=MagicMock()),
        patch("alphaTrade.main._preresolve_tickers"),
        patch("alphaTrade.main.start_health_server", AsyncMock(return_value=MagicMock(cleanup=AsyncMock()))),
        patch("prometheus_client.start_http_server"),
        patch("alphaTrade.main.schedule_bar_close", new=AsyncMock()),
        patch("alphaTrade.telemetry.setup_telemetry"),
    ]


class TestStartupNotify:
    async def test_startup_notify_fires_with_startup_category(self, tmp_path):
        """wh.notify called with category='startup' when webhook_url is set."""
        from alphaTrade.main import run
        settings = _settings(tmp_path)

        with (
            patch("alphaTrade.model_registry.ModelRegistry", return_value=_mock_registry()),
            patch("alphaTrade.main.T212Client", return_value=_mock_t212()),
            patch("alphaTrade.main._build_data_provider", return_value=MagicMock()),
            patch("alphaTrade.main._preresolve_tickers"),
            patch("alphaTrade.main.start_health_server", AsyncMock(return_value=MagicMock(cleanup=AsyncMock()))),
            patch("alphaTrade.api.app.start_api_server", AsyncMock(return_value=MagicMock(should_exit=False))),
            patch("prometheus_client.start_http_server"),
            patch("alphaTrade.main.schedule_bar_close", new=AsyncMock()),
            patch("alphaTrade.telemetry.setup_telemetry"),
            patch("alphaTrade.main.wh.notify") as mock_notify,
        ):
            await run(settings)

        startup_calls = [
            c for c in mock_notify.call_args_list
            if c.kwargs.get("category") == "startup"
        ]
        assert len(startup_calls) == 1
        assert startup_calls[0].args[0] == "INFO"

    async def test_startup_notify_not_fired_without_webhook_url(self, tmp_path):
        """wh.notify must NOT be called when webhook_url is empty."""
        from alphaTrade.main import run
        settings = _settings(tmp_path, webhook_url="")

        with (
            patch("alphaTrade.model_registry.ModelRegistry", return_value=_mock_registry()),
            patch("alphaTrade.main.T212Client", return_value=_mock_t212()),
            patch("alphaTrade.main._build_data_provider", return_value=MagicMock()),
            patch("alphaTrade.main._preresolve_tickers"),
            patch("alphaTrade.main.start_health_server", AsyncMock(return_value=MagicMock(cleanup=AsyncMock()))),
            patch("alphaTrade.api.app.start_api_server", AsyncMock(return_value=MagicMock(should_exit=False))),
            patch("prometheus_client.start_http_server"),
            patch("alphaTrade.main.schedule_bar_close", new=AsyncMock()),
            patch("alphaTrade.telemetry.setup_telemetry"),
            patch("alphaTrade.main.wh.notify") as mock_notify,
        ):
            await run(settings)

        startup_calls = [
            c for c in mock_notify.call_args_list
            if c.kwargs.get("category") == "startup"
        ]
        assert len(startup_calls) == 0


class TestHandlerIdempotency:
    def test_webhook_handler_not_double_attached(self):
        """WebhookHandler guard prevents adding a second handler."""
        root = logging.getLogger("test_idempotency_isolated")
        root.handlers = [h for h in root.handlers if not isinstance(h, wh.WebhookHandler)]

        def _attach():
            _wh = wh.WebhookHandler()
            if not any(isinstance(h, wh.WebhookHandler) for h in root.handlers):
                root.addHandler(_wh)

        _attach()
        _attach()

        handler_count = sum(1 for h in root.handlers if isinstance(h, wh.WebhookHandler))
        assert handler_count == 1
        root.handlers = [h for h in root.handlers if not isinstance(h, wh.WebhookHandler)]

    async def test_run_does_not_double_attach_handler(self, tmp_path):
        """Calling run() twice attaches WebhookHandler exactly once."""
        from alphaTrade.main import run

        root = logging.getLogger()
        root.handlers = [h for h in root.handlers if not isinstance(h, wh.WebhookHandler)]

        settings = _settings(tmp_path)

        async def _run_once():
            with (
                patch("alphaTrade.model_registry.ModelRegistry", return_value=_mock_registry()),
                patch("alphaTrade.main.T212Client", return_value=_mock_t212()),
                patch("alphaTrade.main._build_data_provider", return_value=MagicMock()),
                patch("alphaTrade.main._preresolve_tickers"),
                patch("alphaTrade.main.start_health_server", AsyncMock(return_value=MagicMock(cleanup=AsyncMock()))),
                patch("alphaTrade.api.app.start_api_server", AsyncMock(return_value=MagicMock(should_exit=False))),
                patch("prometheus_client.start_http_server"),
                patch("alphaTrade.main.schedule_bar_close", new=AsyncMock()),
                patch("alphaTrade.telemetry.setup_telemetry"),
                patch("alphaTrade.main.wh.notify"),  # suppress actual delivery
            ):
                await run(settings)

        await _run_once()
        await _run_once()

        handler_count = sum(1 for h in root.handlers if isinstance(h, wh.WebhookHandler))
        assert handler_count == 1, (
            f"Expected 1 WebhookHandler, got {handler_count} — idempotency check failed"
        )
        root.handlers = [h for h in root.handlers if not isinstance(h, wh.WebhookHandler)]
