# T212 Multi-Account Key Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store separate API key pairs for T212 Demo, Live Invest, and Live ISA accounts, with an active account selector that hot-reloads the T212Client when changed.

**Architecture:** Add 6 new credential columns + 1 active account selector to `BotSettings` via a new Alembic migration. `apply_bot_settings` selects the right key pair based on `t212_active_account`, derives the env (demo vs live), and reinitialises `T212Client` when anything changes. Existing `t212_api_key`/`t212_secret_key`/`t212_env`/`t212_account_type` fields are removed — their data is migrated to the new columns.

**Tech Stack:** SQLAlchemy/Alembic (migrations), SQLModel (ORM), FastAPI (API layer), Python dataclasses (Settings)

---

## File Map

| File | Change |
|---|---|
| `alphaTrade/store/repos.py` | Replace old t212 fields with 6 key-pair columns + `t212_active_account` |
| `alphaTrade/store/migrations/versions/0008_t212_multi_account.py` | New migration: add columns, migrate existing data, drop old columns |
| `alphaTrade/config.py` | Replace `t212_api_key`/`t212_secret_key`/`t212_env` with `t212_active_account` + 6 key fields |
| `alphaTrade/main.py` | Update `apply_bot_settings` + startup T212Client init |
| `alphaTrade/api/routers/settings.py` | Update `BotSettingsUpdate` and `_SENSITIVE` |
| `tests/unit/test_api_routers.py` | Update existing t212 settings tests, add new account switching tests |
| `tests/unit/test_apply_bot_settings.py` | New: unit tests for apply_bot_settings account switching logic |

---

### Task 1: Update BotSettings ORM model

**Files:**
- Modify: `alphaTrade/store/repos.py:443-469`

- [ ] **Step 1: Replace old t212 fields in BotSettings**

Open `alphaTrade/store/repos.py`. Replace the block:
```python
class BotSettings(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    t212_api_key: str = Field(default="")
    t212_secret_key: str = Field(default="")
    t212_env: str = Field(default="demo")
    t212_account_type: str = Field(default="invest")
```
with:
```python
class BotSettings(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    t212_active_account: str = Field(default="demo")  # "demo" | "invest" | "isa"
    t212_demo_api_key: str = Field(default="")
    t212_demo_secret_key: str = Field(default="")
    t212_invest_api_key: str = Field(default="")
    t212_invest_secret_key: str = Field(default="")
    t212_isa_api_key: str = Field(default="")
    t212_isa_secret_key: str = Field(default="")
```

- [ ] **Step 2: Verify the model parses (no import errors)**

```bash
cd /home/preeth/projects/alphaTrade
python -c "from alphaTrade.store.repos import BotSettings; print(BotSettings.model_fields.keys())"
```
Expected output includes: `t212_active_account`, `t212_demo_api_key`, `t212_demo_secret_key`, `t212_invest_api_key`, `t212_invest_secret_key`, `t212_isa_api_key`, `t212_isa_secret_key`

- [ ] **Step 3: Commit**

```bash
git add alphaTrade/store/repos.py
git commit -m "refactor(store): replace t212 single key pair with per-account key pairs"
```

---

### Task 2: Write the Alembic migration

**Files:**
- Create: `alphaTrade/store/migrations/versions/0008_t212_multi_account.py`

- [ ] **Step 1: Write the migration**

