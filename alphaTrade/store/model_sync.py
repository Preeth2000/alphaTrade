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

    # ---------- S3 client ----------

    def _make_s3_client(self):
        import boto3
        return boto3.client(
            "s3",
            endpoint_url=f"{'https' if self._minio_cfg.secure else 'http'}://{self._minio_cfg.endpoint}",
            aws_access_key_id=self._minio_cfg.access_key,
            aws_secret_access_key=self._minio_cfg.secret_key,
        )

    # ---------- polling ----------

    def _list_run_names(self, s3) -> list[str]:
        prefix = f"{self._cfg.user}/{self._cfg.account}/"
        resp = s3.list_objects_v2(
            Bucket=self._minio_cfg.bucket,
            Prefix=prefix,
            Delimiter="/",
        )
        prefixes = resp.get("CommonPrefixes") or []
        return [p["Prefix"].rstrip("/").split("/")[-1] for p in prefixes]

    def _read_latest(self, s3, run_name: str) -> Optional[str]:
        key = f"{self._cfg.user}/{self._cfg.account}/{run_name}/latest"
        try:
            resp = s3.get_object(Bucket=self._minio_cfg.bucket, Key=key)
            data = json.loads(resp["Body"].read())
            return data.get("version")
        except Exception:
            return None

    def _download_version(self, s3, run_name: str, version: str, dest: Path) -> None:
        prefix = f"{self._cfg.user}/{self._cfg.account}/{run_name}/{version}/"
        resp = s3.list_objects_v2(Bucket=self._minio_cfg.bucket, Prefix=prefix)
        version_marker = f"/{version}/"
        for obj in resp.get("Contents") or []:
            key = obj["Key"]
            # strip everything up to and including the version segment
            if version_marker in key:
                rel = key.split(version_marker, 1)[1]
            else:
                rel = key[len(prefix):]
            local = dest / rel
            local.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(self._minio_cfg.bucket, key, str(local))

    def _prune_remote(self, s3, run_name: str, to_delete: list[str]) -> None:
        for version in to_delete:
            prefix = f"{self._cfg.user}/{self._cfg.account}/{run_name}/{version}/"
            resp = s3.list_objects_v2(Bucket=self._minio_cfg.bucket, Prefix=prefix)
            objects = [{"Key": o["Key"]} for o in resp.get("Contents") or []]
            if objects:
                s3.delete_objects(
                    Bucket=self._minio_cfg.bucket,
                    Delete={"Objects": objects},
                )
            log.info("model_sync: pruned remote %s %s", run_name, version)

    def _sync_run(self, s3, run_name: str, tmp_root: Path) -> bool:
        remote_version = self._read_latest(s3, run_name)
        if remote_version is None:
            log.warning("model_sync: no latest for %s", run_name)
            return False

        local_version = self._read_sync_record(run_name)
        if local_version == remote_version:
            return False

        log.info("model_sync: new version %s for %s (local=%s)", remote_version, run_name, local_version)
        tmp_dir = tmp_root / run_name
        tmp_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._download_version(s3, run_name, remote_version, tmp_dir)
        except Exception as exc:
            log.error("model_sync: download failed for %s %s: %s", run_name, remote_version, exc)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return False

        backtest_path = tmp_dir / "backtest.json"
        if not backtest_path.exists():
            log.error("model_sync: backtest.json missing for %s %s — rejected", run_name, remote_version)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return False

        metrics = json.loads(backtest_path.read_text())
        result = self._gate.check(metrics)
        if not result.passed:
            log.warning("model_sync: validation failed for %s %s: %s", run_name, remote_version, result.reason)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return False

        self._promote(tmp_dir, run_name, remote_version)
        shutil.rmtree(tmp_dir, ignore_errors=True)

        # prune old remote versions (best-effort — don't fail promotion on prune errors)
        try:
            prefix = f"{self._cfg.user}/{self._cfg.account}/{run_name}/"
            resp = s3.list_objects_v2(Bucket=self._minio_cfg.bucket, Prefix=prefix, Delimiter="/")
            all_versions = [
                p["Prefix"].rstrip("/").split("/")[-1]
                for p in (resp.get("CommonPrefixes") or [])
                if p["Prefix"].rstrip("/").split("/")[-1].startswith("v")
            ]
            to_prune = self._versions_to_prune(all_versions, promoted=remote_version)
            if to_prune:
                self._prune_remote(s3, run_name, to_prune)
        except Exception as exc:
            log.warning("model_sync: remote prune failed for %s: %s", run_name, exc)

        return True

    async def _sync_once(self) -> list[str]:
        def _run() -> list[str]:
            s3 = self._make_s3_client()
            run_names = self._list_run_names(s3)
            tmp_root = self._models_dir / ".tmp"
            tmp_root.mkdir(parents=True, exist_ok=True)
            promoted = []
            for run_name in run_names:
                try:
                    if self._sync_run(s3, run_name, tmp_root):
                        promoted.append(run_name)
                except Exception as exc:
                    log.error("model_sync: error syncing %s: %s", run_name, exc)
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
