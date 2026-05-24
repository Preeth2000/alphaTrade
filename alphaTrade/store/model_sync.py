"""ModelSyncDaemon: poll MLflow Registry for Production models, validate, promote to models_dir."""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import mlflow
from mlflow import MlflowClient

from alphaTrade.config import ModelSyncConfig, ValidationThresholds

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
        sync_cfg: ModelSyncConfig,
        models_dir: Path,
    ) -> None:
        self._cfg = sync_cfg
        self._models_dir = models_dir
        self._gate = ValidationGate(sync_cfg.validation)
        self._sync_dir = models_dir / self._SYNC_DIR
        self._sync_dir.mkdir(parents=True, exist_ok=True)
        self._models_dir.mkdir(parents=True, exist_ok=True)
        self._seen_staging: set[str] = set()

    # ---------- version record helpers ----------

    def _write_sync_record(self, model_name: str, version: str) -> None:
        safe_name = model_name.replace("/", "_")
        (self._sync_dir / safe_name).write_text(version)

    def _read_sync_record(self, model_name: str) -> Optional[str]:
        safe_name = model_name.replace("/", "_")
        p = self._sync_dir / safe_name
        return p.read_text().strip() if p.exists() else None

    # ---------- promotion ----------

    def _promote(self, src: Path, model_name: str, version: str) -> None:
        safe_name = model_name.replace("/", "_")
        dest = self._models_dir / safe_name
        tmp = dest.parent / f"{dest.name}.tmp"
        if tmp.exists():
            shutil.rmtree(tmp)
        shutil.copytree(src, tmp)
        if dest.exists():
            shutil.rmtree(dest)
        tmp.rename(dest)
        self._write_sync_record(model_name, version)
        log.info("model_sync: promoted %s v%s", model_name, version)

    # ---------- staging notification stub ----------

    def _notify_staging(self, name: str, version: str) -> None:
        pass  # stub: wire Slack/email here

    # ---------- polling ----------

    async def _sync_once(self) -> list[str]:
        def _run() -> list[str]:
            client = MlflowClient()
            registered = client.search_registered_models()
            tmp_root = self._models_dir / ".tmp"
            tmp_root.mkdir(parents=True, exist_ok=True)
            promoted: list[str] = []

            for rm in registered:
                name = rm.name

                # Staging: notify on new versions
                staging = client.get_latest_versions(name, stages=["Staging"])
                for sv in staging:
                    key = f"{name}:v{sv.version}"
                    if key not in self._seen_staging:
                        self._seen_staging.add(key)
                        log.info(
                            "model_sync: new staging model %s v%s — ready for review",
                            name, sv.version,
                        )
                        self._notify_staging(name, sv.version)

                # Production: sync if version changed
                production = client.get_latest_versions(name, stages=["Production"])
                if not production:
                    continue
                pv = production[0]
                local_version = self._read_sync_record(name)
                if local_version == pv.version:
                    continue

                log.info(
                    "model_sync: new Production version %s for %s (local=%s)",
                    pv.version, name, local_version,
                )
                safe_name = name.replace("/", "_")
                tmp_dir = tmp_root / safe_name
                if tmp_dir.exists():
                    shutil.rmtree(tmp_dir)
                tmp_dir.mkdir(parents=True, exist_ok=True)

                try:
                    mlflow.artifacts.download_artifacts(
                        f"runs:/{pv.run_id}", dst_path=str(tmp_dir)
                    )
                except Exception as exc:
                    log.error("model_sync: download failed for %s v%s: %s", name, pv.version, exc)
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    continue

                backtest_path = tmp_dir / "backtest.json"
                if not backtest_path.exists():
                    log.error(
                        "model_sync: backtest.json missing for %s v%s — rejected",
                        name, pv.version,
                    )
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    continue

                metrics = json.loads(backtest_path.read_text())
                result = self._gate.check(metrics)
                if not result.passed:
                    log.warning(
                        "model_sync: validation failed for %s v%s: %s",
                        name, pv.version, result.reason,
                    )
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    continue

                self._promote(tmp_dir, name, pv.version)
                shutil.rmtree(tmp_dir, ignore_errors=True)
                promoted.append(name)

            return promoted

        return await asyncio.to_thread(_run)

    async def run(self, stop_event: asyncio.Event) -> None:
        log.info("model_sync: daemon started (poll_interval=%ds)", self._cfg.poll_interval)
        while not stop_event.is_set():
            try:
                promoted = await self._sync_once()
                if promoted:
                    log.info("model_sync: promoted %d model(s): %s", len(promoted), promoted)
            except Exception as exc:
                log.error("model_sync: poll error: %s", exc)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._cfg.poll_interval)
            except asyncio.TimeoutError:
                pass
        log.info("model_sync: daemon stopped")
