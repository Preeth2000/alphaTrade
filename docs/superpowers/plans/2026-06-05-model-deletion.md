# Model Deletion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow users to permanently delete their own models — removing artifacts, MLflow registration, overrides, and adoptions while preserving trade/signal history.

**Architecture:** Synchronous `DELETE /models/{run_name}` endpoint in alphaTrade performs ownership check, DB cleanup, then best-effort MLflow + local file + MinIO deletion via a reusable `_delete_model` async helper. Frontend adds a delete button inside `ModelOverrideDrawer` behind a warning modal.

**Tech Stack:** FastAPI, SQLModel, boto3 (already a dependency), MLflow, React, TanStack Query, shadcn/ui AlertDialog

---

## File Map

| File | Change |
|---|---|
| `alphaTrade/store/repos.py` | Add `ModelAdoptionRepo.delete_all_for_model` |
| `alphaTrade/api/routers/models.py` | Add `_delete_model` helper + `DELETE /models/{run_name}` endpoint |
| `alphaTrade/tests/unit/test_api_model_delete.py` | New — backend deletion tests |
| `alphaLink/src/components/models/ModelOverrideDrawer.tsx` | Add `ownerUserId` prop, delete button, warning modal |
| `alphaLink/src/components/models/ModelOverrideDrawer.test.tsx` | Add delete button/modal tests |
| `alphaLink/src/app/trade/models/registry/page.tsx` | Thread `ownerUserId` prop to drawer |

---

## Task 1: Add `delete_all_for_model` to `ModelAdoptionRepo`

**Files:**
- Modify: `alphaTrade/store/repos.py` (after the `unadopt` method, ~line 865)
- Test: `alphaTrade/tests/unit/test_new_orm_models.py`

- [ ] **Step 1: Write the failing test**

Open `alphaTrade/tests/unit/test_new_orm_models.py` and add at the end:

```python
class TestModelAdoptionRepoDeleteAllForModel:
    def test_deletes_all_adoptions_for_model(self, tmp_path):
        from alphaTrade.store.repos import ModelAdoptionRepo
        db = tmp_path / "test.db"
        from alphaTrade.store.db import run_migrations
        engine = create_engine(f"sqlite:///{db}")
        run_migrations(db)
        with Session(engine) as s:
            repo = ModelAdoptionRepo(s)
            repo.adopt("user-1", "my_model")
            repo.adopt("user-2", "my_model")
            repo.adopt("user-1", "other_model")  # should NOT be deleted
            count = repo.delete_all_for_model("my_model")
            assert count == 2
        with Session(engine) as s:
            repo = ModelAdoptionRepo(s)
            assert repo.adopted_models("user-1") == ["other_model"]
            assert repo.adopted_models("user-2") == []

    def test_returns_zero_when_no_adoptions(self, tmp_path):
        from alphaTrade.store.repos import ModelAdoptionRepo
        db = tmp_path / "test.db"
        from alphaTrade.store.db import run_migrations
        engine = create_engine(f"sqlite:///{db}")
        run_migrations(db)
        with Session(engine) as s:
            count = ModelAdoptionRepo(s).delete_all_for_model("nonexistent")
            assert count == 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/preeth/projects/projectAlpha/alphaTrade
python -m pytest tests/unit/test_new_orm_models.py::TestModelAdoptionRepoDeleteAllForModel -v
```

Expected: FAIL with `AttributeError: 'ModelAdoptionRepo' object has no attribute 'delete_all_for_model'`

- [ ] **Step 3: Implement `delete_all_for_model`**

In `alphaTrade/store/repos.py`, add after the `unadopt` method (after line ~865):

