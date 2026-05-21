"""ModelSyncDaemon: poll MinIO for new model versions, validate, promote to models_dir."""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from alphaTrade.config import MinioConfig, ModelSyncConfig, ValidationThresholds

log = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    passed: bool
    reason: Optional[str] = None


class ValidationGate:
    _REQUIRED_KEYS = ("sharpe", "max_drawdown", "hit_rate")

    def __init__(self, thresholds: ValidationThresholds) -> None:
        self._t = thresholds

    def check(self, metrics: dict[str, Any]) -> ValidationResult:
        missing = [k for k in self._REQUIRED_KEYS if k not in metrics]
        if missing:
            return ValidationResult(False, f"missing keys: {missing}")
        if metrics["sharpe"] < self._t.min_sharpe:
            return ValidationResult(False, f"sharpe {metrics['sharpe']:.3f} < {self._t.min_sharpe}")
        drawdown = abs(metrics["max_drawdown"])
        if drawdown > self._t.max_drawdown:
            return ValidationResult(False, f"drawdown {drawdown:.3f} > {self._t.max_drawdown}")
        if metrics["hit_rate"] < self._t.min_hit_rate:
            return ValidationResult(False, f"hit_rate {metrics['hit_rate']:.3f} < {self._t.min_hit_rate}")
        return ValidationResult(True)
