# Credential Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `POST /api/v1/verify/{t212,polygon,alphatrade-key}` endpoints so the frontend can validate credentials without saving them.

**Architecture:** New `verify` router (`alphaTrade/api/routers/verify.py`) mounted in `app.py`. T212 and Polygon calls are single-attempt `httpx` requests (no retries — fast failure on bad creds). alphaTrade key endpoint is protected by existing auth middleware; handler returns `{"valid": true}`.

**Tech Stack:** FastAPI, httpx, pydantic, pytest, respx (for mocking httpx in tests)

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `alphaTrade/api/routers/verify.py` | Create | Three verify endpoints |
| `alphaTrade/api/app.py` | Modify | Mount verify router |
| `tests/unit/test_api_verify.py` | Create | Unit tests for all endpoints |

---

### Task 1: T212 verify endpoint (TDD)

**Files:**
- Create: `alphaTrade/api/routers/verify.py`
- Create: `tests/unit/test_api_verify.py`

- [ ] **Step 1: Install respx** (needed for mocking httpx in tests)

```bash
pip install respx
```

Expected: installs without error.

- [ ] **Step 2: Write failing tests for T212 verify**

Create `tests/unit/test_api_verify.py`:

```python
from __future__ import annotations
import pytest
import respx
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import create_engine
from alphaTrade.store.db import run_migrations
from alphaTrade.api.auth import make_api_key_dep


def _make_client(tmp_path):
    db = tmp_path / "test.db"
    run_migrations(db)
    engine = create_engine(f"sqlite:///{db}")
    api_key_dep = make_api_key_dep(engine)
    from alphaTrade.api.routers.verify import make_router
    app = FastAPI()
    app.include_router(make_router(api_key_dep), prefix="/api/v1")
    return TestClient(app)


# --- T212 ---

@respx.mock
def test_t212_demo_valid(tmp_path):
    respx.get("https://demo.trading212.com/api/v0/equity/account/summary").mock(
        return_value=httpx.Response(200, json={"cash": {"free": 1000.0}, "pieCash": 0.0})
    )
    client = _make_client(tmp_path)
    resp = client.post("/api/v1/verify/t212", json={
        "account": "demo", "api_key": "test-key", "secret_key": "test-secret"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["account"] == "demo"
    assert "details" in data


@respx.mock
def test_t212_demo_invalid_credentials(tmp_path):
    respx.get("https://demo.trading212.com/api/v0/equity/account/summary").mock(
        return_value=httpx.Response(401, json={"message": "Unauthorized"})
    )
    client = _make_client(tmp_path)
    resp = client.post("/api/v1/verify/t212", json={
        "account": "demo", "api_key": "bad-key", "secret_key": "bad-secret"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is False
    assert "error" in data


@respx.mock
def test_t212_invest_uses_live_url(tmp_path):
    respx.get("https://live.trading212.com/api/v0/equity/account/summary").mock(
        return_value=httpx.Response(200, json={"cash": {"free": 500.0}, "pieCash": 0.0})
    )
    client = _make_client(tmp_path)
    resp = client.post("/api/v1/verify/t212", json={
        "account": "invest", "api_key": "key", "secret_key": "secret"
    })
    assert resp.status_code == 200
    assert resp.json()["valid"] is True


@respx.mock
def test_t212_isa_uses_live_url(tmp_path):
    respx.get("https://live.trading212.com/api/v0/equity/account/summary").mock(
        return_value=httpx.Response(200, json={"cash": {"free": 200.0}, "pieCash": 0.0})
    )
    client = _make_client(tmp_path)
    resp = client.post("/api/v1/verify/t212", json={
        "account": "isa", "api_key": "key", "secret_key": "secret"
    })
    assert resp.status_code == 200
    assert resp.json()["valid"] is True


@respx.mock
def test_t212_network_error_returns_invalid(tmp_path):
    respx.get("https://demo.trading212.com/api/v0/equity/account/summary").mock(
        side_effect=httpx.ConnectError("timeout")
    )
    client = _make_client(tmp_path)
    resp = client.post("/api/v1/verify/t212", json={
        "account": "demo", "api_key": "key", "secret_key": "secret"
    })
    assert resp.status_code == 200
    assert resp.json()["valid"] is False
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
cd /home/preeth/projects/alphaTrade
pytest tests/unit/test_api_verify.py -v 2>&1 | head -30
```

Expected: `ImportError` or `ModuleNotFoundError` — `verify` module doesn't exist yet.

- [ ] **Step 4: Create `alphaTrade/api/routers/verify.py` with T212 endpoint**

