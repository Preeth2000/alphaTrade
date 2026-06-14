"""ModelSyncDaemon: poll MLflow Registry for Production models, validate, promote to models_dir."""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Callable, Optional

import mlflow
from mlflow import MlflowClient
from mlflow.exceptions import MlflowException

from alphaTrade.config import MinioConfig, ModelSyncConfig

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
        minio_cfg: Optional[MinioConfig] = None,
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
        self._minio_cfg = minio_cfg
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

    # ---------- model.ready Redis consumer ----------

    def _sync_from_minio(self, run_name: str, version: str, artifact_prefix: str) -> None:
        """Download artifacts from MinIO artifact_prefix and promote."""
        if self._minio_cfg is None:
            log.warning("model_ready: artifact_prefix=%s but no MinIO config — skipping", artifact_prefix)
            return
        import boto3
        endpoint = self._minio_cfg.endpoint
        if not endpoint.startswith("http"):
            endpoint = f"http://{endpoint}"
        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=self._minio_cfg.access_key,
            aws_secret_access_key=self._minio_cfg.secret_key,
        )
        bucket = self._minio_cfg.bucket
        prefix = artifact_prefix.rstrip("/") + "/"
        tmp_dir = self._models_dir / ".tmp" / run_name.replace("/", "_")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        try:
            paginator = s3.get_paginator("list_objects_v2")
            found_any = False
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    rel = key[len(prefix):]
                    if not rel:
                        continue
                    dest = tmp_dir / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    s3.download_file(bucket, key, str(dest))
                    found_any = True
            if not found_any:
                log.error("model_ready: no artifacts found at %s/%s", bucket, prefix)
                shutil.rmtree(tmp_dir, ignore_errors=True)
                return
            if not (tmp_dir / "manifest.json").exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)
                self._permanently_fail(run_name, version, "manifest.json missing from MinIO artifact_prefix")
                return
            if not (tmp_dir / "model.onnx").exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)
                self._permanently_fail(run_name, version, "model.onnx missing from MinIO artifact_prefix")
                return
            self._promote(tmp_dir, run_name, version)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            if self._session_factory is not None:
                try:
                    from alphaTrade.store.repos import ModelDeploymentRepo
                    session = self._session_factory()
                    try:
                        ModelDeploymentRepo(session).mark_active(run_name)
                    finally:
                        session.close()
                except Exception as exc:
                    log.error("model_ready: mark_active failed for %s: %s", run_name, exc)
        except Exception as exc:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            log.error("model_ready: MinIO download failed for %s v%s: %s", run_name, version, exc)

    async def _handle_model_ready_event(self, raw: bytes | str) -> None:
        """Parse and act on a model.ready Redis message with the new payload schema."""
        try:
            data = raw if isinstance(raw, str) else raw.decode()
            payload = json.loads(data)
        except Exception as exc:
            log.warning("model_ready: malformed payload: %s", exc)
            return
        run_name = payload.get("run_name")
        version = payload.get("version")
        artifact_prefix = payload.get("artifact_prefix")  # optional — present only for explicit publish
        if not run_name or not version:
            log.warning("model_ready: missing run_name/version in payload")
            return
        log.info("model_ready: %s v%s artifact_prefix=%s", run_name, version, artifact_prefix)
        if artifact_prefix:
            # Explicit POST /runs/{id}/publish — download directly from MinIO
            promoted = await asyncio.to_thread(self._sync_from_minio, run_name, version, artifact_prefix)
            local = self._read_sync_record(run_name)
            if local == version and self._on_promote is not None:
                await self._on_promote([run_name])
        else:
            # Celery auto-promote — model registered in MLflow; trigger immediate poll
            try:
                promoted = await self._sync_once()
                if promoted:
                    log.info("model_ready: triggered sync promoted %s", promoted)
                    if self._on_promote is not None:
                        await self._on_promote(promoted)
            except Exception as exc:
                log.error("model_ready: triggered sync failed: %s", exc)

    async def _listen_model_ready(self, stop_event: asyncio.Event) -> None:
        """Subscribe to model.ready Redis channel and process events."""
        if self._redis_client is None:
            return
        pubsub = self._redis_client.pubsub()
        await pubsub.subscribe("model.ready")
        log.info("model_sync: subscribed to model.ready Redis channel")
        try:
            while not stop_event.is_set():
                try:
                    message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True), timeout=5.0
                    )
                except asyncio.TimeoutError:
                    continue
                if message and message["type"] == "message":
                    await self._handle_model_ready_event(message["data"])
        finally:
            await pubsub.unsubscribe("model.ready")
            await pubsub.aclose()

    async def run(self, stop_event: asyncio.Event) -> None:
        log.info("model_sync: daemon started (poll_interval=%ds)", self._cfg.poll_interval)
        listener_task: Optional[asyncio.Task] = None
        if self._redis_client is not None:
            listener_task = asyncio.create_task(self._listen_model_ready(stop_event))
        try:
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
        finally:
            if listener_task is not None:
                listener_task.cancel()
                try:
                    await listener_task
                except asyncio.CancelledError:
                    pass
        log.info("model_sync: daemon stopped")
