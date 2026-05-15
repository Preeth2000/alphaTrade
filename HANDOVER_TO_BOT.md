# alphaGen → Trading Bot: Complete Handover

**Date**: 2026-05-09  
**Trainer project**: `alphaGen` (`github.com/preeth2000/alphaGen` or local `/home/preeth/projects/alphaGen`)  
**Bot project**: separate repo — owns Trading212 HTTP, auth, risk, order sizing  
**alphaGen outputs**: `model.onnx` + `manifest.json` only. Bot consumes these two files.

---

## TL;DR

1. Load `manifest.json`, verify `sha256(model.onnx) == manifest["model_hash"]`
2. Fetch OHLCV bars at `manifest["interval"]` for `manifest["ticker"]`
3. Compute TA-Lib indicators; select + order columns by `manifest["feature_names"]`
4. Normalize per `manifest["normalize"]` using stats in `manifest["norm_stats"]`
5. Take last `manifest["window"]` rows → shape per `manifest["input_shape"]`
6. Run ONNX (input tensor `"input"` → output tensor `"output"` shape `(1,3)`)
7. `argmax(logits[0])` → index 0=BUY, 1=SELL, 2=HOLD via `manifest["class_indices"]`

**Bot owns**: sizing, T212 instrument resolution, risk limits, order execution.

---

## Canonical Reference

`examples/inference_reference.py` is the **specification**. Bot adapter must match its normalization and shape logic exactly. Deviating silently produces wrong predictions.

Run to verify a model before deploying:
```bash
att verify \
    --manifest artifacts/aapl_daily_mlp_example/manifest.json \
    --model    artifacts/aapl_daily_mlp_example/model.onnx
```

`att verify` runs: hash check → ONNX shape smoke test → live OHLCV fetch → inference → prints signal + confidence. `--ticker` overrides the manifest ticker. `--json` for machine-readable output.

---

## 1. Artifact Inventory

All artifacts written under `artifacts/<run_name>/`.

| File | Producer (file:line) | Always written | Bot needs |
|---|---|---|---|
| `model.onnx` | `src/att/export/to_onnx.py:48` | yes | **YES** |
| `manifest.json` | `src/att/export/manifest.py:94` | yes | **YES** |
| `config.snapshot.yaml` | `src/att/cli.py:93-95` | yes | if indicator args needed (see §6) |
| `metrics.json` | `src/att/cli.py:138` | yes | no |
| `train.log` | `src/att/cli.py:85-89` | yes | no |
| `checkpoints/fold{N}_best.pt` | `src/att/train/loop.py:150` | yes (one per fold) | no (PyTorch only) |
| `backtest.json` | `src/att/backtest/report.py:71` | only if `backtest.enabled: true` | no |
| `sweep_results.json` | `src/att/sweep/optuna_runner.py:143` | only on `att sweep` | no |

Out-of-tree: `.cache/data/<ticker>_<interval>_<hash>.parquet` — OHLCV download cache, not needed by bot.

**No separate scaler file**. Normalization stats are embedded in `manifest.json["norm_stats"]`.  
**No signature file**. Integrity is via sha256 in `manifest["model_hash"]`.

---

## 2. Manifest Schema (v1.0.0)

Source: `src/att/export/manifest.py:62-92`. Version constant: `MANIFEST_VERSION = "1.0.0"` (line 11).

### Identity / provenance

| Field | Type | Meaning |
|---|---|---|
| `manifest_version` | `str` | Always `"1.0.0"`. Pin major. |
| `run_name` | `str` | Identifies the training run (e.g. `"aapl_daily_mlp_example"`) |
| `model_arch` | `str` | One of `"mlp"`, `"lstm"`, `"cnn"`, `"transformer"` |
| `opset` | `int` | ONNX opset version (default 17). Refuse if onnxruntime too old. |
| `git_sha` | `str` | Short HEAD SHA of alphaGen at training time, or `"unknown"` |
| `model_hash` | `str` | SHA-256 hex of `model.onnx` bytes, or `"unknown"` |

### Inference contract

| Field | Type | Meaning |
|---|---|---|
| `output_format` | `str` | Always `"logits"`. Apply argmax. **Do NOT treat as probabilities.** |
| `input_shape` | `list[int]` | Non-batch dims. MLP: `[window * n_features]`. Others: `[window, n_features]`. |
| `classes` | `list[str]` | Always `["BUY", "SELL", "HOLD"]` |
| `class_indices` | `dict[str, int]` | Always `{"BUY": 0, "SELL": 1, "HOLD": 2}` |

### Feature contract

