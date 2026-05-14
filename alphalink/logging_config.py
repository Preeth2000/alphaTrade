"""Logging setup: JSON-structured output, RotatingFileHandler (10MB x 5) + stdout."""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from pythonjsonlogger.json import JsonFormatter as _JsonFormatter

LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB per file
LOG_BACKUP_COUNT = 5               # 5 rotated files = 50 MB cap

_JSON_FMT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_RENAME = {"asctime": "ts", "levelname": "level", "name": "module"}


def make_json_formatter() -> _JsonFormatter:
    return _JsonFormatter(_JSON_FMT, rename_fields=_RENAME)


def _apply_per_module_levels() -> None:
    """Apply LOG_LEVEL_<module>=LEVEL env vars to per-logger levels.

    Underscores in the suffix are converted to dots: LOG_LEVEL_alphalink_broker
    sets the level of logging.getLogger("alphalink.broker").
    """
    for key, val in os.environ.items():
        if not key.startswith("LOG_LEVEL_"):
            continue
        logger_name = key[len("LOG_LEVEL_"):].replace("_", ".")
        level = getattr(logging, val.upper(), None)
        if level is not None:
            logging.getLogger(logger_name).setLevel(level)


def configure_logging(
    log_file: Path | None = None,
    level: int = logging.INFO,
) -> None:
    """Attach JSON handlers to root logger. Idempotent — skips if already attached."""
    root = logging.getLogger()
    root.setLevel(level)

    existing_types = {type(h) for h in root.handlers}

    if logging.StreamHandler not in existing_types:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(make_json_formatter())
        root.addHandler(sh)

    if log_file is not None and RotatingFileHandler not in existing_types:
        log_file = Path(log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        rfh = RotatingFileHandler(
            log_file,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        rfh.setFormatter(make_json_formatter())
        root.addHandler(rfh)

    _apply_per_module_levels()
