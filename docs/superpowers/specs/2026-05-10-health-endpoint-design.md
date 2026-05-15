# Health Check HTTP Endpoint — Design Spec

**Date:** 2026-05-10
**Issue:** alphaTrade-79k
**Status:** Approved

## Overview

Expose a lightweight HTTP server on `:8080` alongside the existing asyncio event loop. Two endpoints support Docker `HEALTHCHECK` and Kubernetes-style readiness probes. Uses `aiohttp` for the server; state is shared via a plain dataclass updated by the main tick loop.

## Components

### `alphaTrade/health.py` (new)

**`HealthState` dataclass** — shared mutable state, no locks needed (single asyncio thread):

```python
@dataclass
class HealthState:
    last_tick_at: datetime | None = None
    longest_interval_seconds: int = 3600
    t212_ok: bool = False
    models_loaded: bool = False
```

**`start_health_server(state, port=8080)`** — coroutine that starts aiohttp `TCPSite` bound to `0.0.0.0:8080`. Returns the `AppRunner` so the caller can clean up on shutdown.

### Endpoints

`GET /healthz` — liveness (is the bot processing ticks?):

| Condition | Status | Body |
|-----------|--------|------|
| `last_tick_at` is `None` | 503 | `no tick yet` |
| age > `2 × longest_interval_seconds` | 503 | `stale: {age:.0f}s > {threshold:.0f}s` |
| otherwise | 200 | `ok` |

`GET /readyz` — readiness (are dependencies up?):

| Condition | Status | Body |
|-----------|--------|------|
| `models_loaded` is `False` | 503 | `no models loaded` |
| `t212_ok` is `False` | 503 | `t212 unreachable` |
| otherwise | 200 | `ok` |

### Changes to `alphaTrade/main.py`

Data flow at startup (inside `run()`):

1. Create `HealthState()`
2. Probe T212 once via `t212.get_total_equity()` in `asyncio.to_thread` → set `state.t212_ok`
3. After `registry.refresh()` → `state.models_loaded = bool(registry.by_run_name)`
4. `state.longest_interval_seconds = max(_INTERVAL_SECONDS[i] for i in by_interval)`
5. `asyncio.create_task(start_health_server(state))` alongside scheduler tasks

Per-tick updates (inside `tick()`):

- `get_total_equity()` success → `state.t212_ok = True`; exception → `state.t212_ok = False`
- End of tick (after all signals processed) → `state.last_tick_at = datetime.now(timezone.utc)`

### T212 reachability strategy

Active probe **once at startup** to set initial `t212_ok`. After that, `/readyz` reflects the result of the last tick's `get_total_equity()` call — no extra API calls per health probe, no T212 rate-limit risk.

### Infrastructure changes

**`pyproject.toml`** — add `aiohttp>=3.9` to `dependencies`.

**`Dockerfile`** — add after existing ENV block:
```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD wget -qO- http://localhost:8080/healthz || exit 1
```

**`docker-compose.yml`** — add to `alphaTrade` service:
```yaml
ports:
  - "8080:8080"
```

## Error Handling

- If `start_health_server` raises (e.g. port in use), log error and continue — health server failure must not kill the trading loop.
- `/healthz` and `/readyz` handlers must not raise; catch all exceptions and return 503.

## Testing

- Unit tests in `tests/unit/test_health.py`
- Test `/healthz`: no tick (503), stale tick (503), fresh tick (200)
- Test `/readyz`: no models (503), t212 down (503), both ok (200)
- Use `aiohttp.test_utils.TestClient` for handler tests (no real server needed)
