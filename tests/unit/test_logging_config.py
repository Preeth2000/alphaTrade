"""Tests for logging configuration: RotatingFileHandler + stdout stream."""
from __future__ import annotations

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