```python
"""Replace t212 single key pair with per-account key pairs.

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-17
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    cols = [row[1] for row in conn.execute(sa.text("PRAGMA table_info(botsettings)"))]

    new_cols = [
        ("t212_active_account", "demo"),
        ("t212_demo_api_key", ""),
        ("t212_demo_secret_key", ""),
        ("t212_invest_api_key", ""),
        ("t212_invest_secret_key", ""),
        ("t212_isa_api_key", ""),
        ("t212_isa_secret_key", ""),
    ]
    for col_name, default in new_cols:
        if col_name not in cols:
            op.add_column(
                "botsettings",
                sa.Column(col_name, sa.String(), nullable=False, server_default=default),
            )

    # Migrate existing t212_api_key/t212_secret_key into demo slot
    # (system defaulted to demo env, so existing keys are demo keys)
    if "t212_api_key" in cols:
        conn.execute(sa.text(
            "UPDATE botsettings SET t212_demo_api_key = t212_api_key "
            "WHERE t212_api_key != '' AND t212_demo_api_key = ''"
        ))
    if "t212_secret_key" in cols:
        conn.execute(sa.text(
            "UPDATE botsettings SET t212_demo_secret_key = t212_secret_key "
            "WHERE t212_secret_key != '' AND t212_demo_secret_key = ''"
        ))

    # Drop old columns (SQLite workaround: recreate table)
    # SQLite doesn't support DROP COLUMN before 3.35 — use recreate approach
    conn.execute(sa.text("""
        CREATE TABLE botsettings_new AS
        SELECT
            id,
            t212_active_account,
            t212_demo_api_key,
            t212_demo_secret_key,
            t212_invest_api_key,
            t212_invest_secret_key,
            t212_isa_api_key,
            t212_isa_secret_key,
            data_provider,
            polygon_api_key,
            slack_enabled,
            slack_webhook_url,
            slack_min_level,
            email_enabled,
            email_smtp_host,
            email_smtp_port,
            email_smtp_user,
            email_smtp_password,
            email_from_addr,
            email_to_addrs,
            email_min_level,
            size_pct,
            stop_loss_pct,
            take_profit_pct,
            cooldown_bars,
            extended_hours,
            max_positions,
            daily_loss_halt_pct,
            alphaTrade_api_key
        FROM botsettings
    """))
    conn.execute(sa.text("DROP TABLE botsettings"))
    conn.execute(sa.text("ALTER TABLE botsettings_new RENAME TO botsettings"))


def downgrade() -> None:
    conn = op.get_bind()
    # Restore old columns, copy demo keys back
    cols = [row[1] for row in conn.execute(sa.text("PRAGMA table_info(botsettings)"))]
    for col_name in ("t212_api_key", "t212_secret_key", "t212_env", "t212_account_type"):
        if col_name not in cols:
            default = "demo" if col_name == "t212_env" else ("invest" if col_name == "t212_account_type" else "")
            op.add_column(
                "botsettings",
                sa.Column(col_name, sa.String(), nullable=False, server_default=default),
            )
    conn.execute(sa.text(
        "UPDATE botsettings SET t212_api_key = t212_demo_api_key, "
        "t212_secret_key = t212_demo_secret_key"
    ))
```

- [ ] **Step 2: Run migration against a test DB to verify**

```bash
cd /home/preeth/projects/alphaTrade
python -c "
from alphaTrade.store.db import get_engine
from alphaTrade.store.migrations.runner import run_migrations
import tempfile, pathlib
with tempfile.TemporaryDirectory() as d:
    engine = get_engine(pathlib.Path(d) / 'test.db')
    run_migrations(engine)
    from sqlalchemy import text
    with engine.connect() as conn:
        cols = [r[1] for r in conn.execute(text('PRAGMA table_info(botsettings)'))]
        print('columns:', cols)
"
```
Expected: columns list includes `t212_active_account`, `t212_demo_api_key`, `t212_demo_secret_key`, `t212_invest_api_key`, `t212_invest_secret_key`, `t212_isa_api_key`, `t212_isa_secret_key`. Does NOT include `t212_api_key`, `t212_secret_key`, `t212_env`, `t212_account_type`.

- [ ] **Step 3: Commit**

```bash
git add alphaTrade/store/migrations/versions/0008_t212_multi_account.py
git commit -m "feat(migration): 0008 — t212 per-account key pairs, migrate existing demo keys"
```

---

### Task 3: Update Settings (config.py)

**Files:**
- Modify: `alphaTrade/config.py`

- [ ] **Step 1: Replace t212 fields in Settings**

Find the `Settings` class in `alphaTrade/config.py`. Replace:
```python
    t212_api_key: str = ""
    t212_secret_key: str = ""
    t212_env: str = "demo"
```
with:
```python
    t212_active_account: str = "demo"  # "demo" | "invest" | "isa"
    t212_demo_api_key: str = ""
    t212_demo_secret_key: str = ""
    t212_invest_api_key: str = ""
    t212_invest_secret_key: str = ""
    t212_isa_api_key: str = ""
    t212_isa_secret_key: str = ""
```

