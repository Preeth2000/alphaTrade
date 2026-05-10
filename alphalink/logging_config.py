"""Logging setup: RotatingFileHandler (10MB x 5) + stdout StreamHandler."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB per file
LOG_BACKUP_COUNT = 5               # 5 rotated files = 50 MB cap
_FMT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging(
    log_file: Path | None = None,
    level: int = logging.INFO,
) -> None:
    """Attach handlers to root logger. Idempotent — skips if already attached."""
    root = logging.getLogger()
    root.setLevel(level)

    existing_types = {type(h) for h in root.handlers}

    if logging.StreamHandler not in existing_types:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(logging.Formatter(_FMT))
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
        rfh.setFormatter(logging.Formatter(_FMT))
        root.addHandler(rfh)
