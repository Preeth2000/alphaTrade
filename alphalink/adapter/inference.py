"""ONNX model wrapper. Hash check + smoke test on load. HANDOVER §4 and §7."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from alphaTrade.adapter.manifest import Manifest


class OnnxModel:
    def __init__(self, manifest: Manifest, model_path: str | Path) -> None:
        import onnxruntime as ort

        model_path = Path(model_path)
        manifest.verify_model_hash(model_path)
        self._validate_opset(manifest.opset)

        self._sess = ort.InferenceSession(
            str(model_path), providers=["CPUExecutionProvider"]
        )
        self._smoke_test(manifest)
        self.manifest = manifest

    def run(self, x: np.ndarray) -> np.ndarray:
        """Run inference. x: (1, *input_shape) float32. Returns logits (3,)."""
        outputs = self._sess.run(None, {"input": x})
        return outputs[0][0]  # (3,) float32

    @staticmethod
    def _validate_opset(opset: int) -> None:
        import onnxruntime as ort
        supported = ort.get_device()  # just ensure ort is importable
        _ = supported
        # onnxruntime 1.17+ supports opset 17
        if opset > 20:
            raise ValueError(f"opset {opset} may not be supported by installed onnxruntime")

    def _smoke_test(self, manifest: Manifest) -> None:
        dummy = np.zeros((1, *manifest.input_shape), dtype=np.float32)
        out = self._sess.run(None, {"input": dummy})
        assert out[0].shape == (1, 3), (
            f"ONNX smoke test: expected output shape (1, 3), got {out[0].shape}"
        )
        assert out[0].dtype == np.float32, (
            f"ONNX smoke test: expected float32 output, got {out[0].dtype}"
        )