- [ ] **Step 2: Remove the old t212_env validator**

Find and remove this validator block:
```python
        if self.t212_env not in ("demo", "live"):
            raise ValueError(f"T212_ENV must be 'demo' or 'live', got {self.t212_env!r}")
```
Replace with:
```python
        if self.t212_active_account not in ("demo", "invest", "isa"):
            raise ValueError(f"t212_active_account must be 'demo', 'invest', or 'isa', got {self.t212_active_account!r}")
```

- [ ] **Step 3: Verify config parses**

```bash
cd /home/preeth/projects/alphaTrade
python -c "from alphaTrade.config import Settings; s = Settings(); print(s.t212_active_account)"
```
Expected: `demo`

- [ ] **Step 4: Commit**

```bash
git add alphaTrade/config.py
git commit -m "refactor(config): replace t212 single key pair with per-account fields"
```

---

### Task 4: Update apply_bot_settings and startup in main.py

**Files:**
- Modify: `alphaTrade/main.py`

- [ ] **Step 1: Add helper to extract key pair from BotSettings**

Add this function just before `apply_bot_settings` in `alphaTrade/main.py`:

```python
def _t212_credentials(db_s) -> tuple[str, str, str]:
    """Return (api_key, secret_key, env) for the active T212 account."""
    account = db_s.t212_active_account or "demo"
    env = "demo" if account == "demo" else "live"
    if account == "demo":
        return db_s.t212_demo_api_key or "", db_s.t212_demo_secret_key or "", env
    if account == "invest":
        return db_s.t212_invest_api_key or "", db_s.t212_invest_secret_key or "", env
    if account == "isa":
        return db_s.t212_isa_api_key or "", db_s.t212_isa_secret_key or "", env
    return "", "", env
```

- [ ] **Step 2: Update apply_bot_settings to use _t212_credentials**

Replace the T212 credential block inside `apply_bot_settings`:
```python
    from alphaTrade.broker.t212_client import T212Client
    new_key = db_s.t212_api_key or ""
    new_secret = db_s.t212_secret_key or ""
    new_env = db_s.t212_env or "demo"
    current_auth = getattr(t212_holder[0], "_auth", None)
    current_key = getattr(current_auth, "username", None) if current_auth else getattr(t212_holder[0], "_headers", {}).get("Authorization")
    if new_key and current_key != new_key:
        t212_holder[0] = T212Client(api_key=new_key, secret_key=new_secret, env=new_env)
        log.info("Hot-reload: T212Client reinitialised (env=%s)", new_env)
```
with:
```python
    from alphaTrade.broker.t212_client import T212Client
    new_key, new_secret, new_env = _t212_credentials(db_s)
    current_auth = getattr(t212_holder[0], "_auth", None)
    current_key = getattr(current_auth, "username", None) if current_auth else getattr(t212_holder[0], "_headers", {}).get("Authorization")
    current_base = getattr(t212_holder[0], "_base", None)
    from alphaTrade.broker.t212_client import _BASE_URLS
    new_base = _BASE_URLS.get(new_env)
    if new_key and (current_key != new_key or current_base != new_base):
        t212_holder[0] = T212Client(api_key=new_key, secret_key=new_secret, env=new_env)
        log.info("Hot-reload: T212Client reinitialised (account=%s, env=%s)", db_s.t212_active_account, new_env)
```

- [ ] **Step 3: Update startup T212Client init**

Find the startup line (around line 643):
```python
    t212 = T212Client(api_key=settings.t212_api_key, secret_key=settings.t212_secret_key, env=settings.t212_env)
```
Replace with a helper that reads from settings using the same logic:
```python
    def _t212_from_settings(s) -> "T212Client":
        account = s.t212_active_account or "demo"
        env = "demo" if account == "demo" else "live"
        key_map = {
            "demo": (s.t212_demo_api_key, s.t212_demo_secret_key),
            "invest": (s.t212_invest_api_key, s.t212_invest_secret_key),
            "isa": (s.t212_isa_api_key, s.t212_isa_secret_key),
        }
        api_key, secret_key = key_map.get(account, ("", ""))
        return T212Client(api_key=api_key or "", secret_key=secret_key or "", env=env)

    t212 = _t212_from_settings(settings)
```

