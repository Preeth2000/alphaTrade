# Model Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add MinIO-backed model storage to alphaTrade — a `ModelSyncDaemon` polls MinIO, validates new model versions against configurable backtest thresholds, and promotes passing models into `models_dir` where the existing `ModelRegistry` hot-reloads them.

**Architecture:** Producer uploads model artifacts to MinIO at `models/{user}/{account}/{run_name}/v{n}/`. A `ModelSyncDaemon` async task polls each run's `latest` object, compares against a local `.sync/` version record to avoid re-downloads, validates `backtest.json` metrics against configurable thresholds, then promotes passing models into `models_dir`. Existing `ModelRegistry` hot-reload is unchanged.

**Tech Stack:** Python, `boto3` (S3/MinIO client via `asyncio.to_thread`), MinIO (Docker), pydantic-settings, pytest-asyncio

**Note:** This plan implements the consumer (alphaTrade) side only. Producer upload implementation lives in the producer project and is out of scope here.

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `alphaTrade/config.py` | Modify | Add `MinioConfig`, `ValidationThresholds`, `ModelSyncConfig` to `Settings` |
| `alphaTrade/store/model_sync.py` | Create | `ValidationGate` + `ModelSyncDaemon` |
| `alphaTrade/main.py` | Modify | Wire `ModelSyncDaemon` as async task at startup |
| `pyproject.toml` | Modify | Add `boto3` dependency |
| `docker-compose.yml` | Modify | Add `minio` + `minio-init` services, `minio_data` volume, bot env vars |
| `.env.example` | Modify | Document new env vars |
| `tests/unit/test_model_sync.py` | Create | Unit tests for `ValidationGate` + `ModelSyncDaemon` |

---

## Task 1: Add deps + config

**Files:**
- Modify: `pyproject.toml`
- Modify: `alphaTrade/config.py`

- [ ] **Step 1: Write failing test for new config fields**

```python
# tests/unit/test_model_sync.py
from alphaTrade.config import Settings


def test_settings_has_minio_defaults():
    s = Settings()
    assert s.minio.endpoint == "localhost:9000"
    assert s.minio.access_key == "minioadmin"
    assert s.minio.secret_key == "minioadmin"
    assert s.minio.bucket == "models"
    assert s.minio.secure is False


def test_settings_has_sync_defaults():
    s = Settings()
    assert s.model_sync.user == "default"
    assert s.model_sync.account == "default"
    assert s.model_sync.poll_interval == 60
    assert s.model_sync.max_versions == 5
    assert s.model_sync.enabled is True


def test_settings_has_validation_defaults():
    s = Settings()
    assert s.model_sync.validation.min_sharpe == 0.5
    assert s.model_sync.validation.max_drawdown == 0.20
    assert s.model_sync.validation.min_hit_rate == 0.45
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_model_sync.py -v
```
Expected: `ImportError` or `AttributeError` — fields don't exist yet.

- [ ] **Step 3: Add boto3 to pyproject.toml**

In `pyproject.toml`, add `"boto3>=1.34"` to the `dependencies` list alongside existing deps.

- [ ] **Step 4: Add config classes to alphaTrade/config.py**

After the existing imports at the top of `alphaTrade/config.py`, add:

```python
class ValidationThresholds(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    min_sharpe: float = 0.5
    max_drawdown: float = 0.20      # absolute value — backtest.json stores negative
    min_hit_rate: float = 0.45


class MinioConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    endpoint: str = "localhost:9000"
    access_key: str = "minioadmin"
    secret_key: str = "minioadmin"
    bucket: str = "models"
    secure: bool = False


class ModelSyncConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    enabled: bool = True
    user: str = "default"
    account: str = "default"
    poll_interval: int = 60         # seconds
    max_versions: int = 5           # -1 = unbounded
    validation: ValidationThresholds = ValidationThresholds()
```

Then add fields to `Settings`:

```python
    minio: MinioConfig = MinioConfig()
    model_sync: ModelSyncConfig = ModelSyncConfig()
```