| Field | Type | Meaning |
|---|---|---|
| `feature_names` | `list[str]` | Ordered column list. Bot must produce features in this exact order. |
| `n_features` | `int` | `len(feature_names)` |
| `window` | `int` | Lookback bars (e.g. 32) |
| `normalize` | `str` | `"zscore"` \| `"minmax"` \| `"none"` |
| `norm_stats` | `object` | Per-feature stats. Keys: `"mean"`, `"std"`, `"min"`, `"max"`. Each maps `feature_name → float`. Fitted on **all training data**. |

### Label contract (training metadata — not live decision params)

| Field | Type | Meaning |
|---|---|---|
| `label_strategy` | `str` | e.g. `"forward_return"`, `"triple_barrier"`. Training-time only. |
| `label_horizon_bars` | `int` | Bars-forward used to generate training labels |
| `label_params` | `dict` | Strategy-specific. e.g. `{"buy_threshold": 0.02, "sell_threshold": -0.02}`. These are **training thresholds**, not live decision thresholds. |

### Data provenance

| Field | Type | Meaning |
|---|---|---|
| `ticker` | `str` | e.g. `"AAPL"`. Trainer-side ticker symbol. |
| `interval` | `str` | `"1m"`, `"5m"`, `"15m"`, `"1h"`, `"1d"`, `"1wk"` |
| `train_data_range` | `object` | `{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}` — first/last bar of training OHLCV |

### Real example (aapl_daily_mlp_example)

```json
{
  "manifest_version": "1.0.0",
  "run_name": "aapl_daily_mlp_example",
  "model_arch": "mlp",
  "opset": 17,
  "git_sha": "8640e66",
  "model_hash": "967785264fc507bee940f4b2f984ff8eb4e48d0a3b49a9ee1e99a2868d6ce187",
  "output_format": "logits",
  "input_shape": [416],
  "feature_names": ["RSI","MACD","MACD_signal","MACD_hist","BBANDS_upper","BBANDS_mid","BBANDS_lower","ATR","Open","High","Low","Close","Volume"],
  "n_features": 13,
  "window": 32,
  "normalize": "zscore",
  "norm_stats": {"mean": {"RSI": 55.14, "...": "..."}, "std": {}, "min": {}, "max": {}},
  "label_strategy": "forward_return",
  "label_horizon_bars": 5,
  "label_params": {"buy_threshold": 0.02, "sell_threshold": -0.02},
  "classes": ["BUY","SELL","HOLD"],
  "class_indices": {"BUY": 0, "SELL": 1, "HOLD": 2},
  "ticker": "AAPL",
  "interval": "1d",
  "train_data_range": {"start": "2015-01-02", "end": "2025-12-30"}
}
```

---

## 3. CRITICAL — Manifest Discrepancies

### 3a. Indicator periods/args NOT in manifest

If a training run used non-default periods (e.g. RSI with `timeperiod: 21`), the manifest does not record this. The column name is still `"RSI"` and the bot has no way to know the period differed.

**Mitigation options (pick one):**
- Assume defaults (what `examples/inference_reference.py` does — safe if trainer uses default configs)
- Load `config.snapshot.yaml` alongside the manifest and read `features.indicators[*].args` for the exact periods used

---

## 4. ONNX I/O Contract

Source: `src/att/export/to_onnx.py:48-57` and `src/att/export/verify.py`.

### Inputs

| Property | Value |
|---|---|
| Tensor name | `"input"` |
| dtype | `float32` |
| Batch axis | Dynamic (axis 0) |
| MLP shape | `(batch, window * n_features)` — flat vector |
| LSTM/CNN/Transformer shape | `(batch, window, n_features)` |

Bot constructs one sample at inference time: batch=1.

### Outputs

| Property | Value |
|---|---|
| Tensor name | `"output"` |
| dtype | `float32` |
| Shape | `(batch, 3)` |
| Interpretation | **Raw logits**, ordered `[BUY, SELL, HOLD]` |

No preprocessing or normalization is baked into the ONNX graph. Bot must do all of it externally.

### Parity guarantee

At training time: max|torch_eager − ort_inference| ≤ 1e-4 over 8 random inputs (seed 42). If this fails, the training run aborts. Bot can replicate this smoke test on artifact load.

### ONNX runtime

Bot only needs `onnxruntime` (CPU). No PyTorch dependency. `CPUExecutionProvider` is sufficient; `CUDAExecutionProvider` optional.

---

## 5. Feature Pipeline Replication

**This is the highest-risk section.** Any deviation in feature computation or normalization order produces silent wrong predictions.

### 5a. Raw data requirements

- OHLCV bars at `manifest["interval"]`
- Required columns (case-sensitive): `Open`, `High`, `Low`, `Close`, `Volume`
- Source: any provider that returns adjusted prices at the correct interval
- Warmup: fetch at least `window + 100` bars to absorb indicator NaN warmup periods