- [ ] **Step 4: Verify main.py imports cleanly**

```bash
cd /home/preeth/projects/alphaTrade
python -c "import alphaTrade.main"
```
Expected: no errors

- [ ] **Step 5: Commit**

```bash
git add alphaTrade/main.py
git commit -m "feat(main): hot-reload T212Client on active account or key change"
```

---

### Task 5: Update the /settings API router

**Files:**
- Modify: `alphaTrade/api/routers/settings.py`

- [ ] **Step 1: Write failing test first**

Open `tests/unit/test_api_routers.py`. Add:

```python
def test_settings_t212_multi_account(tmp_path):
    c = _client(_engine(tmp_path))
    resp = c.put("/api/v1/settings", json={
        "t212_active_account": "invest",
        "t212_invest_api_key": "invest-key",
        "t212_invest_secret_key": "invest-secret",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["t212_active_account"] == "invest"
    assert body["t212_invest_api_key"] == "***"
    assert body["t212_invest_secret_key"] == "***"

def test_settings_t212_demo_key_masked(tmp_path):
    c = _client(_engine(tmp_path))
    resp = c.put("/api/v1/settings", json={"t212_demo_api_key": "demo-key"})
    assert resp.status_code == 200
    assert resp.json()["t212_demo_api_key"] == "***"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/preeth/projects/alphaTrade
pytest tests/unit/test_api_routers.py::test_settings_t212_multi_account tests/unit/test_api_routers.py::test_settings_t212_demo_key_masked -v
```
Expected: FAIL — fields not in `BotSettingsUpdate`

- [ ] **Step 3: Update BotSettingsUpdate and _SENSITIVE**

Replace the old t212 fields at the top of `alphaTrade/api/routers/settings.py`:

```python
_SENSITIVE = frozenset({
    "t212_demo_api_key", "t212_demo_secret_key",
    "t212_invest_api_key", "t212_invest_secret_key",
    "t212_isa_api_key", "t212_isa_secret_key",
    "polygon_api_key", "email_smtp_password",
    "slack_webhook_url", "alphaTrade_api_key",
})


class BotSettingsUpdate(BaseModel):
    t212_active_account: Optional[str] = None  # "demo" | "invest" | "isa"
    t212_demo_api_key: Optional[str] = None
    t212_demo_secret_key: Optional[str] = None
    t212_invest_api_key: Optional[str] = None
    t212_invest_secret_key: Optional[str] = None
    t212_isa_api_key: Optional[str] = None
    t212_isa_secret_key: Optional[str] = None
    data_provider: Optional[str] = None
    polygon_api_key: Optional[str] = None
    slack_enabled: Optional[bool] = None
    slack_webhook_url: Optional[str] = None
    slack_min_level: Optional[str] = None
    email_enabled: Optional[bool] = None
    email_smtp_host: Optional[str] = None
    email_smtp_port: Optional[int] = None
    email_smtp_user: Optional[str] = None
    email_smtp_password: Optional[str] = None
    email_from_addr: Optional[str] = None
    email_to_addrs: Optional[str] = None
    email_min_level: Optional[str] = None
    size_pct: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    cooldown_bars: Optional[int] = None
    extended_hours: Optional[bool] = None
    max_positions: Optional[int] = None
    daily_loss_halt_pct: Optional[float] = None
    alphaTrade_api_key: Optional[str] = None
```

- [ ] **Step 4: Run the new tests**

```bash
cd /home/preeth/projects/alphaTrade
pytest tests/unit/test_api_routers.py::test_settings_t212_multi_account tests/unit/test_api_routers.py::test_settings_t212_demo_key_masked -v
```
Expected: PASS

- [ ] **Step 5: Update existing t212 tests that reference old field names**

In `tests/unit/test_api_routers.py`, find and update any tests that reference `t212_api_key`, `t212_secret_key`, `t212_env`, or `t212_account_type`. Replace with the new field names. For example:

Old:
```python
assert body["t212_env"] == "demo"
```
New:
```python
assert body["t212_active_account"] == "demo"
```

