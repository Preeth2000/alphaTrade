# OCO SL/TP via T212 Stop+Limit Orders

**Date:** 2026-05-10  
**Issue:** alphaTrade-h6c  
**Status:** Approved

## Problem

T212 market orders have no native SL/TP parameters. Without broker-side protection, a filled BUY position has no automatic exit — it relies solely on the model emitting a SELL signal. Risk gates (daily-loss halt, cooldown) are portfolio-level, not per-position price-based exits.

## Solution

After every BUY fill: submit a STOP SELL and a LIMIT SELL to T212, then run an asyncio OCO monitor that polls both orders and cancels the surviving leg once one fills.

## Architecture

```
BUY fill (fill_price known)
  ├─ t212.place_stop_order(ticker, qty, fill_price*(1-sl_pct))   → stop_id
  ├─ t212.place_limit_order(ticker, qty, fill_price*(1+tp_pct))  → limit_id
  └─ asyncio.create_task(monitor_oco(...))
                    │
                    └─ polls GET /equity/orders/{id} every 10s
                       one fills → cancel other → pos_repo.remove → cooldown → done
```

## Components

### `alphaTrade/broker/t212_client.py` (additions)

Four new methods:

| Method | HTTP | Endpoint |
|---|---|---|
| `place_stop_order(ticker, qty, stop_price)` | POST | `/equity/orders/stop` |
| `place_limit_order(ticker, qty, limit_price)` | POST | `/equity/orders/limit` |
| `get_order(order_id)` | GET | `/equity/orders/{id}` |
| `cancel_order(order_id)` | DELETE | `/equity/orders/{id}` |

Request body for stop: `{"ticker": ..., "quantity": ..., "stopPrice": ...}`  
Request body for limit: `{"ticker": ..., "quantity": ..., "limitPrice": ...}`  
`cancel_order` calls `_delete` (new private method); no JSON body returned.

### `alphaTrade/broker/oco_monitor.py` (new file)

```python
async def monitor_oco(
    t212: T212Client,
    t212_ticker: str,
    stop_order_id: str,
    limit_order_id: str,
    engine,
    cooldown_td: timedelta,
    poll_interval_s: float = 10.0,
) -> None
```

Loop logic:
1. `await asyncio.sleep(poll_interval_s)`
2. Poll `get_order(stop_order_id)` and `get_order(limit_order_id)`
3. Poll error → log, continue (don't crash)
4. STOP status == `"FILLED"` → cancel limit leg → update DB → `wh.notify` → break
5. LIMIT status == `"FILLED"` → cancel stop leg → update DB → `wh.notify` → break
6. Both in `{"CANCELLED", "REJECTED"}` → log warning, break
7. DB update: `pos_repo.remove(t212_ticker)` + `pos_repo.upsert(Position(quantity=0, cooldown_until_ts=now+cooldown_td))`
8. Uses own `Session(engine)` — tick session is already closed by then

### `alphaTrade/main.py` (BUY branch)

After `pos_repo.upsert(...)`, add:

```python
sl_price = (fill_price or current_price) * (1 - settings.defaults.stop_loss_pct)
tp_price = (fill_price or current_price) * (1 + settings.defaults.take_profit_pct)
try:
    stop_resp = t212.place_stop_order(t212_ticker, qty, sl_price)
    limit_resp = t212.place_limit_order(t212_ticker, qty, tp_price)
    asyncio.create_task(monitor_oco(
        t212=t212,
        t212_ticker=t212_ticker,
        stop_order_id=str(stop_resp["id"]),
        limit_order_id=str(limit_resp["id"]),
        engine=engine,
        cooldown_td=cooldown_td,
    ))
except Exception as exc:
    log.error("OCO setup failed for %s: %s", t212_ticker, exc)
```

OCO failure is non-fatal — fill is already recorded, position is open.

### Config

No changes. `settings.defaults.stop_loss_pct` (default 0.02) and `settings.defaults.take_profit_pct` (default 0.05) already exist.

## Error Handling

| Scenario | Behaviour |
|---|---|
| T212 rejects stop/limit order | Log error, no OCO task spawned. Position open with no auto-exit. |
| Poll error (network, 429) | Log error, sleep, retry next iteration |
| Cancel of other leg fails | Log warning, position/cooldown still updated (fill was real) |
| Both legs externally cancelled | Log warning, exit loop. Position left open (model SELL will close it). |

## Tests

### `tests/unit/test_oco.py`
- Stop fills → limit cancelled, position removed, cooldown set
- TP fills → stop cancelled, position removed, cooldown set
- Both cancelled externally → loop exits cleanly
- Poll error → loop continues, does not raise

Uses mocked `T212Client` and in-memory SQLite engine. No network.

### `tests/integration/test_oco.py`
- Adds stop/limit/get_order/cancel_order routes to `mock_t212/responses.py`
- Full BUY → OCO submit → poll → fill path using `respx.mock`

### `tests/integration/mock_t212/responses.py`
New mock routes added:
- `POST /equity/orders/stop` → `{"id": "stop-001", "status": "PENDING"}`
- `POST /equity/orders/limit` → `{"id": "limit-001", "status": "PENDING"}`
- `GET /equity/orders/stop-001` → configurable status
- `GET /equity/orders/limit-001` → configurable status
- `DELETE /equity/orders/{id}` → 200
