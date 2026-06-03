"""ModelSyncDaemon: poll MLflow Registry for Production models, validate, promote to models_dir."""
from __future__ import annotations

import asyncio
import logging
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Callable, Optional

import mlflow
from mlflow import MlflowClient
from mlflow.exceptions import MlflowException

from alphaTrade.config import ModelSyncConfig

log = logging.getLogger(__name__)


class ModelSyncDaemon:
    _SYNC_DIR = ".sync"

    _FAILED_PREFIX = "FAILED:"

    def __init__(
        self,
        sync_cfg: ModelSyncConfig,
        models_dir: Path,
        session_factory: Optional[Callable] = None,
        redis_client=None,
        on_promote: Optional[Callable] = None,
    ) -> None:
        self._cfg = sync_cfg
        self._models_dir = models_dir
        self._sync_dir = models_dir / self._SYNC_DIR
        self._sync_dir.mkdir(parents=True, exist_ok=True)
        self._models_dir.mkdir(parents=True, exist_ok=True)
        self._seen_staging: set[str] = set()
        self._session_factory = session_factory
        self._redis_client = redis_client
        self._on_promote = on_promote
        self._download_fail_counts: dict[str, int] = defaultdict(int)  # key: "{name}:v{version}"

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

    def _permanently_fail(self, model_name: str, version: str, reason: str) -> None:
        """Write failed sync record and update model_deployments. Stops daemon retrying this version."""
        self._write_sync_record(model_name, f"{self._FAILED_PREFIX}{version}")
        log.error("model_sync: permanently failed %s v%s — %s", model_name, version, reason)
        if self._session_factory is None:
            return
        try:
            from alphaTrade.store.repos import ModelDeploymentRepo
            session = self._session_factory()
            try:
                ModelDeploymentRepo(session).mark_failed(model_name, reason)
            finally:
                session.close()
        except Exception as exc:
            log.error("model_sync: could not write deployment failure for %s: %s", model_name, exc)

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

                # Staging: notify on new versions (alias-based, MLflow 3.x)
                try:
                    sv = client.get_model_version_by_alias(name, "staging")
                    key = f"{name}:v{sv.version}"
                    if key not in self._seen_staging:
                        self._seen_staging.add(key)
                        log.info(
                            "model_sync: new staging model %s v%s — ready for review",
                            name, sv.version,
                        )
                        self._notify_staging(name, sv.version)
                except MlflowException:
                    pass  # no staging alias set

                # Production: sync if version changed (alias-based, MLflow 3.x)
                try:
                    pv = client.get_model_version_by_alias(name, "production")
                except MlflowException:
                    continue  # no production alias
                local_version = self._read_sync_record(name)
                # skip if already synced or permanently failed for this version
                if local_version == pv.version or local_version == f"{self._FAILED_PREFIX}{pv.version}":
                    # recover orphaned "launching" rows from a restart after _promote() completed
                    if local_version == pv.version and self._session_factory is not None:
                        try:
                            from alphaTrade.store.repos import ModelDeploymentRepo
                            session = self._session_factory()
                            try:
                                ModelDeploymentRepo(session).mark_active(name)
                            finally:
                                session.close()
                        except Exception as exc:
                            log.error("model_sync: recovery mark_active failed for %s: %s", name, exc)
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

                # Unrecoverable: no run attached — fail immediately
                if not pv.run_id:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    self._permanently_fail(name, pv.version, "no run_id — registered without mlflow.log_model")
                    continue

                fail_key = f"{name}:v{pv.version}"
                try:
                    mlflow.artifacts.download_artifacts(
                        run_id=pv.run_id, artifact_path="", dst_path=str(tmp_dir)
                    )
                    self._download_fail_counts.pop(fail_key, None)  # reset on success
                except Exception as exc:
                    self._download_fail_counts[fail_key] += 1
                    attempts = self._download_fail_counts[fail_key]
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    if attempts >= self._cfg.max_download_attempts:
                        self._permanently_fail(
                            name, pv.version,
                            f"download failed after {attempts} attempts: {exc}",
                        )
                    else:
                        log.warning(
                            "model_sync: download failed for %s v%s (attempt %d/%d): %s",
                            name, pv.version, attempts, self._cfg.max_download_attempts, exc,
                        )
                    continue

                # Unrecoverable: backtest.json absent
                backtest_path = tmp_dir / "backtest.json"
                if not backtest_path.exists():
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    self._permanently_fail(name, pv.version, "backtest.json missing from artifacts")
                    continue

                # MLflow 3.x: onnx.log_model stores model.onnx as a LoggedModel (models:/m-{id}),
                # not in run artifacts — download separately if missing.
                onnx_path = tmp_dir / "model.onnx"
                if not onnx_path.exists() and pv.source:
                    onnx_dl_dir = tmp_dir / "_onnx_dl"
                    try:
                        mlflow.artifacts.download_artifacts(
                            artifact_uri=pv.source, dst_path=str(onnx_dl_dir)
                        )
                        # find model.onnx and copy all sibling files (e.g. external .data weights)
                        found = next(onnx_dl_dir.rglob("model.onnx"), None)
                        if found:
                            for sibling in found.parent.iterdir():
                                dest_file = tmp_dir / sibling.name
                                if not dest_file.exists():
                                    sibling.rename(dest_file)
                        shutil.rmtree(onnx_dl_dir, ignore_errors=True)
                        self._download_fail_counts.pop(fail_key, None)
                        log.info("model_sync: downloaded model.onnx for %s v%s from %s", name, pv.version, pv.source)
                    except Exception as exc:
                        shutil.rmtree(onnx_dl_dir, ignore_errors=True)
                        self._download_fail_counts[fail_key] += 1
                        attempts = self._download_fail_counts[fail_key]
                        shutil.rmtree(tmp_dir, ignore_errors=True)
                        if attempts >= self._cfg.max_download_attempts:
                            self._permanently_fail(
                                name, pv.version,
                                f"model.onnx download from logged model failed after {attempts} attempts: {exc}",
                            )
                        else:
                            log.warning(
                                "model_sync: model.onnx download failed for %s v%s (attempt %d/%d): %s",
                                name, pv.version, attempts, self._cfg.max_download_attempts, exc,
                            )
                        continue

                if not onnx_path.exists():
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    self._permanently_fail(name, pv.version, "model.onnx missing — not in run artifacts or logged model")
                    continue

                self._promote(tmp_dir, name, pv.version)
                shutil.rmtree(tmp_dir, ignore_errors=True)
                if self._session_factory is not None:
                    try:
                        from alphaTrade.store.repos import ModelDeploymentRepo
                        session = self._session_factory()
                        try:
                            ModelDeploymentRepo(session).mark_active(name)
                        finally:
                            session.close()
                    except Exception as exc:
                        log.error("model_sync: could not mark deployment active for %s: %s", name, exc)
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
                    if self._on_promote is not None:
                        await self._on_promote(promoted)
            except Exception as exc:
                log.error("model_sync: poll error: %s", exc)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._cfg.poll_interval)
            except asyncio.TimeoutError:
                pass
        log.info("model_sync: daemon stopped")