```python
from __future__ import annotations
from typing import Any, Literal
from collections.abc import Callable

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel

_T212_BASE = {
    "demo": "https://demo.trading212.com/api/v0",
    "live": "https://live.trading212.com/api/v0",
}
_T212_ENV_MAP = {
    "demo": "demo",
    "invest": "live",
    "isa": "live",
}
_POLYGON_EXCHANGES_URL = "https://api.polygon.io/v1/meta/exchanges"
_TIMEOUT = 10.0


class T212VerifyRequest(BaseModel):
    account: Literal["demo", "invest", "isa"]
    api_key: str
    secret_key: str


class PolygonVerifyRequest(BaseModel):
    api_key: str


def make_router(api_key_dep: Callable) -> APIRouter:
    router = APIRouter()

    @router.post("/verify/t212")
    def verify_t212(
        body: T212VerifyRequest,
        _: None = Depends(api_key_dep),
    ) -> dict[str, Any]:
        env = _T212_ENV_MAP[body.account]
        base = _T212_BASE[env]
        url = f"{base}/equity/account/summary"
        try:
            r = httpx.get(
                url,
                auth=httpx.BasicAuth(body.api_key, body.secret_key),
                timeout=_TIMEOUT,
            )
            if r.status_code == 200:
                return {"valid": True, "account": body.account, "details": r.json()}
            return {"valid": False, "account": body.account, "error": f"{r.status_code} {r.reason_phrase}"}
        except httpx.HTTPError as exc:
            return {"valid": False, "account": body.account, "error": str(exc)}

    return router
```

- [ ] **Step 5: Run T212 tests to verify they pass**

```bash
pytest tests/unit/test_api_verify.py -k "t212" -v
```

Expected: all 5 T212 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add alphaTrade/api/routers/verify.py tests/unit/test_api_verify.py
git commit -m "feat(verify): T212 credential verification endpoint"
```

---

### Task 2: Polygon verify endpoint (TDD)

**Files:**
- Modify: `alphaTrade/api/routers/verify.py`
- Modify: `tests/unit/test_api_verify.py`

- [ ] **Step 1: Add Polygon tests to `tests/unit/test_api_verify.py`**

Append to the file:

```python
# --- Polygon ---

