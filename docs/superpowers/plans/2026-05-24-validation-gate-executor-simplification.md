# Validation Gate — Executor Simplification

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove metric validation from the executor (alphaTrade) — strip `ValidationGate`, `ValidationResult`, and `ValidationThresholds`; keep only structural/artifact integrity checks.

**Architecture:** Producer service owns all metric validation and will never register a bad model to MLflow. Executor's `ModelSyncDaemon` checks only: run_id present, artifacts download, `backtest.json` present. Failure writes to `model_deployments` table as a signal back to producer.

**Tech Stack:** Python 3.11, MLflow, SQLModel/PostgreSQL, pytest-asyncio

---

> ⚠️ **Producer dependency:** This change removes the executor's metric gate. **Do NOT deploy to production until the producer service has implemented `ProducerValidationGate`** (separate spec: `docs/superpowers/specs/2026-05-24-validation-gate-design.md`). Deploying this change alone means models with bad metrics will be promoted.

---

## File Map

| File | Action | What changes |
|---|---|---|
| `tests/unit/test_model_sync.py` | Modify | Remove `ValidationGate` + `ValidationThresholds` tests; add structural-check tests |
| `alphaTrade/store/model_sync.py` | Modify | Remove `ValidationResult`, `ValidationGate`, `_gate`, metric check block; remove `json`/`Any` imports |
| `alphaTrade/config.py` | Modify | Remove `ValidationThresholds` class; remove `validation` field from `ModelSyncConfig` |

---

## Task 1: Rewrite tests — remove metric tests, add structural tests

**Files:**
- Modify: `tests/unit/test_model_sync.py`

- [ ] **Step 1: Open the test file and locate the imports block (line 1–8)**

Current:
```python
from alphaTrade.config import Settings, ValidationThresholds
from alphaTrade.store.model_sync import ModelSyncDaemon, ValidationGate
```

Replace with:
```python
from alphaTrade.config import Settings
from alphaTrade.store.model_sync import ModelSyncDaemon
```

- [ ] **Step 2: Delete `test_settings_has_validation_defaults`**

Delete this entire test function:
```python
def test_settings_has_validation_defaults():
    s = Settings()
    assert s.model_sync.validation.min_sharpe == 0.5
    assert s.model_sync.validation.max_drawdown == 0.20
    assert s.model_sync.validation.min_hit_rate == 0.45
```

- [ ] **Step 3: Delete all five `ValidationGate` unit tests**

Delete these entire test functions:
- `test_validation_gate_passes`
- `test_validation_gate_fails_sharpe`
- `test_validation_gate_fails_drawdown`
- `test_validation_gate_fails_hit_rate`
- `test_validation_gate_fails_missing_key`

- [ ] **Step 4: Delete `test_sync_once_rejects_failed_validation`**

Delete the entire `test_sync_once_rejects_failed_validation` function. It tests metric rejection which is no longer executor's responsibility.

- [ ] **Step 5: Add `test_sync_once_fails_permanently_when_backtest_json_missing`**

Add after `test_sync_once_downloads_and_promotes_on_new_production_version`:

```python
@pytest.mark.asyncio
async def test_sync_once_fails_permanently_when_backtest_json_missing(tmp_path):
    daemon = _make_daemon(tmp_path)

    def fake_download_artifacts(run_id=None, artifact_path=None, dst_path=None):
        dst = Path(dst_path)
        dst.mkdir(parents=True, exist_ok=True)
        (dst / "model.onnx").write_bytes(b"\x00")
        # deliberately no backtest.json
        return str(dst)

    mock_client = MagicMock()
    mock_client.search_registered_models.return_value = [
        _make_mock_registered_model("AAPL_mlp")
    ]
    mock_client.get_model_version_by_alias.side_effect = lambda name, alias: (
        _make_mock_production_version("AAPL_mlp", "1", "run001")
        if alias == "production"
        else (_ for _ in ()).throw(MlflowException("no alias"))
    )

    with patch("alphaTrade.store.model_sync.MlflowClient", return_value=mock_client), \
         patch("alphaTrade.store.model_sync.mlflow") as mock_mlflow:
        mock_mlflow.artifacts.download_artifacts.side_effect = fake_download_artifacts
        promoted = await daemon._sync_once()

    assert promoted == []
    assert daemon._read_sync_record("AAPL_mlp") == "FAILED:1"
```

- [ ] **Step 6: Add `test_sync_once_fails_permanently_when_no_run_id`**

Add after the test above:

```python
@pytest.mark.asyncio
async def test_sync_once_fails_permanently_when_no_run_id(tmp_path):
    daemon = _make_daemon(tmp_path)

    no_run_id_version = MagicMock()
    no_run_id_version.version = "1"
    no_run_id_version.run_id = None
    no_run_id_version.name = "AAPL_mlp"

    mock_client = MagicMock()
    mock_client.search_registered_models.return_value = [
        _make_mock_registered_model("AAPL_mlp")
    ]
    mock_client.get_model_version_by_alias.side_effect = lambda name, alias: (
        no_run_id_version
        if alias == "production"
        else (_ for _ in ()).throw(MlflowException("no alias"))
    )

    with patch("alphaTrade.store.model_sync.MlflowClient", return_value=mock_client), \
         patch("alphaTrade.store.model_sync.mlflow"):
        promoted = await daemon._sync_once()

    assert promoted == []
    assert daemon._read_sync_record("AAPL_mlp") == "FAILED:1"
```