Add these two fields after `api_port: int = 8081` in the `Settings` class body.

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/unit/test_model_sync.py::test_settings_has_minio_defaults tests/unit/test_model_sync.py::test_settings_has_sync_defaults tests/unit/test_model_sync.py::test_settings_has_validation_defaults -v
```
Expected: 3 PASSED.

- [ ] **Step 6: Install new dep**

```bash
pip install boto3
```

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml alphaTrade/config.py tests/unit/test_model_sync.py
git commit -m "feat(model-sync): add MinioConfig, ModelSyncConfig, ValidationThresholds to Settings"
```

---

## Task 2: ValidationGate

**Files:**
- Create: `alphaTrade/store/model_sync.py`
- Test: `tests/unit/test_model_sync.py`

- [ ] **Step 1: Write failing tests for ValidationGate**

Append to `tests/unit/test_model_sync.py`:

```python
import json
from pathlib import Path
import pytest
from alphaTrade.config import ValidationThresholds
from alphaTrade.store.model_sync import ValidationGate


def _write_backtest(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "backtest.json"
    p.write_text(json.dumps(data))
    return p


def test_validation_gate_passes():
    thresholds = ValidationThresholds(min_sharpe=0.5, max_drawdown=0.20, min_hit_rate=0.45)
    gate = ValidationGate(thresholds)
    result = gate.check({"sharpe": 1.2, "max_drawdown": -0.08, "hit_rate": 0.55, "n_trades": 20})
    assert result.passed is True
    assert result.reason is None


def test_validation_gate_fails_sharpe():
    thresholds = ValidationThresholds(min_sharpe=0.5, max_drawdown=0.20, min_hit_rate=0.45)
    gate = ValidationGate(thresholds)
    result = gate.check({"sharpe": 0.3, "max_drawdown": -0.08, "hit_rate": 0.55, "n_trades": 20})
    assert result.passed is False
    assert "sharpe" in result.reason


def test_validation_gate_fails_drawdown():
    thresholds = ValidationThresholds(min_sharpe=0.5, max_drawdown=0.20, min_hit_rate=0.45)
    gate = ValidationGate(thresholds)
    result = gate.check({"sharpe": 1.2, "max_drawdown": -0.35, "hit_rate": 0.55, "n_trades": 20})
    assert result.passed is False
    assert "drawdown" in result.reason


def test_validation_gate_fails_hit_rate():
    thresholds = ValidationThresholds(min_sharpe=0.5, max_drawdown=0.20, min_hit_rate=0.45)
    gate = ValidationGate(thresholds)
    result = gate.check({"sharpe": 1.2, "max_drawdown": -0.08, "hit_rate": 0.30, "n_trades": 20})
    assert result.passed is False
    assert "hit_rate" in result.reason


def test_validation_gate_fails_missing_key():
    thresholds = ValidationThresholds()
    gate = ValidationGate(thresholds)
    result = gate.check({"sharpe": 1.2})  # missing max_drawdown, hit_rate
    assert result.passed is False
    assert "missing" in result.reason.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_model_sync.py -k "validation_gate" -v
```
Expected: `ImportError` — `model_sync` module doesn't exist yet.

- [ ] **Step 3: Create alphaTrade/store/model_sync.py with ValidationGate**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_model_sync.py -k "validation_gate" -v
```
Expected: 5 PASSED.

- [ ] **Step 5: Commit**

```bash
git add alphaTrade/store/model_sync.py tests/unit/test_model_sync.py
git commit -m "feat(model-sync): ValidationGate checks sharpe/drawdown/hit_rate"
```

---

## Task 3: ModelSyncDaemon — version tracking + promotion

**Files:**
- Modify: `alphaTrade/store/model_sync.py`
- Test: `tests/unit/test_model_sync.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_model_sync.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch
from alphaTrade.store.model_sync import ModelSyncDaemon


def _make_daemon(tmp_path: Path, cfg: Optional[ModelSyncConfig] = None) -> ModelSyncDaemon:
    from alphaTrade.config import MinioConfig, ModelSyncConfig as MSC, ValidationThresholds
    cfg = cfg or MSC()
    return ModelSyncDaemon(
        minio_cfg=MinioConfig(),
        sync_cfg=cfg,
        models_dir=tmp_path / "models",
    )


def test_sync_record_roundtrip(tmp_path):
    daemon = _make_daemon(tmp_path)
    daemon._write_sync_record("AAPL_Transformer", "v3")
    assert daemon._read_sync_record("AAPL_Transformer") == "v3"


