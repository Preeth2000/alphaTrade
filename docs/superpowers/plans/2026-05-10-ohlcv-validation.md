# OHLCV Gap and Volume Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `alphaTrade/adapter/validators.py` with three new checks: Close-outside-range (raises), zero-volume bars (warns), and timestamp gap detection (warns).

**Architecture:** All changes are confined to two files — `validators.py` (implementation) and `test_validators.py` (tests). The existing `validate_ohlcv` function is extended in-place; no API change, no caller changes. A module-level logger is added for the two new warning checks.

**Tech Stack:** pandas, Python logging, pytest caplog fixture.

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `alphaTrade/adapter/validators.py` | Add logger + 3 new checks |
| Modify | `tests/unit/test_validators.py` | Add 9 new tests across 3 classes |

---

## Task 1: Close-outside-range check

**Files:**
- Modify: `alphaTrade/adapter/validators.py`
- Modify: `tests/unit/test_validators.py`

The check raises `ValueError` if any row has `Close > High` or `Close < Low`. No logger needed — it's a hard error like the existing `High < Low` check.

- [ ] **Step 1: Write failing tests**

Add this class to `tests/unit/test_validators.py` (after `TestHighLowChecks`):

```python
class TestCloseConsistency:
    def test_raises_close_above_high(self):
        df = _make_df()
        df.loc[df.index[1], "Close"] = 200.0  # above High=105
        with pytest.raises(ValueError, match="Close outside"):
            validate_ohlcv(df, _INTERVAL)

    def test_raises_close_below_low(self):
        df = _make_df()
        df.loc[df.index[1], "Close"] = 50.0  # below Low=95
        with pytest.raises(ValueError, match="Close outside"):
            validate_ohlcv(df, _INTERVAL)

    def test_passes_close_at_high_boundary(self):
        df = _make_df()
        df.loc[df.index[0], "Close"] = 105.0  # exactly High — valid
        validate_ohlcv(df, _INTERVAL)  # no raise

    def test_passes_close_at_low_boundary(self):
        df = _make_df()
        df.loc[df.index[0], "Close"] = 95.0  # exactly Low — valid
        validate_ohlcv(df, _INTERVAL)  # no raise
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
pytest tests/unit/test_validators.py::TestCloseConsistency -v
```