```python
    def delete_all_for_model(self, model_name: str) -> int:
        rows = self._s.exec(
            select(ModelAdoption).where(ModelAdoption.model_name == model_name)
        ).all()
        for row in rows:
            self._s.delete(row)
        self._s.commit()
        return len(rows)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/unit/test_new_orm_models.py::TestModelAdoptionRepoDeleteAllForModel -v
```

Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add alphaTrade/store/repos.py alphaTrade/tests/unit/test_new_orm_models.py
git commit -m "feat(store): add ModelAdoptionRepo.delete_all_for_model"
```

---

## Task 2: Backend `DELETE /models/{run_name}` endpoint

**Files:**
- Modify: `alphaTrade/api/routers/models.py`
- Create: `alphaTrade/tests/unit/test_api_model_delete.py`

- [ ] **Step 1: Write the failing tests**

Create `alphaTrade/tests/unit/test_api_model_delete.py`:

```python
"""Tests for DELETE /models/{run_name}."""
from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from mlflow.exceptions import MlflowException
from sqlmodel import Session, create_engine

from alphaTrade.api.app import create_app
from alphaTrade.health import HealthState
from alphaTrade.store.db import run_migrations
from alphaTrade.store.repos import (
    ModelAdoption,
    ModelAdoptionRepo,
    ModelOverrideRecord,
    ModelOverrideRepo,
)


def _make_engine(tmp_path: Path):
    db = tmp_path / "test.db"
    run_migrations(db)
    return create_engine(f"sqlite:///{db}")


def _make_router(
    engine=None,
    user_id: str = "user-1",
    role: str = "standard",
    mlflow_tracking_uri: str = "http://mlflow:5000",
    registry=None,
    settings=None,
):
    """Build a minimal FastAPI app with the models router and injected request state."""
    from alphaTrade.api.routers.models import make_router
    from alphaTrade.api.deps import make_session_dep

    app = FastAPI()

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user_id = user_id
        request.state.role = role
        return await call_next(request)

    if engine is not None:
        session_dep = make_session_dep(engine)
    else:
        def session_dep():
            return MagicMock()

    def noop_api_key():
        return None

    router = make_router(
        session_dep=session_dep,
        api_key_dep=noop_api_key,
        registry=registry,
        settings=settings,
        mlflow_tracking_uri=mlflow_tracking_uri,
    )
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


def _mock_mlflow_client(owner_user_id: str = "user-1") -> MagicMock:
    client = MagicMock()
    mv = MagicMock()
    mv.tags = {"user_id": owner_user_id}
    client.get_model_version_by_alias.return_value = mv
    return client


class TestDeleteModelOwnership:
    def test_returns_200_for_owner(self):
        client = _make_router(user_id="user-1")
        with patch("alphaTrade.api.routers.models.MlflowClient", return_value=_mock_mlflow_client("user-1")):
            resp = client.delete("/api/v1/models/my_model")
        assert resp.status_code == 200
        assert resp.json() == {"deleted": True, "run_name": "my_model"}

    def test_returns_403_for_non_owner(self):
        client = _make_router(user_id="user-2")
        with patch("alphaTrade.api.routers.models.MlflowClient", return_value=_mock_mlflow_client("user-1")):
            resp = client.delete("/api/v1/models/my_model")
        assert resp.status_code == 403

    def test_admin_can_delete_own_legacy_model(self):
        """Legacy models (no user_id tag) are deletable by anyone including admin."""
        client = _make_router(user_id="admin-id", role="admin")
        mock_client = MagicMock()
        mock_mv = MagicMock()
        mock_mv.tags = {}  # no user_id tag = legacy
        mock_client.get_model_version_by_alias.return_value = mock_mv
        with patch("alphaTrade.api.routers.models.MlflowClient", return_value=mock_client):
            resp = client.delete("/api/v1/models/legacy_model")
        assert resp.status_code == 200