def test_sync_record_missing_returns_none(tmp_path):
    daemon = _make_daemon(tmp_path)
    assert daemon._read_sync_record("AAPL_Transformer") is None


def test_promote_copies_and_writes_record(tmp_path):
    daemon = _make_daemon(tmp_path)
    src = tmp_path / "tmp" / "AAPL_Transformer"
    src.mkdir(parents=True)
    (src / "manifest.json").write_text('{"run_name": "AAPL_Transformer"}')
    (src / "model.onnx").write_bytes(b"\x00\x01")

    daemon._promote(src, "AAPL_Transformer", "v2")

    dest = daemon._models_dir / "AAPL_Transformer"
    assert (dest / "manifest.json").exists()
    assert (dest / "model.onnx").exists()
    assert daemon._read_sync_record("AAPL_Transformer") == "v2"


def test_prune_keeps_max_versions(tmp_path):
    from alphaTrade.config import ModelSyncConfig as MSC
    daemon = _make_daemon(tmp_path, cfg=MSC(max_versions=2))
    # Simulate 3 versions in MinIO listing — daemon prunes to 2
    versions = ["v1", "v2", "v3"]
    to_delete = daemon._versions_to_prune(versions, promoted="v3")
    assert to_delete == ["v1"]


def test_prune_unbounded_keeps_all(tmp_path):
    from alphaTrade.config import ModelSyncConfig as MSC
    daemon = _make_daemon(tmp_path, cfg=MSC(max_versions=-1))
    versions = ["v1", "v2", "v3", "v4", "v5"]
    assert daemon._versions_to_prune(versions, promoted="v5") == []
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/unit/test_model_sync.py -k "sync_record or promote or prune" -v
```
Expected: `ImportError` or `AttributeError`.

- [ ] **Step 3: Add ModelSyncDaemon skeleton + version tracking to model_sync.py**

Append to `alphaTrade/store/model_sync.py` (after `ValidationGate`):

```python

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
        (self._sync_dir / run_name).write_text(version)

    def _read_sync_record(self, run_name: str) -> Optional[str]:
        p = self._sync_dir / run_name
        return p.read_text().strip() if p.exists() else None

    # ---------- promotion ----------

    def _promote(self, src: Path, run_name: str, version: str) -> None:
        dest = self._models_dir / run_name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        self._write_sync_record(run_name, version)
        log.info("model_sync: promoted %s %s", run_name, version)

    # ---------- retention ----------

    def _versions_to_prune(self, versions: list[str], promoted: str) -> list[str]:
        if self._cfg.max_versions == -1:
            return []
        sorted_versions = sorted(versions, key=lambda v: int(v[1:]))
        if len(sorted_versions) <= self._cfg.max_versions:
            return []
        return sorted_versions[: len(sorted_versions) - self._cfg.max_versions]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_model_sync.py -k "sync_record or promote or prune" -v
```
Expected: 5 PASSED.

- [ ] **Step 5: Commit**

```bash
git add alphaTrade/store/model_sync.py tests/unit/test_model_sync.py
git commit -m "feat(model-sync): ModelSyncDaemon version tracking, promotion, retention"
```

---

## Task 4: ModelSyncDaemon — MinIO polling loop

**Files:**
- Modify: `alphaTrade/store/model_sync.py`
- Test: `tests/unit/test_model_sync.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_model_sync.py`:

```python
@pytest.mark.asyncio
async def test_sync_once_skips_when_version_unchanged(tmp_path):
    daemon = _make_daemon(tmp_path)
    daemon._write_sync_record("AAPL_Transformer", "v2")

    mock_s3 = MagicMock()
    mock_s3.list_objects_v2 = MagicMock(return_value={
        "CommonPrefixes": [{"Prefix": "models/default/default/AAPL_Transformer/"}]
    })
    mock_s3.get_object = MagicMock(return_value={
        "Body": MagicMock(read=lambda: json.dumps({"version": "v2"}).encode())
    })

    with patch("alphaTrade.store.model_sync.ModelSyncDaemon._make_s3_client", return_value=mock_s3):
        downloaded = await daemon._sync_once()

    assert downloaded == []
    mock_s3.get_object.assert_called_once()  # only fetched latest, no download