### 5b. Indicator computation

All indicators use TA-Lib (system package: `libta-lib-dev` + Python `ta-lib`). Mirror `src/att/features/registry.py`.

| Column name(s) in manifest | TA-Lib function | Default args |
|---|---|---|
| `RSI` | `talib.RSI(Close, timeperiod=14)` | period=14 |
| `MACD`, `MACD_signal`, `MACD_hist` | `talib.MACD(Close, fastperiod=12, slowperiod=26, signalperiod=9)` | 12/26/9 |
| `BBANDS_upper`, `BBANDS_mid`, `BBANDS_lower` | `talib.BBANDS(Close, timeperiod=20, nbdevup=2, nbdevdn=2)` | 20, ±2σ |
| `ATR` | `talib.ATR(High, Low, Close, timeperiod=14)` | period=14 |
| `EMA_<tp>` | `talib.EMA(Close, timeperiod=tp)` | tp=20 (from column name suffix) |
| `SMA_<tp>` | `talib.SMA(Close, timeperiod=tp)` | tp=20 (from column name suffix) |
| `ADX` | `talib.ADX(High, Low, Close, timeperiod=14)` | period=14 |
| `STOCH_k`, `STOCH_d` | `talib.STOCH(High, Low, Close, fastk_period=5, slowk_period=3, slowd_period=3)` | 5/3/3 |
| `OBV` | `talib.OBV(Close, Volume.astype(float))` | — |
| `Open`, `High`, `Low`, `Close`, `Volume` | raw OHLCV passthrough | — |

Detection: check `feature_names` for substring matches (e.g. `"MACD" in name`). See `examples/inference_reference.py:97-121`.

### 5c. Optional fundamentals

When `features.fundamentals.enabled: true` was set at training time, these columns appear in `feature_names`:
- `reported_eps` — trailing EPS value, forward-filled
- `eps_surprise_pct` — actual vs estimated EPS, forward-filled
- `sector_<name>` — one-hot sector encoding

Source: yfinance `earnings_dates`, shifted by `announcement_lag_days` (default 1 bar) to prevent look-ahead.

If any `feature_names` entries match these patterns and your data source cannot supply them, fail loudly rather than filling with zeros (zeros imply a specific sector/EPS value to the model).

### 5d. Column ordering

```python
df = df[manifest["feature_names"]]   # ORDER IS CRITICAL
```

Missing columns → raise immediately. Never silently fill.

### 5e. Drop NaN rows

After computing all indicators, drop any row with NaN in any feature column:
```python
df = df.dropna()
```

### 5f. Normalization

Apply per `manifest["normalize"]`. All stats from `manifest["norm_stats"]`:

```python
# zscore
x_norm = (x - mean[col]) / (std[col] or 1.0)

# minmax
x_norm = (x - min[col]) / ((max[col] - min[col]) or 1.0)
```

Guard zero-divisor with `or 1.0` — matches training code.

### 5g. Window assembly and final tensor

```python
window = manifest["window"]
arr = feature_df.values[-window:]        # (window, n_features)

if manifest["model_arch"] == "mlp":
    x = arr.flatten()[np.newaxis, :]     # (1, window * n_features)
else:
    x = arr[np.newaxis, :, :]           # (1, window, n_features)

x = x.astype(np.float32)
```

Validate shape against `manifest["input_shape"]` before running inference.

---

## 6. Decision Logic

### Inference call

```python
import onnxruntime as ort
import numpy as np

sess = ort.InferenceSession("model.onnx", providers=["CPUExecutionProvider"])
logits = sess.run(None, {"input": x})[0][0]   # (3,) float32

pred_idx = int(np.argmax(logits))
signal = manifest["class_indices"]             # {"BUY":0,"SELL":1,"HOLD":2}
# reverse lookup
CLASS_NAMES = ["BUY", "SELL", "HOLD"]
decision = CLASS_NAMES[pred_idx]
```

### Signal → order mapping

| Decision | Action |
|---|---|
| `BUY` | positive-quantity MARKET order |
| `SELL` | negative-quantity MARKET order (or close long + open short depending on bot rules) |
| `HOLD` | no order |

### What the manifest does NOT provide

- **No confidence threshold.** There is no `min_confidence` field. If the bot wants gating, implement it by computing `softmax(logits)` and setting a bot-defined threshold.
- **No position sizing.** `BacktestConfig.fraction` (default 0.1 of equity per trade) is a training/backtest parameter only — not propagated to manifest.
- **No risk limits.** Max drawdown, stop-loss, max position count — all bot-side.
- **No T212 instrument mapping.** `manifest["ticker"]` is a yfinance symbol (e.g. `"AAPL"`). Bot owns the mapping to T212's `instrument_ticker` (e.g. `"AAPL_US_EQ"`) — this is executor config, not model config.