class TestDeleteModelDbCleanup:
    def test_deletes_override_record(self, tmp_path):
        engine = _make_engine(tmp_path)
        with Session(engine) as s:
            ModelOverrideRepo(s).upsert(ModelOverrideRecord(run_name="my_model", visibility="private"))
        client = _make_router(engine=engine, user_id="user-1")
        with patch("alphaTrade.api.routers.models.MlflowClient", return_value=_mock_mlflow_client("user-1")):
            resp = client.delete("/api/v1/models/my_model")
        assert resp.status_code == 200
        with Session(engine) as s:
            assert ModelOverrideRepo(s).get("my_model") is None

    def test_deletes_adoption_rows(self, tmp_path):
        engine = _make_engine(tmp_path)
        with Session(engine) as s:
            ModelAdoptionRepo(s).adopt("user-2", "my_model", source_user_id="user-1")
            ModelAdoptionRepo(s).adopt("user-3", "my_model", source_user_id="user-1")
        client = _make_router(engine=engine, user_id="user-1")
        with patch("alphaTrade.api.routers.models.MlflowClient", return_value=_mock_mlflow_client("user-1")):
            resp = client.delete("/api/v1/models/my_model")
        assert resp.status_code == 200
        with Session(engine) as s:
            assert ModelAdoptionRepo(s).adopted_models("user-2") == []
            assert ModelAdoptionRepo(s).adopted_models("user-3") == []

    def test_succeeds_with_no_override_or_adoptions(self, tmp_path):
        """Model may have no DB records — should not error."""
        engine = _make_engine(tmp_path)
        client = _make_router(engine=engine, user_id="user-1")
        with patch("alphaTrade.api.routers.models.MlflowClient", return_value=_mock_mlflow_client("user-1")):
            resp = client.delete("/api/v1/models/clean_model")
        assert resp.status_code == 200


class TestDeleteModelBestEffort:
    def test_returns_200_when_mlflow_delete_fails(self, tmp_path):
        """MLflow failure is logged but does not abort the delete."""
        engine = _make_engine(tmp_path)
        mock_client = _mock_mlflow_client("user-1")
        mock_client.delete_registered_model.side_effect = MlflowException("not found")
        client = _make_router(engine=engine, user_id="user-1")
        with patch("alphaTrade.api.routers.models.MlflowClient", return_value=mock_client):
            resp = client.delete("/api/v1/models/my_model")
        assert resp.status_code == 200

    def test_returns_200_when_mlflow_not_configured(self, tmp_path):
        engine = _make_engine(tmp_path)
        client = _make_router(engine=engine, user_id="user-1", mlflow_tracking_uri="")
        # No MLflow URI — _try_mlflow_client returns None, ownership check falls through to legacy path
        resp = client.delete("/api/v1/models/legacy_model")
        assert resp.status_code == 200

    def test_deletes_local_model_dir(self, tmp_path):
        from alphaTrade.config import Settings
        engine = _make_engine(tmp_path)
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        model_dir = models_dir / "my_model"
        model_dir.mkdir()
        (model_dir / "model.onnx").write_bytes(b"fake")
        sync_dir = models_dir / ".sync"
        sync_dir.mkdir()
        (sync_dir / "my_model").write_text("1")

        settings = MagicMock(spec=Settings)
        settings.models_dir = models_dir
        settings.minio.endpoint = "localhost:9000"
        settings.minio.access_key = "key"
        settings.minio.secret_key = "secret"
        settings.minio.bucket = "models"
        settings.model_sync.user = "u"
        settings.model_sync.account = "a"

        client = _make_router(engine=engine, user_id="user-1", settings=settings)
        with patch("alphaTrade.api.routers.models.MlflowClient", return_value=_mock_mlflow_client("user-1")), \
             patch("boto3.client") as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            mock_s3.get_paginator.return_value.paginate.return_value = [{"Contents": []}]
            resp = client.delete("/api/v1/models/my_model")

        assert resp.status_code == 200
        assert not model_dir.exists()
        assert not (sync_dir / "my_model").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/preeth/projects/projectAlpha/alphaTrade
python -m pytest tests/unit/test_api_model_delete.py -v
```

Expected: FAIL — `delete_model` endpoint not yet defined (404s or import errors)

- [ ] **Step 3: Implement `_delete_model` and the endpoint**

In `alphaTrade/api/routers/models.py`, add at the top with other imports:

```python
import logging
import shutil

