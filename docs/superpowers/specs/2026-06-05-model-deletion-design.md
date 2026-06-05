# Model Deletion Design

**Date:** 2026-06-05  
**Scope:** alphaTrade (backend API) + alphaLink (frontend UI)

## Overview

Allow users to permanently delete their own models from the platform. Deletion removes all config, artifacts, and registry entries while preserving trade/signal history for regulatory compliance.

## What Gets Deleted

| Data | Action | Reason |
|---|---|---|
| `ModelOverrideRecord` | Delete | Config — no audit value |
| `ModelAdoption` rows for model | Delete | Config linkage |
| MLflow registered model (all versions, aliases, tags) | Delete | Artifact registry |
| alphaTrade local `models_dir/{safe_name}/` | Delete | Synced artifacts |
| alphaTrade local `models_dir/.sync/{safe_name}` | Delete | Sync state record |
| MinIO `{user}/{account}/{run_name}/` prefix | Delete | Shared bucket, same instance as alphaGen |

## What Is Preserved

| Data | Reason |
|---|---|
| `TradeJournal` | Regulatory audit trail (MiFID II / FCA) |
| `Signal` | Audit trail |
| `ModelPerformance` | Historical stats |
| `ModelDeployment` rows | Deployment audit trail |
| `BacktestTrade` | Research audit trail |
| `BacktestModelRun` | Research audit trail |

## Backend

### Endpoint

```
DELETE /models/{run_name}
```

Returns `{"deleted": true, "run_name": run_name}` on success. 403 if caller does not own the model.

### Ownership

Uses existing `_owns_model(manifest_user_id, req_user_id)` + `_req_user(request)` pattern from promote/demote/fork. Admin role (`_req_user` returns `None`) bypasses ownership check. Standard and developer users are scoped to their own models.

Fallback: if model has no MLflow entry (DB-only), check `ModelOverrideRecord` for ownership info. If no ownership info available, only admin can delete.

### Core Logic

Extracted as `async def _delete_model(run_name, session, settings, mlflow_client, req_user_id)` for future bulk deletion reuse (`asyncio.gather` over N models).

Steps in order:
1. Ownership check — raise 403 if not owner/admin
2. Delete `ModelOverrideRecord` for `run_name`
3. Delete all `ModelAdoption` rows where `model_name == run_name`
4. Delete MLflow registered model via `client.delete_registered_model(run_name)` — best-effort
5. Delete local `models_dir/{safe_name}/` and `.sync/{safe_name}` — skip if missing
6. Delete MinIO prefix `{user}/{account}/{run_name}/` — list all objects, batch delete — best-effort

Steps 4–6 are best-effort: log errors and continue. Model is already removed from DB by step 3.

### MinIO Access

alphaTrade and alphaGen share a single MinIO instance. alphaTrade already holds credentials via `MinioConfig` (`endpoint`, `access_key`, `secret_key`, `bucket`). Path convention: `{model_sync.user}/{model_sync.account}/{run_name}/`.

## Frontend

### Delete Button

Rendered in `ModelOverrideDrawer` footer. Visible only when `userId === owner_id || role === 'admin'`. Disabled while any save/reset mutation is pending.

Styling: `variant="ghost"` with red text — visually distinct from primary Save action.

Footer layout:
```
[Reset to defaults]   [Delete]   [N unsaved changes]   [Save]
```

### Warning Modal

Separate Dialog component (not inline confirm like the reset flow).

```
Title:   Delete model?
Body:    "{run_name}" will be permanently removed. Artifacts, overrides,
         and deployments will be deleted. Trade history is preserved.
         This cannot be undone.
Buttons: [Cancel]   [Delete model]  ← destructive (red)
```

### On Confirm

1. Fire `DELETE /models/{run_name}` via `useMutation`
2. On success: invalidate `['models']` query, call `onClose()`
3. On error: show inline error in footer, drawer stays open

### Ownership Check

`useAuthStore` exposes `id` (maps to `owner_id`). `owner_id` comes from `ModelSummary` — passed as a new `ownerUserId: string | null` prop to `ModelOverrideDrawer`. Callers (registry page) already have `ModelSummary` in scope. No store changes needed.

## Error Handling

| Failure | Behaviour |
|---|---|
| 403 from backend | Show "You don't own this model" in footer |
| MLflow not configured | Log warning, skip — model may be DB-only |
| MLflow delete fails | Log error, continue — orphaned artifacts are ops concern |
| MinIO delete fails | Log error, continue — same rationale |
| Local files missing | Skip silently |
| Any other backend error | Show message in footer, drawer stays open |

No partial rollback. Deletion is intentional and confirmed via modal.

## Future: Bulk Deletion

Tracked in `alphatrade-uop`. When implemented:
- Multi-select UI on registry page
- Single confirm modal listing all selected models
- Backend calls `_delete_model` N times via `asyncio.gather`
- Scoped to requesting user's own models only
