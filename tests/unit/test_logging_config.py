"""Tests for logging configuration: RotatingFileHandler + stdout stream + JSON format."""
from __future__ import annotations

import io
import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from alphalink.logging_config import configure_logging, LOG_MAX_BYTES, LOG_BACKUP_COUNT


class TestConfigureLogging:
    def test_adds_rotating_file_handler(self, tmp_path):
        log_file = tmp_path / "alphalink.log"
        configure_logging(log_file=log_file)
        root = logging.getLogger()
        rfh = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
        assert rfh, "RotatingFileHandler must be attached to root logger"

    def test_rotating_handler_max_bytes(self, tmp_path):
        log_file = tmp_path / "alphalink.log"
        configure_logging(log_file=log_file)
        root = logging.getLogger()
        rfh = next(h for h in root.handlers if isinstance(h, RotatingFileHandler))
        assert rfh.maxBytes == LOG_MAX_BYTES

    def test_rotating_handler_backup_count(self, tmp_path):
        log_file = tmp_path / "alphalink.log"
        configure_logging(log_file=log_file)
        root = logging.getLogger()
        rfh = next(h for h in root.handlers if isinstance(h, RotatingFileHandler))
        assert rfh.backupCount == LOG_BACKUP_COUNT

    def test_stdout_stream_handler_present(self, tmp_path):
        log_file = tmp_path / "alphalink.log"
        configure_logging(log_file=log_file)
        root = logging.getLogger()
        stream_handlers = [
            h for h in root.handlers
            if isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler)
        ]
        assert stream_handlers, "StreamHandler for stdout must be present"

    def test_no_log_file_skips_rotating_handler(self):
        configure_logging(log_file=None)
        root = logging.getLogger()
        rfh = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
        assert not rfh, "No RotatingFileHandler when log_file=None"

    def test_idempotent_multiple_calls(self, tmp_path):
        log_file = tmp_path / "alphalink.log"
        configure_logging(log_file=log_file)
        handler_count_before = len(logging.getLogger().handlers)
        configure_logging(log_file=log_file)
        handler_count_after = len(logging.getLogger().handlers)
        assert handler_count_after == handler_count_before, "Duplicate handlers must not be added"

    def test_constants_match_spec(self):
        assert LOG_MAX_BYTES == 10 * 1024 * 1024  # 10 MB
        assert LOG_BACKUP_COUNT == 5

    @pytest.fixture(autouse=True)
    def reset_root_handlers(self):
        root = logging.getLogger()
        original = root.handlers[:]
        yield
        root.handlers = original


class TestJsonOutput:
    """Verify the JSON formatter produces required keys."""

    def _capture(self, extra: dict | None = None) -> dict:
        from alphalink.logging_config import make_json_formatter as _make_json_formatter
        buf = io.StringIO()
        sh = logging.StreamHandler(buf)
        sh.setFormatter(_make_json_formatter())
        logger = logging.getLogger("_alphalink_test_json")
        logger.propagate = False
        logger.addHandler(sh)
        logger.setLevel(logging.DEBUG)
        try:
            logger.info("probe", extra=extra or {"ticker": "AAPL", "signal": "BUY"})
        finally:
            logger.removeHandler(sh)
        buf.seek(0)
        return json.loads(buf.getvalue().strip())

    def test_output_is_valid_json(self):
        assert isinstance(self._capture(), dict)

    def test_json_has_ts_field(self):
        assert "ts" in self._capture()

    def test_json_has_level_field(self):
        assert "level" in self._capture()

    def test_json_has_module_field(self):
        assert "module" in self._capture()

    def test_json_has_message_field(self):
        assert "message" in self._capture()

    def test_extra_fields_passed_through(self):
        record = self._capture()
        assert record.get("ticker") == "AAPL"
        assert record.get("signal") == "BUY"


class TestPerModuleLogLevel:
    """LOG_LEVEL_<module_underscored>=LEVEL sets per-logger level."""

    @pytest.fixture(autouse=True)
    def reset_loggers(self):
        loggers_to_reset = ["alphalink.broker", "alphalink.data"]
        saved = {n: logging.getLogger(n).level for n in loggers_to_reset}
        root = logging.getLogger()
        orig_handlers = root.handlers[:]
        yield
        root.handlers = orig_handlers
        for name, lvl in saved.items():
            logging.getLogger(name).setLevel(lvl)

    def test_env_var_sets_module_level(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL_alphalink_broker", "DEBUG")
        configure_logging(log_file=None)
        assert logging.getLogger("alphalink.broker").level == logging.DEBUG

    def test_multiple_env_vars(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL_alphalink_broker", "DEBUG")
        monkeypatch.setenv("LOG_LEVEL_alphalink_data", "WARNING")
        configure_logging(log_file=None)
        assert logging.getLogger("alphalink.broker").level == logging.DEBUG
        assert logging.getLogger("alphalink.data").level == logging.WARNING

    def test_invalid_level_ignored(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL_alphalink_broker", "NOTLEVEL")
        before = logging.getLogger("alphalink.broker").level
        configure_logging(log_file=None)
        assert logging.getLogger("alphalink.broker").level == before

    def test_unrelated_env_var_ignored(self, monkeypatch):
        monkeypatch.setenv("OTHER_VAR", "DEBUG")
        before = logging.getLogger("alphalink.broker").level
        configure_logging(log_file=None)
        assert logging.getLogger("alphalink.broker").level == before