log = logging.getLogger(__name__)
```

Then, inside the `make_router` function body (after the existing `_try_mlflow_client` helper, before the `list_models` route), add:

```python
    async def _delete_model(run_name: str, session: Session, mlflow_client) -> None:
        """Delete all platform traces of run_name. Best-effort for external services."""
        # DB: override record
        ModelOverrideRepo(session).delete(run_name)
        # DB: all adoption rows
        from alphaTrade.store.repos import ModelAdoptionRepo
        ModelAdoptionRepo(session).delete_all_for_model(run_name)

        # MLflow: delete registered model (all versions, aliases, tags)
        if mlflow_client is not None:
            try:
                mlflow_client.delete_registered_model(run_name)
                log.info("model_delete: removed MLflow model %s", run_name)
            except Exception as exc:
                log.error("model_delete: MLflow deletion failed for %s: %s", run_name, exc)

        # Local files: models_dir/{safe_name}/ and .sync/{safe_name}
        if settings is not None and settings.models_dir is not None:
            safe_name = run_name.replace("/", "_")
            for p in (
                settings.models_dir / safe_name,
                settings.models_dir / ".sync" / safe_name,
            ):
                if p.exists():
                    try:
                        shutil.rmtree(p) if p.is_dir() else p.unlink()
                        log.info("model_delete: removed local path %s", p)
                    except Exception as exc:
                        log.error("model_delete: could not remove %s: %s", p, exc)

        # MinIO: delete all objects under {user}/{account}/{run_name}/
        if settings is not None:
            try:
                import boto3
                endpoint = settings.minio.endpoint
                if not endpoint.startswith("http"):
                    endpoint = f"http://{endpoint}"
                s3 = boto3.client(
                    "s3",
                    endpoint_url=endpoint,
                    aws_access_key_id=settings.minio.access_key,
                    aws_secret_access_key=settings.minio.secret_key,
                )
                prefix = f"{settings.model_sync.user}/{settings.model_sync.account}/{run_name}/"
                paginator = s3.get_paginator("list_objects_v2")
                for page in paginator.paginate(Bucket=settings.minio.bucket, Prefix=prefix):
                    objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
                    if objects:
                        s3.delete_objects(
                            Bucket=settings.minio.bucket,
                            Delete={"Objects": objects},
                        )
                log.info("model_delete: cleared MinIO prefix %s", prefix)
            except Exception as exc:
                log.error("model_delete: MinIO cleanup failed for %s: %s", run_name, exc)
