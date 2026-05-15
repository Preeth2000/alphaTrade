# OHLCV Gap and Volume Validation — Design Spec

**Date:** 2026-05-10
**Issue:** alphaTrade-5pw
**Status:** Approved

## Overview

Extend `alphaTrade/adapter/validators.py` with three new checks. Two are warnings (logged, inference continues); one is a hard error (raises, inference aborts). No API change — callers unaffected.

## Existing Checks (unchanged)

- Empty DataFrame
- Missing columns
- NaN in OHLC
- Non-positive prices (Open/High/Low/Close <= 0)
- Negative Volume
- High < Low
- Stale latest bar

## New Checks

### 1. Close outside [Low, High] — ValueError

**Condition:** `(df["Close"] > df["High"]).any() or (df["Close"] < df["Low"]).any()`

**Behavior:** Raises `ValueError` with message `"{tag}Close outside [Low, High] on some rows"`.

**Rationale:** Hard data corruption — a close outside the candle range is physically impossible. Abort inference.

### 2. Zero-volume bar — log.warning

**Condition:** `(df["Volume"] == 0).any()`

**Behavior:** `log.warning("%szero-volume bars: %d row(s)", tag, count)` where count is the number of zero-volume rows. Does not raise.

**Rationale:** Zero-volume bars are legitimate (market halt, no trades in interval) but worth surfacing for operational awareness.

### 3. Timestamp gap detection — log.warning

**Condition:** Any consecutive bar gap > `_INTERVAL_SECONDS[interval] × 3`

**Behavior:**
```
log.warning("%s%d timestamp gap(s) detected (largest: %.0fs, threshold: %ds)",
            tag, gap_count, largest_gap, threshold)
```

**Implementation:**
```python
if hasattr(df.index, "to_pydatetime"):
    try:
        diffs = pd.Series(df.index).diff().dt.total_seconds().dropna()
        threshold = _INTERVAL_SECONDS.get(interval, 86400) * 3
        gaps = diffs[diffs > threshold]
        if not gaps.empty:
            log.warning(...)
    except (AttributeError, TypeError):
        pass  # non-datetime index — skip
```

**Rationale:** Gaps indicate missing bars (data source issue, exchange halt). Warning not error — weekend/holiday gaps are expected and model trained on gappy data. Threshold of 3× interval avoids false positives on regular market closures.

## Module Changes

**File:** `alphaTrade/adapter/validators.py`

1. Add `import logging` and `log = logging.getLogger(__name__)` at module level.
2. Add Close-outside-range check after the existing High < Low check (both are OHLC consistency checks — group them together).
3. Add zero-volume warning after the existing negative-Volume check.
4. Add timestamp gap warning after the existing staleness check (both use the DatetimeIndex — group them together).

## Test Changes

**File:** `tests/unit/test_validators.py`

New test classes:

```
TestCloseConsistency
  test_raises_close_above_high
  test_raises_close_below_low
  test_passes_close_at_high_boundary
  test_passes_close_at_low_boundary

TestZeroVolumeWarning
  test_warns_on_zero_volume (use caplog)
  test_no_warning_all_nonzero

TestTimestampGaps
  test_warns_on_gap_exceeding_3x_interval (use caplog)
  test_no_warning_on_normal_gaps
  test_skips_gap_check_for_non_datetime_index
```

## Out of Scope

- Adding `validate_ohlcv` call to `polygon_provider.py` (separate issue)
- Configurable gap thresholds
- NaN in Volume column (already caught indirectly by non-positive/negative checks if NaN propagates; explicit NaN Volume check is separate scope)