### Reference backtest semantics (not a mandate)

`src/att/backtest/engine.py` uses: one position at a time, no pyramiding, close on signal change, cost = `(fee_bps + slippage_bps) / 10_000` both legs. Bot may implement differently.

---

## 7. Versioning and Integrity

### On load, bot must

1. **Hash check**: `sha256(model.onnx bytes) == manifest["model_hash"]` — refuse if mismatch
2. **Version check**: assert `manifest["manifest_version"]` starts with `"1."` (major=1 is supported)
3. **Opset check**: assert onnxruntime supports `manifest["opset"]` (currently 17)
4. **Shape smoke test**: pass dummy `np.zeros((1, *input_shape), dtype=np.float32)` through ONNX, assert output shape `(1, 3)` and dtype `float32`
5. **Feature parity**: assert `len(manifest["feature_names"]) == manifest["n_features"]`

### Hash check implementation (verbatim from `examples/inference_reference.py:46-56`)

```python
import hashlib
from pathlib import Path

def verify_model_hash(manifest: dict, model_path: str) -> None:
    expected = manifest.get("model_hash", "unknown")
    if expected == "unknown":
        print("[WARN] model_hash is 'unknown' — skipping integrity check")
        return
    actual = hashlib.sha256(Path(model_path).read_bytes()).hexdigest()
    assert actual == expected, (
        f"model.onnx hash mismatch!\n  expected: {expected}\n  actual:   {actual}"
    )
```

### Schema evolution

- Minor bumps (`1.1.0`): additive — new optional fields. Bot handles via `manifest.get("field", default)`.
- Major bump (`2.0.0`): breaking change. Bot should refuse to load and alert operator.
- No CHANGELOG for manifest schema exists today in alphaGen. Bot maintainer should track compat matrix in bot repo.

`manifest["git_sha"]` + `manifest["model_hash"]` together uniquely identify an artifact build.

---

## 8. Known Gaps and Limitations

| # | Issue | Impact | Workaround |
|---|---|---|---|
| 1 | Indicator periods not in manifest | Non-default periods cause silent feature mismatch | Read `config.snapshot.yaml` or assume defaults |
| 2 | `feature_names` derivation duck-typed in `pipeline.py:152-174` | Could drift from registry | Trainer risk; bot trusts the list |
| 3 | Norm stats fit on all data, not just train fold | Mild look-ahead leakage, slightly optimistic stats | No action needed by bot |
| 4 | No probability calibration | Raw logits; argmax-only | Implement softmax gating bot-side if desired |
| 5 | No multi-output / regression / volatility head | Single (1,3) output only | — |
| 6 | Fundamentals silently optional on yfinance failure | Training can succeed with missing columns | Validate feature_names before inference |
| 7 | `output_dir` in config snapshot uses literal `./artifacts` | Config not relocatable | Bot doesn't use config snapshot |

---

## 9. Suggested Bot Project Layout

```
t212-bot/
├── adapter/
│   ├── manifest.py        # Manifest dataclass + load/validate
│   ├── features.py        # compute_features(), normalize(), build_input()
│   └── inference.py       # OnnxModel wrapper with hash-check + smoke-test
├── broker/
│   ├── t212_client.py     # Trading212 HTTP client (auth, orders, portfolio)
│   └── instrument_map.py  # yfinance ticker → T212 instrument_ticker
├── risk/
│   └── sizing.py          # Position sizing, drawdown limits, max exposure
├── strategy.py            # Loads manifest+model, calls adapter, applies risk rules
├── tests/
│   └── test_adapter.py    # Unit tests over aapl_daily_mlp_example reference artifact
└── requirements.txt       # onnxruntime, numpy, pandas, ta-lib, requests (no torch)
```

Vendor the manifest schema as a Python dataclass in `adapter/manifest.py`. Unit tests in `test_adapter.py` should point at a checked-in copy of `artifacts/aapl_daily_mlp_example/` from alphaGen — this is the reference artifact and it is stable.

---

## 10. Quick Dependency Reference

Bot pip requirements (no torch needed):

```
onnxruntime>=1.17.0       # opset 17 support
numpy
pandas
ta-lib                    # requires system: apt install libta-lib-dev
yfinance                  # or your own OHLCV data source
```

TA-Lib system install:
```bash
sudo apt-get install -y libta-lib-dev
pip install ta-lib
```

---

*This document was generated from alphaGen source at git sha `d608d3e` on 2026-05-09. Verify against current `src/att/export/manifest.py` if the trainer has been updated since.*