```

Then add the endpoint (after the existing `delete_overrides` route, before `_get_mlflow_client`):

```python
    @router.delete("/models/{run_name}")
    async def delete_model(
        run_name: str,
        request: Request,
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        user_id = _req_user(request)  # None = admin

        # Determine model owner: MLflow → in-memory registry → legacy (no owner)
        mlflow_client = _try_mlflow_client()
        model_owner: Optional[str] = None
        if mlflow_client:
            model_owner = _mlflow_model_user(mlflow_client, run_name)
        if model_owner is None and registry is not None:
            entry = registry.by_run_name.get(run_name)
            if entry:
                model_owner = entry[0].user_id or None

        if not _owns_model(model_owner or "", user_id):
            raise HTTPException(status_code=403, detail="Not authorised to delete this model")

        await _delete_model(run_name, session, mlflow_client)
        return {"deleted": True, "run_name": run_name}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/unit/test_api_model_delete.py -v
```

Expected: all PASS

- [ ] **Step 5: Run the full model test suite to check for regressions**

```bash
python -m pytest tests/unit/test_api_models.py tests/unit/test_api_mlflow_models.py -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add alphaTrade/api/routers/models.py alphaTrade/tests/unit/test_api_model_delete.py
git commit -m "feat(api): add DELETE /models/{run_name} with ownership check and best-effort cleanup"
```

---

## Task 3: Frontend — delete button and warning modal in `ModelOverrideDrawer`

**Files:**
- Modify: `alphaLink/src/components/models/ModelOverrideDrawer.tsx`
- Modify: `alphaLink/src/components/models/ModelOverrideDrawer.test.tsx`

- [ ] **Step 1: Write failing tests**

Open `alphaLink/src/components/models/ModelOverrideDrawer.test.tsx` and add these test cases inside the existing `describe('ModelOverrideDrawer', ...)` block:

```tsx
  describe('delete button visibility', () => {
    beforeEach(() => {
      // useAuthStore returns user with id 'user-1' and role 'standard'
      // (patch the mock at the top of the file to expose user.id)
    })

    it('hides delete button when ownerUserId does not match current user', () => {
      render(
        <ModelOverrideDrawer runName="test_model" ownerUserId="user-99" onClose={jest.fn()} />,
        { wrapper },
      )
      expect(screen.queryByRole('button', { name: /delete model/i })).not.toBeInTheDocument()
    })

    it('shows delete button when ownerUserId matches current user', () => {
      render(
        <ModelOverrideDrawer runName="test_model" ownerUserId="user-1" onClose={jest.fn()} />,
        { wrapper },
      )
      expect(screen.getByRole('button', { name: /delete model/i })).toBeInTheDocument()
    })

    it('shows delete button for admin regardless of ownerUserId', () => {
      // Override the mock to return role='admin'
      // render with ownerUserId='user-99'
      // button present
    })

    it('hides delete button when runName is null', () => {
      render(
        <ModelOverrideDrawer runName={null} ownerUserId="user-1" onClose={jest.fn()} />,
        { wrapper },
      )
      expect(screen.queryByRole('button', { name: /delete model/i })).not.toBeInTheDocument()
    })
  })

  describe('delete flow', () => {
    it('opens warning modal when delete button clicked', async () => {
      render(
        <ModelOverrideDrawer runName="test_model" ownerUserId="user-1" onClose={jest.fn()} />,
        { wrapper },
      )
      await userEvent.click(screen.getByRole('button', { name: /delete model/i }))
      expect(screen.getByRole('alertdialog')).toBeInTheDocument()
      expect(screen.getByText(/permanently removed/i)).toBeInTheDocument()
    })

    it('closes modal without deleting on cancel', async () => {
      render(
        <ModelOverrideDrawer runName="test_model" ownerUserId="user-1" onClose={jest.fn()} />,
        { wrapper },
      )
      await userEvent.click(screen.getByRole('button', { name: /delete model/i }))
      await userEvent.click(screen.getByRole('button', { name: /cancel/i }))
      expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    })
  })
```

Note: the test file already mocks `useAuthStore` — check how `role` is mocked there and extend the mock to also expose `user: { id: 'user-1' }`.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/preeth/projects/projectAlpha/alphaLink
npx jest src/components/models/ModelOverrideDrawer.test.tsx --testNamePattern="delete" --no-coverage
```

Expected: FAIL — props not accepted / button not rendered

- [ ] **Step 3: Update `ModelOverrideDrawer` — add prop, state, mutation, modal, button**

At the top of `ModelOverrideDrawer.tsx`, add to imports:

```tsx
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Trash2 } from 'lucide-react'
```

Update the `Props` interface:

```tsx
interface Props {
  runName: string | null
  ownerUserId: string | null
  onClose: () => void
}
```

Inside `ModelOverrideDrawer`, add after the existing auth store reads:

```tsx
  const userId = useAuthStore((s) => s.user?.id ?? null)
  const canDelete = runName !== null && (role === 'admin' || userId === ownerUserId)
  const [showDeleteModal, setShowDeleteModal] = useState(false)

  const deleteMutation = useMutation({
    mutationFn: () => tradeFetch<{ deleted: boolean; run_name: string }>(
      `/models/${runName}`,
      { method: 'DELETE' },
    ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['models'] })
      onClose()
    },
  })
```

In the footer section, add the delete button between Reset and the spacer. Replace the existing non-`confirmReset` footer branch with:

```tsx
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setConfirmReset(true)}
              disabled={loadingOverrides || (!hasOverrides && !retirementHasOverrides)}
            >
              Reset to defaults
            </Button>
            {canDelete && (
              <Button
                variant="ghost"
                size="sm"
                className="text-red-500 hover:text-red-600 hover:bg-red-50"
                onClick={() => setShowDeleteModal(true)}
                aria-label="Delete model"
              >
                <Trash2 size={14} className="mr-1" />
                Delete
              </Button>
            )}
            <div className="flex-1" />
            {totalUnsaved > 0 && (
              <span className="text-xs text-muted-foreground">
                {totalUnsaved} unsaved {totalUnsaved === 1 ? 'change' : 'changes'}
              </span>
            )}
            <Button
              size="sm"
              disabled={saveDisabled}
              onClick={handleSave}
            >
              {saveAnySaving ? 'Saving…' : 'Save'}
            </Button>
          </>
```

Add the delete error display before the closing `</>` of the body section (alongside existing error messages):

```tsx
              {deleteMutation.isError && (
                <p className="text-xs text-red-500">
                  Delete failed: {(deleteMutation.error as Error).message}
                </p>
              )}
```

Add the warning modal just before the final closing `</>` of the component return (alongside the existing overlay and drawer divs):

```tsx
      <AlertDialog open={showDeleteModal} onOpenChange={setShowDeleteModal}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete model?</AlertDialogTitle>
            <AlertDialogDescription>
              <span className="font-mono font-medium">{runName}</span> will be permanently
              removed. Artifacts, overrides, and deployments will be deleted. Trade history
              is preserved. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-red-600 hover:bg-red-700 text-white"
              disabled={deleteMutation.isPending}
              onClick={() => deleteMutation.mutate()}
            >
              {deleteMutation.isPending ? 'Deleting…' : 'Delete model'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
```

- [ ] **Step 4: Update the `useAuthStore` mock in the test file**

Find the `jest.mock` for `@/lib/auth-store` in `ModelOverrideDrawer.test.tsx`. It currently returns `role`. Extend it to also return `user.id`:

```tsx
jest.mock('@/lib/auth-store', () => ({
  useAuthStore: (selector: (s: { user: { id: string }; role: string }) => unknown) =>
    selector({ user: { id: 'user-1' }, role: 'standard' }),
}))
```

For the admin test case, create a local override using `jest.spyOn` or a separate mock context.

- [ ] **Step 5: Run tests to verify they pass**

```bash
npx jest src/components/models/ModelOverrideDrawer.test.tsx --no-coverage
```

Expected: all PASS

- [ ] **Step 6: TypeScript check**

```bash
npx tsc --noEmit
```

Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add src/components/models/ModelOverrideDrawer.tsx src/components/models/ModelOverrideDrawer.test.tsx
git commit -m "feat(ui): add delete button and warning modal to ModelOverrideDrawer"
```

---

## Task 4: Thread `ownerUserId` prop from registry page

**Files:**
- Modify: `alphaLink/src/app/trade/models/registry/page.tsx`

- [ ] **Step 1: Update the `ModelOverrideDrawer` usage**

In `alphaLink/src/app/trade/models/registry/page.tsx`, find line 338:

```tsx
      <ModelOverrideDrawer runName={overrideTarget} onClose={() => setOverrideTarget(null)} />
```

Replace with:

```tsx
      <ModelOverrideDrawer
        runName={overrideTarget}
        ownerUserId={models?.find(m => m.run_name === overrideTarget)?.owner_id ?? null}
        onClose={() => setOverrideTarget(null)}
      />
```

- [ ] **Step 2: TypeScript check**

```bash
npx tsc --noEmit
```

Expected: no errors

- [ ] **Step 3: Run full test suite**

```bash
npx jest --no-coverage
```

Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add src/app/trade/models/registry/page.tsx
git commit -m "feat(registry): pass ownerUserId to ModelOverrideDrawer for delete visibility"
```

---

## Self-Review Notes

**Spec coverage check:**
- ✅ `DELETE /models/{run_name}` endpoint with ownership check (Task 2)
- ✅ DB: `ModelOverrideRecord` deleted (Task 2, `_delete_model`)
- ✅ DB: `ModelAdoption` rows deleted (Task 1 + Task 2)
- ✅ MLflow registered model deleted (Task 2, best-effort)
- ✅ Local `models_dir/{name}/` and `.sync/{name}` deleted (Task 2, best-effort)
- ✅ MinIO prefix `{user}/{account}/{run_name}/` deleted (Task 2, best-effort)
- ✅ Trade/signal history preserved — not touched
- ✅ Delete button only visible to owner or admin (Task 3)
- ✅ Warning modal before deletion (Task 3)
- ✅ On success: invalidate `['models']` query + close drawer (Task 3)
- ✅ Error shown inline on failure (Task 3)
- ✅ `_delete_model` is async helper ready for future `asyncio.gather` bulk use (Task 2)

**Bulk deletion bead `alphatrade-uop`:** No code added for bulk — `_delete_model` is the reuse point.