@pytest.mark.asyncio
async def test_sync_once_downloads_and_promotes_on_new_version(tmp_path):
    daemon = _make_daemon(tmp_path)
    daemon._write_sync_record("AAPL_Transformer", "v1")

    backtest_data = json.dumps({
        "sharpe": 1.5, "max_drawdown": -0.05, "hit_rate": 0.60, "n_trades": 50
    }).encode()
    manifest_data = json.dumps({"run_name": "AAPL_Transformer", "model_hash": "abc123"}).encode()

    def fake_download(Bucket, Key, Filename):
        p = Path(Filename)
        p.parent.mkdir(parents=True, exist_ok=True)
        if Key.endswith("backtest.json"):
            p.write_bytes(backtest_data)
        elif Key.endswith("manifest.json"):
            p.write_bytes(manifest_data)
        elif Key.endswith("model.onnx"):
            p.write_bytes(b"\x00" * 8)

    mock_s3 = MagicMock()
    mock_s3.list_objects_v2 = MagicMock(side_effect=[
        # first call: list run prefixes
        {"CommonPrefixes": [{"Prefix": "models/default/default/AAPL_Transformer/"}]},
        # second call: list files in v2/
        {"Contents": [
            {"Key": "models/default/default/AAPL_Transformer/v2/manifest.json"},
            {"Key": "models/default/default/AAPL_Transformer/v2/model.onnx"},
            {"Key": "models/default/default/AAPL_Transformer/v2/backtest.json"},
        ]}
    ])
    mock_s3.get_object = MagicMock(return_value={
        "Body": MagicMock(read=lambda: json.dumps({"version": "v2"}).encode())
    })
    mock_s3.download_file = MagicMock(side_effect=fake_download)

    with patch("alphaTrade.store.model_sync.ModelSyncDaemon._make_s3_client", return_value=mock_s3):
        promoted = await daemon._sync_once()

    assert "AAPL_Transformer" in promoted
    assert daemon._read_sync_record("AAPL_Transformer") == "v2"
    assert (tmp_path / "models" / "AAPL_Transformer" / "manifest.json").exists()


