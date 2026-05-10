# Health Check HTTP Endpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose `/healthz` (liveness) and `/readyz` (readiness) on `:8080` via aiohttp, wired into the existing asyncio event loop with Docker HEALTHCHECK support.

**Architecture:** A `HealthState` dataclass holds shared mutable state (last tick timestamp, T212 status, models loaded). The aiohttp server reads this state on every probe request — no locking needed (single asyncio thread). The main tick loop updates state; T212 is probed once at startup then updated on each tick's `get_total_equity()` call.

**Tech Stack:** aiohttp>=3.9, pytest-asyncio (already installed), aiohttp.test_utils for unit tests.

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `alphalink/health.py` | `HealthState`, `make_app`, `start_health_server` |
| Create | `tests/unit/test_health.py` | Unit tests for both endpoints |
| Modify | `alphalink/main.py` | Wire HealthState: startup probe, per-tick updates, start server |
| Modify | `pyproject.toml` | Add `aiohttp>=3.9` dependency |
| Modify | `Dockerfile` | Add `HEALTHCHECK` directive |
| Modify | `docker-compose.yml` | Expose port 8080 |

---

## Task 1: Add aiohttp dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add aiohttp to dependencies**

In `pyproject.toml`, add `"aiohttp>=3.9",` to the `dependencies` list after `"httpx>=0.27",`:

```toml
    "httpx>=0.27",
    "aiohttp>=3.9",
```

- [ ] **Step 2: Install**

```bash
pip install -e ".[dev]"
```

Expected: installs aiohttp without errors.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add aiohttp>=3.9 for health endpoint"
```

---

## Task 2: Write failing tests for health.py

**Files:**
- Create: `tests/unit/test_health.py`

- [ ] **Step 1: Write all six tests**

Create `tests/unit/test_health.py`:

```python
from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta

from aiohttp.test_utils import TestClient, TestServer

from alphalink.health import HealthState, make_app


@pytest.fixture
async def health_client():
    state = HealthState()
    app = make_app(state)
    client = TestClient(TestServer(app))
    await client.start_server()
    yield client, state
    await client.close()


async def test_healthz_no_tick(health_client):
    client, _ = health_client
    resp = await client.get("/healthz")
    assert resp.status == 503
    assert "no tick yet" in await resp.text()


async def test_healthz_stale(health_client):
    client, state = health_client
    state.last_tick_at = datetime.now(timezone.utc) - timedelta(seconds=7201)
    state.longest_interval_seconds = 3600
    resp = await client.get("/healthz")
    assert resp.status == 503
    assert "stale" in await resp.text()


async def test_healthz_ok(health_client):
    client, state = health_client
    state.last_tick_at = datetime.now(timezone.utc) - timedelta(seconds=60)
    state.longest_interval_seconds = 3600
    resp = await client.get("/healthz")
    assert resp.status == 200
    assert await resp.text() == "ok"


async def test_readyz_no_models(health_client):
    client, state = health_client
    state.t212_ok = True
    resp = await client.get("/readyz")
    assert resp.status == 503
    assert "no models loaded" in await resp.text()


async def test_readyz_t212_down(health_client):
    client, state = health_client
    state.models_loaded = True
    state.t212_ok = False
    resp = await client.get("/readyz")
    assert resp.status == 503
    assert "t212 unreachable" in await resp.text()


async def test_readyz_ok(health_client):
    client, state = health_client
    state.models_loaded = True
    state.t212_ok = True
    resp = await client.get("/readyz")
    assert resp.status == 200
    assert await resp.text() == "ok"
```

- [ ] **Step 2: Run tests — verify they fail with ImportError**

```bash
pytest tests/unit/test_health.py -v
```

Expected: `ImportError: cannot import name 'HealthState' from 'alphalink.health'` (module doesn't exist yet).

---

## Task 3: Implement health.py — make tests pass

**Files:**
- Create: `alphalink/health.py`

- [ ] **Step 1: Create the module**

Create `alphalink/health.py`:

```python
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from aiohttp import web

log = logging.getLogger(__name__)


@dataclass
class HealthState:
    last_tick_at: datetime | None = None
    longest_interval_seconds: int = 3600
    t212_ok: bool = False
    models_loaded: bool = False


def make_app(state: HealthState) -> web.Application:
    app = web.Application()

    async def healthz(request: web.Request) -> web.Response:
        if state.last_tick_at is None:
            return web.Response(status=503, text="no tick yet")
        now = datetime.now(timezone.utc)
        age = (now - state.last_tick_at).total_seconds()
        threshold = state.longest_interval_seconds * 2
        if age > threshold:
            return web.Response(status=503, text=f"stale: {age:.0f}s > {threshold:.0f}s")
        return web.Response(status=200, text="ok")

    async def readyz(request: web.Request) -> web.Response:
        if not state.models_loaded:
            return web.Response(status=503, text="no models loaded")
        if not state.t212_ok:
            return web.Response(status=503, text="t212 unreachable")
        return web.Response(status=200, text="ok")

    app.router.add_get("/healthz", healthz)
    app.router.add_get("/readyz", readyz)
    return app


async def start_health_server(state: HealthState, port: int = 8080) -> web.AppRunner:
    app = make_app(state)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("Health server listening on :%d", port)
    return runner
```

- [ ] **Step 2: Run tests — verify all pass**

```bash
pytest tests/unit/test_health.py -v
```

Expected: 6 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add alphalink/health.py tests/unit/test_health.py
git commit -m "feat: health endpoint module with /healthz and /readyz (alphaLink-79k)"
```

