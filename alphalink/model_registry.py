"""Hot-reloadable model registry. Rescans models_dir each tick, diffs, adds/removes."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.engine import Engine
from sqlmodel import Session

from alphalink.adapter.inference import OnnxModel
from alphalink.adapter.manifest import Manifest
from alphalink.main import scan_models

log = logging.getLogger(__name__)


class ModelRegistry:
    def __init__(self, engine: Optional[Engine] = None) -> None:
        self._lock = asyncio.Lock()
        self._engine = engine
        self.by_run_name: dict[str, tuple[Manifest, OnnxModel]] = {}

    async def refresh(self, models_dir: Path, overrides: dict[str, Any]) -> None:
        """Rescan models_dir, hot-add new models, drop removed ones. Skip retired models."""
        try:
            scanned = scan_models(models_dir)
        except Exception as exc:
            log.error("model_registry: scan failed, keeping existing models: %s", exc)
            return

        active: dict[str, tuple[Manifest, OnnxModel]] = {}
        for manifest, model in scanned:
            override = overrides.get(manifest.run_name)
            if override is not None and not override.enabled:
                continue
            if self._is_retired(manifest.run_name):
                log.info("model_registry: skipping retired model %s", manifest.run_name)
                continue
            active[manifest.run_name] = (manifest, model)

        async with self._lock:
            current = set(self.by_run_name)
            new = set(active)
            for added in new - current:
                log.info("model_registry: hot-added %s", added)
            for removed in current - new:
                log.info("model_registry: hot-removed %s", removed)
            self.by_run_name = active

    def _is_retired(self, run_name: str) -> bool:
        if self._engine is None:
            return False
        from alphalink.store.repos import ModelPerformanceRepo
        try:
            with Session(self._engine) as s:
                return ModelPerformanceRepo(s).is_retired(run_name)
        except Exception as exc:
            log.warning("model_registry: retirement check failed for %s: %s", run_name, exc)
            return False

    def snapshot_by_interval(self) -> dict[str, list[tuple[Manifest, OnnxModel]]]:
        by_interval: dict[str, list[tuple[Manifest, OnnxModel]]] = defaultdict(list)
        for manifest, model in self.by_run_name.values():
            by_interval[manifest.interval].append((manifest, model))
        return dict(by_interval)