@pytest.mark.asyncio
async def test_sync_once_rejects_failed_validation(tmp_path):
    daemon = _make_daemon(tmp_path)

    backtest_data = json.dumps({
        "sharpe": -2.1, "max_drawdown": -0.12, "hit_rate": 0.0, "n_trades": 1
    }).encode()
    manifest_data = json.dumps({"run_name": "AAPL_Transformer", "model_hash": "abc123"}).encode()

    def fake_download(Bucket, Key, Filename):
        p = Path(Filename)
        p.parent.mkdir(parents=True, exist_ok=True)
        if Key.endswith("backtest.json"):
            p.write_bytes(backtest_data)
        elif Key.endswith("manifest.json"):
            p.write_bytes(manifest_data)
        elif Key.endswith("model.onnx"):
            p.write_bytes(b"\x00" * 8)

    mock_s3 = MagicMock()
    mock_s3.list_objects_v2 = MagicMock(side_effect=[
        {"CommonPrefixes": [{"Prefix": "models/default/default/AAPL_Transformer/"}]},
        {"Contents": [
            {"Key": "models/default/default/AAPL_Transformer/v1/manifest.json"},
            {"Key": "models/default/default/AAPL_Transformer/v1/model.onnx"},
            {"Key": "models/default/default/AAPL_Transformer/v1/backtest.json"},
        ]}
    ])
    mock_s3.get_object = MagicMock(return_value={
        "Body": MagicMock(read=lambda: json.dumps({"version": "v1"}).encode())
    })
    mock_s3.download_file = MagicMock(side_effect=fake_download)

    with patch("alphaTrade.store.model_sync.ModelSyncDaemon._make_s3_client", return_value=mock_s3):
        promoted = await daemon._sync_once()

    assert promoted == []
    assert daemon._read_sync_record("AAPL_Transformer") is None
    assert not (tmp_path / "models" / "AAPL_Transformer").exists()
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/unit/test_model_sync.py -k "sync_once" -v
```
Expected: `AttributeError` — `_sync_once` / `_make_s3_client` not defined.

- [ ] **Step 3: Add MinIO polling to ModelSyncDaemon**

Append these methods inside the `ModelSyncDaemon` class in `alphaTrade/store/model_sync.py`:

```python
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

    def _prefix(self) -> str:
        return f"{self._minio_cfg.bucket}/{self._cfg.user}/{self._cfg.account}"

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
        for obj in resp.get("Contents") or []:
            key = obj["Key"]
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

        # prune old remote versions
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_model_sync.py -k "sync_once" -v
```
Expected: 3 PASSED.

- [ ] **Step 5: Run full test_model_sync suite**

```bash
pytest tests/unit/test_model_sync.py -v
```
Expected: all PASSED.

- [ ] **Step 6: Commit**

```bash
git add alphaTrade/store/model_sync.py tests/unit/test_model_sync.py
git commit -m "feat(model-sync): ModelSyncDaemon MinIO polling, download, validate, promote, prune"
```

---

## Task 5: Wire daemon into main.py

**Files:**
- Modify: `alphaTrade/main.py`

- [ ] **Step 1: Add import**

At the top of `alphaTrade/main.py`, alongside other store imports, add:

```python
from alphaTrade.store.model_sync import ModelSyncDaemon
```

- [ ] **Step 2: Instantiate daemon before tasks list**

Find the line `tasks = [` in `main.py` (around line 972). Just before it, add:

```python
    model_sync_daemon: Optional[ModelSyncDaemon] = None
    if settings.model_sync.enabled:
        model_sync_daemon = ModelSyncDaemon(
            minio_cfg=settings.minio,
            sync_cfg=settings.model_sync,
            models_dir=settings.models_dir,
        )
        log.info("model_sync: daemon enabled, polling MinIO every %ds", settings.model_sync.poll_interval)
    else:
        log.info("model_sync: daemon disabled (MODEL_SYNC_ENABLED=false)")
```

- [ ] **Step 3: Add daemon task to tasks list**

After the existing `tasks.append(...)` calls (before `await asyncio.gather(*tasks)`), add:

```python
    if model_sync_daemon is not None:
        tasks.append(asyncio.create_task(model_sync_daemon.run(stop_event=stop_event)))
```

- [ ] **Step 4: Run full unit test suite to check for regressions**

```bash
pytest tests/unit/ -v --tb=short
```
Expected: all existing tests PASSED, no regressions.

- [ ] **Step 5: Commit**

```bash
git add alphaTrade/main.py
git commit -m "feat(model-sync): wire ModelSyncDaemon into main startup"
```

---

## Task 6: Infrastructure (docker-compose + .env.example)

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example`

- [ ] **Step 1: Add MinIO services to docker-compose.yml**

In `docker-compose.yml`, add after the `bot` service (before `volumes:`):

```yaml
  minio:
    image: minio/minio:latest
    networks:
      - appnet
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: ${MINIO_ROOT_USER:-minioadmin}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD:-minioadmin}
    volumes:
      - minio_data:/data
    ports:
      - "9000:9000"
      - "9001:9001"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 10s
      timeout: 5s
      retries: 5

  minio-init:
    image: minio/mc:latest
    networks:
      - appnet
    depends_on:
      minio:
        condition: service_healthy
    entrypoint: >
      /bin/sh -c "
      mc alias set local http://minio:9000 $$MINIO_ROOT_USER $$MINIO_ROOT_PASSWORD;
      mc mb --ignore-existing local/models;
      mc mb --ignore-existing local/mlflow;
      "
    environment:
      MINIO_ROOT_USER: ${MINIO_ROOT_USER:-minioadmin}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD:-minioadmin}
```

- [ ] **Step 2: Add minio_data volume**

In `docker-compose.yml`, in the `volumes:` section, add:

```yaml
  minio_data:
```

- [ ] **Step 3: Add MinIO env vars to bot service**

In the `bot` service `environment:` block, add:

```yaml
      - MINIO__ENDPOINT=minio:9000
      - MINIO__ACCESS_KEY=${MINIO_ROOT_USER:-minioadmin}
      - MINIO__SECRET_KEY=${MINIO_ROOT_PASSWORD:-minioadmin}
      - MINIO__BUCKET=${MINIO_BUCKET:-models}
      - MODEL_SYNC__USER=${MODEL_USER:-default}
      - MODEL_SYNC__ACCOUNT=${MODEL_ACCOUNT:-default}
      - MODEL_SYNC__POLL_INTERVAL=${MODEL_SYNC_POLL_INTERVAL:-60}
      - MODEL_SYNC__MAX_VERSIONS=${MODEL_MAX_VERSIONS:-5}
      - MODEL_SYNC__VALIDATION__MIN_SHARPE=${MODEL_VALIDATION_MIN_SHARPE:-0.5}
      - MODEL_SYNC__VALIDATION__MAX_DRAWDOWN=${MODEL_VALIDATION_MAX_DRAWDOWN:-0.20}
      - MODEL_SYNC__VALIDATION__MIN_HIT_RATE=${MODEL_VALIDATION_MIN_HIT_RATE:-0.45}
```

Note: pydantic-settings uses `__` as nested separator for env vars.

- [ ] **Step 4: Add bot depends_on minio**

In the `bot` service `depends_on:` block, add:

```yaml
      minio:
        condition: service_healthy
```

- [ ] **Step 5: Update .env.example**

Add to `.env.example`:

```bash
# MinIO / model storage
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=minioadmin
MINIO_BUCKET=models
MODEL_USER=default
MODEL_ACCOUNT=default
MODEL_SYNC_POLL_INTERVAL=60
MODEL_MAX_VERSIONS=5          # -1 = unbounded
MODEL_VALIDATION_MIN_SHARPE=0.5
MODEL_VALIDATION_MAX_DRAWDOWN=0.20
MODEL_VALIDATION_MIN_HIT_RATE=0.45
```

- [ ] **Step 6: Verify docker-compose config is valid**

```bash
docker compose config --quiet
```
Expected: exits 0, no errors.

- [ ] **Step 7: Commit**

```bash
git add docker-compose.yml .env.example
git commit -m "feat(model-sync): add MinIO to docker-compose, document env vars"
```

---

## Task 7: Smoke test with live MinIO

**Files:** none (manual verification)

- [ ] **Step 1: Start MinIO locally**

```bash
docker compose up minio minio-init -d
```
Expected: MinIO healthy, `models` and `mlflow` buckets created.

- [ ] **Step 2: Verify MinIO web UI accessible**

Open `http://localhost:9001` in browser. Login: `minioadmin` / `minioadmin`. Verify `models` bucket exists.

- [ ] **Step 3: Upload a test model manually**

```bash
# requires mc (MinIO client) installed locally, or run via docker
docker run --rm --network host \
  -v $(pwd)/models/AAPL_Transformer_test1:/model \
  minio/mc:latest \
  /bin/sh -c "
    mc alias set local http://localhost:9000 minioadmin minioadmin &&
    mc mb --ignore-existing local/models &&
    mc cp --recursive /model/ local/models/default/default/AAPL_Transformer/v1/ &&
    mc cp /dev/stdin local/models/default/default/AAPL_Transformer/latest <<< '{\"version\":\"v1\",\"uploaded_at\":\"2026-05-21T00:00:00Z\",\"run_name\":\"AAPL_Transformer\"}'
  "
```

- [ ] **Step 4: Run bot with model_sync enabled, watch logs**

```bash
MODEL_SYNC__ENABLED=true \
MINIO__ENDPOINT=localhost:9000 \
MODEL_SYNC__POLL_INTERVAL=10 \
python -m alphaTrade.cli
```

Expected log lines:
```
model_sync: daemon started (poll_interval=10s)
model_sync: new version v1 for AAPL_Transformer (local=None)
model_sync: promoted AAPL_Transformer v1
model_registry: hot-added AAPL_Transformer
```

- [ ] **Step 5: Verify promoted model dir**

```bash
ls models/AAPL_Transformer/
cat models/.sync/AAPL_Transformer
```
Expected: model files present, `.sync/AAPL_Transformer` contains `v1`.

- [ ] **Step 6: Run full test suite one final time**

```bash
pytest tests/unit/ -v --tb=short
```
Expected: all PASSED.

- [ ] **Step 7: Final commit**

```bash
git add -A
git commit -m "chore: model-sync smoke test verified"
```