@respx.mock
def test_polygon_valid_returns_details(tmp_path):
    exchanges = [{"id": 1, "name": "NYSE"}, {"id": 2, "name": "NASDAQ"}]
    respx.get("https://api.polygon.io/v1/meta/exchanges").mock(
        return_value=httpx.Response(
            200,
            json=exchanges,
            headers={"X-RateLimit-Limit": "5"},
        )
    )
    client = _make_client(tmp_path)
    resp = client.post("/api/v1/verify/polygon", json={"api_key": "good-key"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["details"]["exchanges_count"] == 2
    assert data["details"]["rate_limit"] == "5"


@respx.mock
def test_polygon_invalid_key(tmp_path):
    respx.get("https://api.polygon.io/v1/meta/exchanges").mock(
        return_value=httpx.Response(403, json={"status": "ERROR", "error": "Forbidden"})
    )
    client = _make_client(tmp_path)
    resp = client.post("/api/v1/verify/polygon", json={"api_key": "bad-key"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is False
    assert "error" in data


@respx.mock
def test_polygon_network_error(tmp_path):
    respx.get("https://api.polygon.io/v1/meta/exchanges").mock(
        side_effect=httpx.ConnectError("unreachable")
    )
    client = _make_client(tmp_path)
    resp = client.post("/api/v1/verify/polygon", json={"api_key": "key"})
    assert resp.status_code == 200
    assert resp.json()["valid"] is False


@respx.mock
def test_polygon_no_rate_limit_header(tmp_path):
    respx.get("https://api.polygon.io/v1/meta/exchanges").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "name": "NYSE"}])
    )
    client = _make_client(tmp_path)
    resp = client.post("/api/v1/verify/polygon", json={"api_key": "key"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["details"]["rate_limit"] is None
    assert data["details"]["exchanges_count"] == 1
```

- [ ] **Step 2: Run Polygon tests to confirm they fail**

```bash
pytest tests/unit/test_api_verify.py -k "polygon" -v
```

Expected: FAIL — `POST /api/v1/verify/polygon` returns 404 (endpoint doesn't exist yet).

- [ ] **Step 3: Add Polygon endpoint to `alphaTrade/api/routers/verify.py`**

Inside `make_router`, after the T212 endpoint, add:

```python
    @router.post("/verify/polygon")
    def verify_polygon(
        body: PolygonVerifyRequest,
        _: None = Depends(api_key_dep),
    ) -> dict[str, Any]:
        try:
            r = httpx.get(
                _POLYGON_EXCHANGES_URL,
                params={"apiKey": body.api_key},
                timeout=_TIMEOUT,
            )
            if r.status_code == 200:
                exchanges = r.json() if isinstance(r.json(), list) else []
                rate_limit = r.headers.get("X-RateLimit-Limit")
                return {
                    "valid": True,
                    "details": {
                        "exchanges_count": len(exchanges),
                        "rate_limit": rate_limit,
                    },
                }
            return {"valid": False, "error": f"{r.status_code} {r.reason_phrase}"}
        except httpx.HTTPError as exc:
            return {"valid": False, "error": str(exc)}
```

- [ ] **Step 4: Run Polygon tests to verify they pass**

```bash
pytest tests/unit/test_api_verify.py -k "polygon" -v
```

Expected: all 4 Polygon tests PASS.

- [ ] **Step 5: Commit**

```bash
git add alphaTrade/api/routers/verify.py tests/unit/test_api_verify.py
git commit -m "feat(verify): Polygon credential verification endpoint"
```

---

### Task 3: alphaTrade key verify endpoint (TDD)

**Files:**
- Modify: `alphaTrade/api/routers/verify.py`
- Modify: `tests/unit/test_api_verify.py`

- [ ] **Step 1: Add alphaTrade key tests to `tests/unit/test_api_verify.py`**

Append to the file:

```python
# --- alphaTrade API key ---

def test_alphatrade_key_no_key_configured_returns_valid(tmp_path, monkeypatch):
    monkeypatch.delenv("alphaTrade_API_KEY", raising=False)
    client = _make_client(tmp_path)
    resp = client.post("/api/v1/verify/alphatrade-key")
    assert resp.status_code == 200
    assert resp.json() == {"valid": True}


def test_alphatrade_key_correct_key_returns_valid(tmp_path, monkeypatch):
    monkeypatch.setenv("alphaTrade_API_KEY", "secret")
    client = _make_client(tmp_path)
    resp = client.post("/api/v1/verify/alphatrade-key", headers={"X-API-Key": "secret"})
    assert resp.status_code == 200
    assert resp.json() == {"valid": True}


def test_alphatrade_key_wrong_key_returns_403(tmp_path, monkeypatch):
    monkeypatch.setenv("alphaTrade_API_KEY", "secret")
    client = _make_client(tmp_path)
    resp = client.post("/api/v1/verify/alphatrade-key", headers={"X-API-Key": "wrong"})
    assert resp.status_code == 403


def test_alphatrade_key_missing_header_returns_403(tmp_path, monkeypatch):
    monkeypatch.setenv("alphaTrade_API_KEY", "secret")
    client = _make_client(tmp_path)
    resp = client.post("/api/v1/verify/alphatrade-key")
    assert resp.status_code == 403
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/unit/test_api_verify.py -k "alphatrade" -v
```

Expected: FAIL — `POST /api/v1/verify/alphatrade-key` returns 404.

- [ ] **Step 3: Add alphaTrade key endpoint to `alphaTrade/api/routers/verify.py`**

Inside `make_router`, after the Polygon endpoint, add:

```python
    @router.post("/verify/alphatrade-key")
    def verify_alphatrade_key(
        _: None = Depends(api_key_dep),
    ) -> dict[str, Any]:
        return {"valid": True}
```

- [ ] **Step 4: Run alphaTrade key tests to verify they pass**

```bash
pytest tests/unit/test_api_verify.py -k "alphatrade" -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add alphaTrade/api/routers/verify.py tests/unit/test_api_verify.py
git commit -m "feat(verify): alphaTrade API key verification endpoint"
```

---

### Task 4: Wire verify router into app

**Files:**
- Modify: `alphaTrade/api/app.py`

- [ ] **Step 1: Add verify router import and mount in `alphaTrade/api/app.py`**

In `create_app`, find the line:
```python
    from alphaTrade.api.routers import positions, orders, signals, pnl, models, backtest, health, settings, equity, trades, stream, kill_switch
```

Replace with:
```python
    from alphaTrade.api.routers import positions, orders, signals, pnl, models, backtest, health, settings, equity, trades, stream, kill_switch, verify
```

Then after the last `app.include_router(...)` call (the `kill_switch` line), add:
```python
    app.include_router(verify.make_router(api_key_dep), prefix="/api/v1")
```

- [ ] **Step 2: Run the full verify test suite**

```bash
pytest tests/unit/test_api_verify.py -v
```

Expected: all 13 tests PASS.

- [ ] **Step 3: Run full unit test suite to check for regressions**

```bash
pytest tests/unit/ -v --tb=short 2>&1 | tail -20
```

Expected: all tests pass (same count as before this task started).

- [ ] **Step 4: Smoke test via curl (optional, requires running server)**

```bash
curl -s -X POST http://localhost:8081/api/v1/verify/alphatrade-key | python3 -m json.tool
```

Expected: `{"valid": true}` (when no key configured).

- [ ] **Step 5: Commit**

```bash
git add alphaTrade/api/app.py
git commit -m "feat(verify): mount verify router in app"
```