Expected: `FAILED` — `test_raises_close_above_high` and `test_raises_close_below_low` pass without raising (check doesn't exist yet).

- [ ] **Step 3: Implement Close-outside-range check**

In `alphaTrade/adapter/validators.py`, after the existing `if (df["Low"] < 0).any():` block (currently the last OHLC check, around line 68), add:

```python
    if (df["Close"] > df["High"]).any() or (df["Close"] < df["Low"]).any():
        raise ValueError(f"{tag}Close outside [Low, High] on some rows")
```

- [ ] **Step 4: Run tests — verify all pass**

```bash
pytest tests/unit/test_validators.py::TestCloseConsistency -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Run full validator suite to check for regressions**

```bash
pytest tests/unit/test_validators.py -v
```

Expected: all existing tests + 4 new = all PASS.

- [ ] **Step 6: Commit**

```bash
git add alphaTrade/adapter/validators.py tests/unit/test_validators.py
git commit -m "feat: validate Close within [Low, High] range (alphaTrade-5pw)"
```

---

## Task 2: Zero-volume warning

**Files:**
- Modify: `alphaTrade/adapter/validators.py`
- Modify: `tests/unit/test_validators.py`

Adds a module-level logger and emits `log.warning` when any bar has `Volume == 0`. Does not raise — zero-volume bars are legitimate (market halt, illiquid instrument). Tests use pytest's `caplog` fixture to assert warning presence/absence.

- [ ] **Step 1: Write failing tests**

Add `import logging` to the imports at the top of `tests/unit/test_validators.py`:

```python
import logging
```

Then add this class after `TestCloseConsistency`:

```python
class TestZeroVolumeWarning:
    def test_warns_on_zero_volume(self, caplog):
        df = _make_df()
        df.loc[df.index[0], "Volume"] = 0
        with caplog.at_level(logging.WARNING, logger="alphaTrade.adapter.validators"):
            validate_ohlcv(df, _INTERVAL)
        assert "zero-volume" in caplog.text

    def test_no_warning_all_nonzero(self, caplog):
        df = _make_df()
        with caplog.at_level(logging.WARNING, logger="alphaTrade.adapter.validators"):
            validate_ohlcv(df, _INTERVAL)
        assert "zero-volume" not in caplog.text
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
pytest tests/unit/test_validators.py::TestZeroVolumeWarning -v
```

Expected: `test_warns_on_zero_volume` FAILS — no warning emitted yet.

- [ ] **Step 3: Add logger and zero-volume warning**

In `alphaTrade/adapter/validators.py`:

**3a.** Add `import logging` and the module logger. Change the imports block from:

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
```

To:

```python
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

log = logging.getLogger(__name__)
```

**3b.** After the existing `if (df["Volume"] < 0).any():` block (around line 62), add:

```python
    zero_vol = int((df["Volume"] == 0).sum())
    if zero_vol:
        log.warning("%szero-volume bars: %d row(s)", tag, zero_vol)
```

- [ ] **Step 4: Run tests — verify all pass**

```bash
pytest tests/unit/test_validators.py::TestZeroVolumeWarning -v
```

Expected: 2 tests PASS.

- [ ] **Step 5: Run full suite**

```bash
pytest tests/unit/test_validators.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add alphaTrade/adapter/validators.py tests/unit/test_validators.py
git commit -m "feat: warn on zero-volume bars (alphaTrade-5pw)"
```

---

## Task 3: Timestamp gap detection

**Files:**
- Modify: `alphaTrade/adapter/validators.py`
- Modify: `tests/unit/test_validators.py`

Emits `log.warning` when any consecutive bar gap exceeds `interval_seconds × 3`. Uses `pd.Series(df.index).diff().dt.total_seconds()` on the DatetimeIndex. Skips silently for non-datetime indices (same guard pattern as existing staleness check).

- [ ] **Step 1: Write failing tests**

Add `from datetime import datetime, timedelta, timezone` is already imported in the test file. Also add `import pandas as pd` is already imported. Add this class after `TestZeroVolumeWarning`:

```python
class TestTimestampGaps:
    def test_warns_on_gap_exceeding_3x_interval(self, caplog):
        # _make_df creates 1h-spaced bars. Shift bars 2-4 forward by 3h to
        # create a 4h gap between bar 1 and bar 2 (threshold = 3h for 1h interval).
        df = _make_df(n=5)
        idx = df.index.tolist()
        shift = timedelta(hours=3)
        idx[2] = idx[2] + shift
        idx[3] = idx[3] + shift
        idx[4] = idx[4] + shift
        df.index = pd.DatetimeIndex(idx)
        with caplog.at_level(logging.WARNING, logger="alphaTrade.adapter.validators"):
            validate_ohlcv(df, _INTERVAL)
        assert "timestamp gap" in caplog.text

    def test_no_warning_on_normal_gaps(self, caplog):
        df = _make_df(n=5)
        with caplog.at_level(logging.WARNING, logger="alphaTrade.adapter.validators"):
            validate_ohlcv(df, _INTERVAL)
        assert "timestamp gap" not in caplog.text

    def test_skips_gap_check_for_non_datetime_index(self, caplog):
        df = _make_df()
        df.index = range(len(df))
        with caplog.at_level(logging.WARNING, logger="alphaTrade.adapter.validators"):
            validate_ohlcv(df, _INTERVAL)
        assert "timestamp gap" not in caplog.text
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
pytest tests/unit/test_validators.py::TestTimestampGaps -v
```

Expected: `test_warns_on_gap_exceeding_3x_interval` FAILS — no gap warning emitted yet.

- [ ] **Step 3: Implement gap detection**

In `alphaTrade/adapter/validators.py`, at the very end of `validate_ohlcv` (after the entire staleness check block, as the last block before the function ends), add:

```python
    # Timestamp gap detection
    if hasattr(df.index, "to_pydatetime"):
        try:
            diffs = pd.Series(df.index).diff().dt.total_seconds().dropna()
            threshold = _INTERVAL_SECONDS.get(interval, 86400) * 3
            gaps = diffs[diffs > threshold]
            if not gaps.empty:
                log.warning(
                    "%s%d timestamp gap(s) detected (largest: %.0fs, threshold: %ds)",
                    tag, len(gaps), gaps.max(), threshold,
                )
        except (AttributeError, TypeError):
            pass
```

- [ ] **Step 4: Run tests — verify all pass**

```bash
pytest tests/unit/test_validators.py::TestTimestampGaps -v
```

Expected: 3 tests PASS.

- [ ] **Step 5: Run full suite including all other unit tests**

```bash
pytest tests/unit/ -v 2>&1 | tail -5
```

Expected: all 134+ tests PASS.

- [ ] **Step 6: Commit**

```bash
git add alphaTrade/adapter/validators.py tests/unit/test_validators.py
git commit -m "feat: timestamp gap detection warning (alphaTrade-5pw)"
```