Old:
```python
BotSettings(id=1, t212_api_key="real-key", alphaTrade_api_key="api-key")
```
New:
```python
BotSettings(id=1, t212_demo_api_key="real-key", alphaTrade_api_key="api-key")
```

Old:
```python
resp = client.put("/api/v1/settings", json={"max_positions": 10, "t212_env": "live"})
assert body["t212_env"] == "live"
```
New:
```python
resp = client.put("/api/v1/settings", json={"max_positions": 10, "t212_active_account": "invest"})
assert body["t212_active_account"] == "invest"
```

Old:
```python
resp = _client(_engine(tmp_path)).put("/api/v1/settings", json={"t212_api_key": "new-key"})
assert resp.json()["t212_api_key"] == "***"
```
New:
```python
resp = _client(_engine(tmp_path)).put("/api/v1/settings", json={"t212_demo_api_key": "new-key"})
assert resp.json()["t212_demo_api_key"] == "***"
```

- [ ] **Step 6: Run full settings test suite**

```bash
cd /home/preeth/projects/alphaTrade
pytest tests/unit/test_api_routers.py -v
```
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add alphaTrade/api/routers/settings.py tests/unit/test_api_routers.py
git commit -m "feat(api): expose t212 per-account key pairs in /settings endpoint"
```

---

### Task 6: Unit tests for apply_bot_settings account switching

**Files:**
- Create: `tests/unit/test_apply_bot_settings.py`

- [ ] **Step 1: Write the test file**

```python
"""Unit tests for apply_bot_settings T212 account switching."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from alphaTrade.store.repos import BotSettings
from alphaTrade.main import _t212_credentials


def _make_db_settings(**kwargs) -> BotSettings:
    defaults = dict(
        t212_active_account="demo",
        t212_demo_api_key="demo-key",
        t212_demo_secret_key="demo-secret",
        t212_invest_api_key="invest-key",
        t212_invest_secret_key="invest-secret",
        t212_isa_api_key="isa-key",
        t212_isa_secret_key="isa-secret",
    )
    defaults.update(kwargs)
    return BotSettings(id=1, **defaults)


def test_demo_account_returns_demo_key():
    db_s = _make_db_settings(t212_active_account="demo")
    key, secret, env = _t212_credentials(db_s)
    assert key == "demo-key"
    assert secret == "demo-secret"
    assert env == "demo"


def test_invest_account_returns_invest_key():
    db_s = _make_db_settings(t212_active_account="invest")
    key, secret, env = _t212_credentials(db_s)
    assert key == "invest-key"
    assert secret == "invest-secret"
    assert env == "live"


def test_isa_account_returns_isa_key():
    db_s = _make_db_settings(t212_active_account="isa")
    key, secret, env = _t212_credentials(db_s)
    assert key == "isa-key"
    assert secret == "isa-secret"
    assert env == "live"


def test_empty_active_account_defaults_to_demo():
    db_s = _make_db_settings(t212_active_account="")
    key, secret, env = _t212_credentials(db_s)
    assert env == "demo"
    assert key == "demo-key"


def test_none_active_account_defaults_to_demo():
    db_s = _make_db_settings(t212_active_account=None)
    key, secret, env = _t212_credentials(db_s)
    assert env == "demo"
```

- [ ] **Step 2: Run tests**

```bash
cd /home/preeth/projects/alphaTrade
pytest tests/unit/test_apply_bot_settings.py -v
```
Expected: all 5 PASS

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_apply_bot_settings.py
git commit -m "test(main): unit tests for _t212_credentials account selection"
```

---

### Task 7: Full regression run

- [ ] **Step 1: Run full test suite**

```bash
cd /home/preeth/projects/alphaTrade
pytest tests/ -v 2>&1 | tail -30
```
Expected: all tests pass, no references to old field names.

- [ ] **Step 2: Check for stale references to old field names**

```bash
grep -rn "t212_api_key\|t212_secret_key\b\|t212_env\|t212_account_type" \
  /home/preeth/projects/alphaTrade/alphaTrade \
  /home/preeth/projects/alphaTrade/tests \
  --include="*.py" | grep -v "migrations\|__pycache__"
```
Expected: no output. If any hits, fix them before proceeding.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "chore: verify no stale t212 field references remain"
```
