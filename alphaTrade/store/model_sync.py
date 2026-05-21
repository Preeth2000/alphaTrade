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


class ModelSyncDaemon:
    _SYNC_DIR = ".sync"

    def __init__(
        self,
        minio_cfg: MinioConfig,
        sync_cfg: ModelSyncConfig,
        models_dir: Path,
    ) -> None:
        self._minio_cfg = minio_cfg
        self._cfg = sync_cfg
        self._models_dir = models_dir
        self._gate = ValidationGate(sync_cfg.validation)
        self._sync_dir = models_dir / self._SYNC_DIR
        self._sync_dir.mkdir(parents=True, exist_ok=True)
        self._models_dir.mkdir(parents=True, exist_ok=True)

    # ---------- version record helpers ----------

    def _write_sync_record(self, run_name: str, version: str) -> None:
        safe_name = Path(run_name).name.replace("/", "_")
        (self._sync_dir / safe_name).write_text(version)

    def _read_sync_record(self, run_name: str) -> Optional[str]:
        safe_name = Path(run_name).name.replace("/", "_")
        p = self._sync_dir / safe_name
        return p.read_text().strip() if p.exists() else None

    # ---------- promotion ----------

    def _promote(self, src: Path, run_name: str, version: str) -> None:
        dest = self._models_dir / run_name
        tmp = dest.with_suffix(".tmp")
        if tmp.exists():
            shutil.rmtree(tmp)
        shutil.copytree(src, tmp)
        if dest.exists():
            shutil.rmtree(dest)
        tmp.rename(dest)
        self._write_sync_record(run_name, version)
        log.info("model_sync: promoted %s %s", run_name, version)

    # ---------- retention ----------

    def _versions_to_prune(self, versions: list[str], promoted: str) -> list[str]:
        if self._cfg.max_versions == -1:
            return []

        def _version_int(v: str) -> int:
            try:
                return int(v[1:]) if v.startswith("v") else 0
            except ValueError:
                return 0

        sorted_versions = sorted(versions, key=_version_int)
        # never prune the just-promoted version
        candidates = [v for v in sorted_versions if v != promoted]
        excess = len(sorted_versions) - self._cfg.max_versions
        if excess <= 0:
            return []
        return candidates[:excess]
