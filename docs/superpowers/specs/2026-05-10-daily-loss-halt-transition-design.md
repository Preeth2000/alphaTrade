# Daily-Loss-Halt: Alert on Transition Only — Design Spec

**Date:** 2026-05-10  
**Issue:** alphaLink-a73  
**Priority:** P2

## Problem

`wh.notify()` fires every tick while `daily_loss_halted` is True. A rate-limit (30s) caps spam, but the alert should fire exactly once per halt event — on the `False → True` transition.

## Design

Add a closure variable `_prev_halt: list[bool] = [False]` in `make_tick()`, before `async def tick()`. Each tick:

1. Compute `daily_loss_halted` as before.
2. Call `wh.notify()` and `log.warning()` **only** when `daily_loss_halted and not _prev_halt[0]`.
3. Set `_prev_halt[0] = daily_loss_halted` unconditionally at the end of the halt block.

`list[bool]` used instead of a bare `bool` to allow mutation from within the nested closure without `nonlocal`.

## Change

**File:** `alphalink/main.py`

Before `async def tick():` in `make_tick()`, add:
```python
_prev_halt: list[bool] = [False]
```

Replace the existing `if daily_loss_halted:` block with:
```python
if daily_loss_halted and not _prev_halt[0]:
    log.warning("Daily loss halt active (%.2f%%). No new orders.", daily_loss_pct * 100)
    wh.notify(
        "WARNING",
        f"Daily loss halt active ({daily_loss_pct:.2%}). No new orders.",
        category="daily-loss-halt",
    )
_prev_halt[0] = daily_loss_halted
```

The `log.warning` moves inside the transition-only block (was firing every tick too).

## Testing

**File:** `tests/unit/test_daily_loss_halt_transition.py`

- Two ticks with `daily_loss_pct` below threshold → `wh.notify` called exactly once (first tick), not twice.
- First tick not halted, second tick halted → `wh.notify` called on second tick only.
- Halt clears (pct recovers above threshold) then drops again → `wh.notify` fires again on re-entry.

## Out of Scope

- Recovery notification (halt lifted alert)
- Halt state persistence across restarts