- [ ] **Step 7: Run tests — expect failures because implementation still has ValidationGate**

```bash
python -m pytest tests/unit/test_model_sync.py -v 2>&1 | tail -20
```

Expected: import errors on `ValidationGate`/`ValidationThresholds` (removed from imports), and the two new structural tests fail. If the file parses OK after import fix, expect the two new tests to fail because implementation still reads backtest.json metrics.

---

## Task 2: Strip `ValidationGate` from `model_sync.py`

**Files:**
- Modify: `alphaTrade/store/model_sync.py`

- [ ] **Step 1: Remove `json` and `Any` from imports**

Current line 5: `import json`  
Current line 11: `from typing import Any, Callable, Optional`

After:
```python
from typing import Callable, Optional
```

Remove `import json` entirely (line 5).

- [ ] **Step 2: Remove `ValidationThresholds` from the config import**

Current line 17:
```python
from alphaTrade.config import ModelSyncConfig, ValidationThresholds
```

After:
```python
from alphaTrade.config import ModelSyncConfig
```

- [ ] **Step 3: Delete `ValidationResult` dataclass (lines 23–26)**

Delete:
```python
@dataclass
class ValidationResult:
    passed: bool
    reason: Optional[str] = None
```

- [ ] **Step 4: Delete `ValidationGate` class (lines 28–45)**

Delete:
```python
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

- [ ] **Step 5: Remove `self._gate` from `ModelSyncDaemon.__init__`**

Current line 61:
```python
        self._gate = ValidationGate(sync_cfg.validation)
```

Delete that line entirely.

- [ ] **Step 6: Remove the metric check block from `_sync_once` (lines 198–204)**

Current:
```python
                # Unrecoverable: validation gate
                metrics = json.loads(backtest_path.read_text())
                result = self._gate.check(metrics)
                if not result.passed:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    self._permanently_fail(name, pv.version, f"validation failed: {result.reason}")
                    continue
```

Delete those 7 lines. The `backtest.json` presence check immediately above stays unchanged.

- [ ] **Step 7: Run tests — expect passes**

```bash
python -m pytest tests/unit/test_model_sync.py -v 2>&1 | tail -25
```

Expected: all tests pass. Count should be 14 (was 17; removed 3 net: deleted 7 ValidationGate tests + `test_settings_has_validation_defaults` + `test_sync_once_rejects_failed_validation` = 9 deleted, added 2 new = net -7, so 17 - 7 = 10... let me recount.

Original: 17 tests.  
Deleted: `test_settings_has_validation_defaults` (1) + 5 ValidationGate tests (5) + `test_sync_once_rejects_failed_validation` (1) = 7 deleted.  
Added: 2 new structural tests.  
Expected total: **12 tests passing**.

---

## Task 3: Remove `ValidationThresholds` from `config.py`

**Files:**
- Modify: `alphaTrade/config.py`

- [ ] **Step 1: Delete `ValidationThresholds` class**

Locate and delete (around line 168–172):
```python
class ValidationThresholds(BaseModel):
    min_sharpe: float = 0.5
    max_drawdown: float = 0.20      # absolute value — backtest.json stores negative
    min_hit_rate: float = 0.45
```

- [ ] **Step 2: Remove `validation` field from `ModelSyncConfig`**

Locate in `ModelSyncConfig` (around line 194) and delete:
```python
    validation: ValidationThresholds = ValidationThresholds()
```

- [ ] **Step 3: Run full test suite**

```bash
python -m pytest tests/unit/test_model_sync.py -v 2>&1 | tail -25
```

Expected: 12 tests passing, 0 failures.

- [ ] **Step 4: Run any other tests that might import these**

```bash
python -m pytest tests/ -q 2>&1 | tail -15
```

Expected: no new failures. If any test imports `ValidationThresholds` or `ValidationGate` from outside `test_model_sync.py`, fix the import there too.

---

## Task 4: Commit

- [ ] **Step 1: Verify clean diff**

```bash
git diff --stat
```

Expected: 3 files changed (`model_sync.py`, `config.py`, `tests/unit/test_model_sync.py`).

- [ ] **Step 2: Stage and commit**

```bash
git add alphaTrade/store/model_sync.py alphaTrade/config.py tests/unit/test_model_sync.py
git commit -m "feat: remove metric validation from executor — producer owns gate

ValidationGate, ValidationResult, ValidationThresholds removed.
Executor now does structural checks only: run_id present, artifacts
download, backtest.json present. Producer service will validate metrics
before registering to MLflow.

⚠️  Do not deploy until producer ProducerValidationGate is live."
```

- [ ] **Step 3: Verify**

```bash
git show --stat HEAD
python -m pytest tests/unit/test_model_sync.py -q
```

Expected: 12 passed.