---

## Task 4: Wire HealthState into main.py

**Files:**
- Modify: `alphalink/main.py`

- [ ] **Step 1: Add import**

At the top of `alphalink/main.py`, add after the existing first-party imports block (after line 43 `from alphalink.store.repos import ...`):

```python
from alphalink.health import HealthState, start_health_server
```

- [ ] **Step 2: Create HealthState and startup T212 probe in run()**

In `alphalink/main.py`, inside `async def run(settings: Settings) -> None:`, after the line `t212 = T212Client(api_key=settings.t212_api_key, env=settings.t212_env)` (line 147), add:

```python
    health_state = HealthState()
    try:
        await asyncio.to_thread(t212.get_total_equity)
        health_state.t212_ok = True
    except Exception as exc:
        log.warning("T212 startup probe failed: %s", exc)
        health_state.t212_ok = False
```

- [ ] **Step 3: Set models_loaded and longest_interval after registry loads**

After the block `if not registry.by_run_name:` check (after line 153), add:

```python
    health_state.models_loaded = bool(registry.by_run_name)
    health_state.longest_interval_seconds = max(
        (_INTERVAL_SECONDS.get(i, 3600) for i in registry.snapshot_by_interval()),
        default=3600,
    )
```

- [ ] **Step 4: Start health server as asyncio task in run()**

After the `by_interval = registry.snapshot_by_interval()` line (line 174), add:

```python
    health_runner = None
    try:
        health_runner = await start_health_server(health_state)
    except Exception as exc:
        log.error("Health server failed to start on :8080: %s", exc)
```

- [ ] **Step 5: Update models_loaded and t212_ok inside tick()**

Inside `async def tick() -> None:` (inside `make_tick`), after the line `await registry.refresh(settings.models_dir, settings.model_overrides)`, add:

```python
            health_state.models_loaded = bool(registry.by_run_name)
```

Then update the `get_total_equity()` try/except block. Change from:

```python
                try:
                    equity = t212.get_total_equity()
                except Exception as exc:
                    log.error("Cannot fetch equity: %s. Skipping tick.", exc)
                    return
```

To:

```python
                try:
                    equity = t212.get_total_equity()
                    health_state.t212_ok = True
                except Exception as exc:
                    log.error("Cannot fetch equity: %s. Skipping tick.", exc)
                    health_state.t212_ok = False
                    return
```

- [ ] **Step 6: Update last_tick_at at end of tick()**

At the very end of the `tick()` function body — after the closing of the `with Session(engine) as session:` block (at 12-space indent, before `return tick`) — add:

```python
            health_state.last_tick_at = datetime.now(timezone.utc)
```

The final structure of `tick()` should end like:

```python
        async def tick() -> None:
            # ... all existing code ...
            with Session(engine) as session:
                # ... existing session code ...
                for yf_ticker, signal in signals.items():
                    # ... existing loop ...
            health_state.last_tick_at = datetime.now(timezone.utc)

        return tick
```

- [ ] **Step 7: Cleanup health runner on shutdown**

In the shutdown section of `run()`, after the OCO task cancellation block (after `log.info("Graceful shutdown complete.")`), add cleanup before that log line:

```python
    if health_runner is not None:
        await health_runner.cleanup()
    log.info("Graceful shutdown complete.")
```

- [ ] **Step 8: Run existing tests to verify no regressions**

```bash
pytest tests/unit/ -v
```

Expected: all unit tests PASS.

- [ ] **Step 9: Commit**

```bash
git add alphalink/main.py
git commit -m "feat: wire HealthState into main loop (alphaLink-79k)"
```

---

## Task 5: Dockerfile and docker-compose

**Files:**
- Modify: `Dockerfile`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add HEALTHCHECK to Dockerfile**

In `Dockerfile`, add after the `ENV` block (after `STATE_DB_PATH=/app/state.db`) and before `ENTRYPOINT`:

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD wget -qO- http://localhost:8080/healthz || exit 1
```

The end of the file should look like:

```dockerfile
ENV PYTHONUNBUFFERED=1 \
    T212_ENV=demo \
    DATA_PROVIDER=yfinance \
    MODELS_DIR=/app/models \
    STATE_DB_PATH=/app/state.db

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD wget -qO- http://localhost:8080/healthz || exit 1

ENTRYPOINT ["alphalink"]
CMD ["run"]
```

- [ ] **Step 2: Expose port in docker-compose.yml**

In `docker-compose.yml`, add `ports` to the `alphalink` service after `restart: unless-stopped`:

```yaml
services:
  alphalink:
    build: .
    env_file: .env
    environment:
      - T212_ENV=${T212_ENV:-demo}
      - DATA_PROVIDER=${DATA_PROVIDER:-yfinance}
      - MODELS_DIR=/app/models
      - STATE_DB_PATH=/app/state.db
    volumes:
      - ./models:/app/models:ro
      - ./state.db:/app/state.db
      - ./overrides.yaml:/app/overrides.yaml:ro
    ports:
      - "8080:8080"
    restart: unless-stopped
```

- [ ] **Step 3: Run full test suite one final time**

```bash
pytest tests/unit/ -v
```

Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add Dockerfile docker-compose.yml
git commit -m "feat: Docker HEALTHCHECK + expose :8080 (alphaLink-79k)"
```
