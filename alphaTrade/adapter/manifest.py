"""Manifest dataclass, validation, and integrity checks. See HANDOVER §2 and §7."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, model_validator


SUPPORTED_MANIFEST_MAJOR = "1"


class NormStats(BaseModel):
    mean: dict[str, float] = {}
    std: dict[str, float] = {}
    min: dict[str, float] = {}
    max: dict[str, float] = {}


class Manifest(BaseModel):
    manifest_version: str
    run_name: str
    model_arch: str
    opset: int
    git_sha: str
    model_hash: str

    output_format: str
    input_shape: list[int]
    classes: list[str]
    class_indices: dict[str, int]

    feature_names: list[str]
    n_features: int
    window: int
    normalize: str
    norm_stats: NormStats | dict[str, Any]

    label_strategy: str = ""
    label_horizon_bars: int = 0
    label_params: dict[str, Any] = {}

    ticker: str
    interval: str
    train_data_range: dict[str, str] = {}

    @model_validator(mode="after")
    def _validate(self) -> "Manifest":
        if not self.manifest_version.startswith(f"{SUPPORTED_MANIFEST_MAJOR}."):
            raise ValueError(
                f"Unsupported manifest major version: {self.manifest_version}. "
                f"Only major={SUPPORTED_MANIFEST_MAJOR} is supported."
            )
        if len(self.feature_names) != self.n_features:
            raise ValueError(
                f"feature_names length {len(self.feature_names)} != n_features {self.n_features}"
            )
        if self.output_format != "logits":
            raise ValueError(f"Unknown output_format: {self.output_format!r}. Expected 'logits'.")
        return self

    @classmethod
    def load(cls, manifest_path: str | Path) -> "Manifest":
        import json
        data = json.loads(Path(manifest_path).read_text())
        return cls.model_validate(data)

    def verify_model_hash(self, model_path: str | Path) -> None:
        if self.model_hash == "unknown":
            import warnings
            warnings.warn("model_hash is 'unknown' — skipping integrity check")
            return
        actual = hashlib.sha256(Path(model_path).read_bytes()).hexdigest()
        if actual != self.model_hash:
            raise ValueError(
                f"model.onnx hash mismatch!\n  expected: {self.model_hash}\n  actual:   {actual}"
            )
