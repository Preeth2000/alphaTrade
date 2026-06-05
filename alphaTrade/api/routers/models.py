from __future__ import annotations
import logging
import os
import shutil
from collections.abc import Callable
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)
from fastapi import APIRouter, Depends, HTTPException, Request
from mlflow import MlflowClient
from mlflow.exceptions import MlflowException
from pydantic import BaseModel
from sqlmodel import Session, select
from alphaTrade.store.repos import (
    InstrumentCacheRepo,
    ModelAdoptionRepo,
    ModelDeploymentRepo,
    ModelPerformance,
    ModelPerformanceRepo,
    ModelOverrideRecord,
    ModelOverrideRepo,
    Signal,
)
from alphaTrade.data.factory import build_data_provider


class ModelOverrideUpdate(BaseModel):
    enabled: Optional[bool] = None
    broker_ticker: Optional[str] = None
    size_pct: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    cooldown_bars: Optional[int] = None
    safe_mode: Optional[bool] = None
    dangerously_allow_pyramid: Optional[bool] = None
    visibility: Optional[str] = None  # "public" | "private"


class ModelOverrideResponse(BaseModel):
    run_name: str
    enabled: Optional[bool] = None
    broker_ticker: Optional[str] = None
    size_pct: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    cooldown_bars: Optional[int] = None
    safe_mode: Optional[bool] = None
    dangerously_allow_pyramid: Optional[bool] = None
    visibility: str = "private"
    updated_at: Optional[datetime] = None
    resolved_ticker: Optional[str] = None
    """Effective broker ticker the tick loop will use. None = resolves at tick time via yaml/cache/API."""


class ModelSummary(BaseModel):
    run_name: str
    ticker: Optional[str] = None
    interval: Optional[str] = None
    model_arch: Optional[str] = None
    n_features: Optional[int] = None
    active: bool
    resolved_ticker: Optional[str] = None
    trade_count: int = 0
    win_count: int = 0
    rolling_pnl: float = 0.0
    retired: bool = False
    retired_at: Optional[datetime] = None
    last_updated: Optional[datetime] = None
    max_lookback_days: Optional[int] = None
    visibility: str = "private"
    is_adopted: bool = False
    owner_id: Optional[str] = None


def _resolve_ticker(session: Session, run_name: str, broker_ticker_override: Optional[str]) -> Optional[str]:
    if broker_ticker_override:
        return broker_ticker_override
    sig = session.exec(
        select(Signal).where(Signal.run_name == run_name).order_by(Signal.ts.desc()).limit(1)  # type: ignore[attr-defined]
    ).first()
    if sig:
        cached = InstrumentCacheRepo(session).get(sig.ticker)
        if cached:
            return cached.t212_ticker
    return None


def _to_override_response(record: ModelOverrideRecord, session: Session) -> ModelOverrideResponse:
    return ModelOverrideResponse(
        run_name=record.run_name,
        enabled=record.enabled,
        broker_ticker=record.broker_ticker,
        size_pct=record.size_pct,
        stop_loss_pct=record.stop_loss_pct,
        take_profit_pct=record.take_profit_pct,
        cooldown_bars=record.cooldown_bars,
        safe_mode=record.safe_mode,
        dangerously_allow_pyramid=record.dangerously_allow_pyramid,
        visibility=record.visibility,
        updated_at=record.updated_at,
        resolved_ticker=_resolve_ticker(session, record.run_name, record.broker_ticker),
    )


class MLflowVersionInfo(BaseModel):
    version: str
    stage: str
    run_id: str


class MLflowModelInfo(BaseModel):
    name: str
    versions: list[MLflowVersionInfo]


class ModelDeploymentResponse(BaseModel):
    run_name: str
    status: str
    promoted_at: datetime
    activated_at: Optional[datetime] = None
    failed_at: Optional[datetime] = None
    failure_msg: Optional[str] = None


class MLflowPromoteRequest(BaseModel):
    version: Optional[str] = None


class MLflowPromoteResponse(BaseModel):
    model_config = {"protected_namespaces": ()}
    model_name: str
    version: str
    stage: str


_SENTINEL_USER_ID = "00000000-0000-0000-0000-000000000001"


def _req_user(request: Request) -> Optional[str]:
    """Return user_id filter for model queries.

    Admin returns None (sees all users' models). Developer and standard are
    scoped to their own models.
    """
    from alphaTrade.api.routers._scope import scoped_user_id
    return scoped_user_id(request)


def _owns_model(manifest_user_id: str, req_user_id: Optional[str]) -> bool:
    """True if the requesting user owns the model.

    Legacy models (manifest.user_id == '') are visible to everyone so existing
    models remain accessible after upgrading to user-scoped manifests.
    Sentinel rows are also open — they represent the single-tenant bootstrap user.
    """
    if not manifest_user_id or manifest_user_id == _SENTINEL_USER_ID:
        return True
    return req_user_id == manifest_user_id


def _mlflow_model_user(client: "MlflowClient", model_name: str) -> Optional[str]:
    """Return user_id tag from the most-relevant aliased model version, or None."""
    for alias in ("production", "staging"):
        try:
            mv = client.get_model_version_by_alias(model_name, alias)
            return (mv.tags or {}).get("user_id")
        except Exception:
            pass
    return None


def _mlflow_model_visibility(client: "MlflowClient", model_name: str) -> str:
    """Return visibility tag from most-relevant aliased model version."""
    for alias in ("production", "staging"):
        try:
            mv = client.get_model_version_by_alias(model_name, alias)
            return (mv.tags or {}).get("visibility", "private")
        except Exception:
            pass
    return "private"


def make_router(
    session_dep: Callable,
    api_key_dep: Callable,
    registry=None,
    settings=None,
    mlflow_tracking_uri: Optional[str] = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/models", response_model=list[ModelSummary])
    def list_models(
        request: Request,
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        user_id = _req_user(request)
        req_user_id = getattr(request.state, "user_id", None)  # always the actual caller
        perf_by_id: dict[str, ModelPerformance] = {
            p.model_id: p for p in ModelPerformanceRepo(session).all()
        }
        override_by_id: dict[str, ModelOverrideRecord] = {
            r.run_name: r for r in ModelOverrideRepo(session).all()
        }
        adopted_names: set[str] = set(
            ModelAdoptionRepo(session).adopted_models(req_user_id) if req_user_id else []
        )

        provider = build_data_provider(settings) if settings is not None else None

        seen: set[str] = set()
        results: list[ModelSummary] = []

        # Active models from local registry (synced from this bot's MinIO namespace)
        if registry is not None:
            for run_name, (manifest, _) in registry.by_run_name.items():
                is_adopted = run_name in adopted_names
                if not _owns_model(manifest.user_id, user_id) and not is_adopted:
                    continue
                perf = perf_by_id.get(run_name)
                ov = override_by_id.get(run_name)
                results.append(ModelSummary(
                    run_name=run_name,
                    ticker=manifest.ticker,
                    interval=manifest.interval,
                    model_arch=manifest.model_arch,
                    n_features=manifest.n_features,
                    active=True,
                    resolved_ticker=_resolve_ticker(session, run_name, ov.broker_ticker if ov else None),
                    trade_count=perf.trade_count if perf else 0,
                    win_count=perf.win_count if perf else 0,
                    rolling_pnl=perf.rolling_pnl if perf else 0.0,
                    retired=perf.retired if perf else False,
                    retired_at=perf.retired_at if perf else None,
                    last_updated=perf.last_updated if perf else None,
                    max_lookback_days=provider.max_lookback_days(manifest.interval) if provider and manifest.interval else None,
                    visibility=manifest.visibility,
                    is_adopted=is_adopted,
                    owner_id=manifest.user_id or None,
                ))
                seen.add(run_name)

        # DB-only models (removed from disk or retired)
        for run_name, perf in perf_by_id.items():
            if run_name not in seen:
                ov = override_by_id.get(run_name)
                results.append(ModelSummary(
                    run_name=run_name,
                    active=False,
                    resolved_ticker=_resolve_ticker(session, run_name, ov.broker_ticker if ov else None),
                    trade_count=perf.trade_count,
                    win_count=perf.win_count,
                    rolling_pnl=perf.rolling_pnl,
                    retired=perf.retired,
                    retired_at=perf.retired_at,
                    last_updated=perf.last_updated,
                    is_adopted=run_name in adopted_names,
                ))
                seen.add(run_name)

        # MLflow-only models (registered, not yet synced to this bot)
        mlflow_client = _try_mlflow_client()
        if mlflow_client:
            try:
                for rm in mlflow_client.search_registered_models():
                    if rm.name in seen:
                        continue
                    aliases = rm.aliases or {}
                    if not aliases:
                        continue
                    model_owner = _mlflow_model_user(mlflow_client, rm.name)
                    is_adopted = rm.name in adopted_names
                    if not _owns_model(model_owner or "", user_id) and not is_adopted:
                        continue
                    run_params: dict = {}
                    for alias in ("production", "staging"):
                        if alias in aliases:
                            try:
                                mv = mlflow_client.get_model_version_by_alias(rm.name, alias)
                                run = mlflow_client.get_run(mv.run_id)  # type: ignore[arg-type]
                                run_params = run.data.params
                            except MlflowException:
                                pass
                            break
                    interval = run_params.get("interval")
                    vis = _mlflow_model_visibility(mlflow_client, rm.name)
                    results.append(ModelSummary(
                        run_name=rm.name,
                        ticker=run_params.get("ticker"),
                        interval=interval,
                        model_arch=run_params.get("arch"),
                        n_features=int(run_params["n_features"]) if "n_features" in run_params else None,
                        active=False,
                        resolved_ticker=None,
                        trade_count=0,
                        win_count=0,
                        rolling_pnl=0.0,
                        retired=False,
                        max_lookback_days=provider.max_lookback_days(interval) if provider and interval else None,
                        visibility=vis,
                        is_adopted=is_adopted,
                        owner_id=model_owner,
                    ))
                    seen.add(rm.name)
            except MlflowException:
                pass

        return results

    @router.get("/models/overrides", response_model=list[ModelOverrideResponse])
    def get_all_overrides(
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        return [_to_override_response(r, session) for r in ModelOverrideRepo(session).all()]

    @router.get("/models/{run_name}/overrides", response_model=ModelOverrideResponse)
    def get_overrides(
        run_name: str,
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        record = ModelOverrideRepo(session).get(run_name) or ModelOverrideRecord(run_name=run_name)
        return _to_override_response(record, session)

    @router.put("/models/{run_name}/overrides", response_model=ModelOverrideResponse)
    def set_overrides(
        run_name: str,
        update: ModelOverrideUpdate,
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        repo = ModelOverrideRepo(session)
        existing = repo.get(run_name) or ModelOverrideRecord(run_name=run_name)
        for field, val in update.model_dump(exclude_unset=True).items():
            setattr(existing, field, val)
        saved = repo.upsert(existing)
        return _to_override_response(saved, session)

    @router.delete("/models/{run_name}/overrides")
    def delete_overrides(
        run_name: str,
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        deleted = ModelOverrideRepo(session).delete(run_name)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"No overrides found for {run_name!r}")
        return {"deleted": True, "run_name": run_name}

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

    def _get_mlflow_client() -> MlflowClient:
        uri = mlflow_tracking_uri or os.environ.get("MLFLOW_TRACKING_URI")
        if not uri:
            raise HTTPException(status_code=503, detail="MLFLOW_TRACKING_URI not configured")
        return MlflowClient(tracking_uri=uri)

    def _try_mlflow_client() -> Optional[MlflowClient]:
        uri = mlflow_tracking_uri or os.environ.get("MLFLOW_TRACKING_URI")
        if not uri:
            return None
        return MlflowClient(tracking_uri=uri)

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

    @router.get("/models/deployments", response_model=list[ModelDeploymentResponse])
    def list_deployments(
        request: Request,
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        req_uid = getattr(request.state, "user_id", None)
        adopted = ModelAdoptionRepo(session).adopted_models(req_uid) if req_uid else []
        rows = ModelDeploymentRepo(session).latest_per_model(user_id=_req_user(request), adopted_names=adopted)
        return [
            ModelDeploymentResponse(
                run_name=r.run_name,
                status=r.status,
                promoted_at=r.promoted_at,
                activated_at=r.activated_at,
                failed_at=r.failed_at,
                failure_msg=r.failure_msg,
            )
            for r in rows
        ]

    @router.get("/models/registry", response_model=list[MLflowModelInfo])
    def list_registry_models(request: Request, _: None = Depends(api_key_dep)):
        user_id = _req_user(request)
        client = _get_mlflow_client()
        try:
            registered = client.search_registered_models()
        except MlflowException as exc:
            raise HTTPException(status_code=502, detail=f"MLflow error: {exc}")
        result = []
        for rm in registered:
            model_owner = _mlflow_model_user(client, rm.name)
            if not _owns_model(model_owner or "", user_id):
                continue
            aliases = rm.aliases or {}
            if not aliases:
                result.append(MLflowModelInfo(name=rm.name, versions=[]))
                continue
            versions = []
            for alias, ver_str in aliases.items():
                try:
                    mv = client.get_model_version_by_alias(rm.name, alias)
                    versions.append(MLflowVersionInfo(version=ver_str, stage=alias, run_id=mv.run_id))  # type: ignore[arg-type]
                except MlflowException:
                    pass
            result.append(MLflowModelInfo(name=rm.name, versions=versions))
        return result

    @router.post("/models/{model_name}/promote", response_model=MLflowPromoteResponse)
    def promote_model(
        model_name: str,
        request: Request,
        body: MLflowPromoteRequest = MLflowPromoteRequest(),
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        req_user_id = getattr(request.state, "user_id", None)
        user_id = _req_user(request)
        client = _get_mlflow_client()
        model_owner = _mlflow_model_user(client, model_name)
        is_adopted = req_user_id and ModelAdoptionRepo(session).is_adopted(req_user_id, model_name)
        if not _owns_model(model_owner or "", user_id) and not is_adopted:
            raise HTTPException(status_code=403, detail="Not authorised to promote this model")
        version = body.version
        try:
            if version is None:
                try:
                    sv = client.get_model_version_by_alias(model_name, "staging")
                    version = sv.version
                except MlflowException:
                    raise HTTPException(status_code=404, detail=f"No staging alias for {model_name!r}")
            client.set_registered_model_alias(model_name, "production", version)
            try:
                client.delete_registered_model_alias(model_name, "staging")
            except MlflowException:
                pass
        except HTTPException:
            raise
        except MlflowException as exc:
            raise HTTPException(status_code=502, detail=f"MLflow error: {exc}")
        ModelDeploymentRepo(session).insert_launching(model_name, user_id=user_id)
        return MLflowPromoteResponse(model_name=model_name, version=version, stage="production")

    @router.post("/models/{model_name}/retry-deploy")
    def retry_deploy(
        model_name: str,
        request: Request,
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ) -> dict:
        req_user_id = getattr(request.state, "user_id", None)
        user_id = _req_user(request)
        client = _get_mlflow_client()
        model_owner = _mlflow_model_user(client, model_name)
        is_adopted = req_user_id and ModelAdoptionRepo(session).is_adopted(req_user_id, model_name)
        if not _owns_model(model_owner or "", user_id) and not is_adopted:
            raise HTTPException(status_code=403, detail="Not authorised to redeploy this model")
        try:
            pv = client.get_model_version_by_alias(model_name, "production")
            version = pv.version
        except MlflowException:
            raise HTTPException(status_code=404, detail=f"No production alias for {model_name!r} — promote first")
        if settings is not None and settings.models_dir is not None:
            safe_name = model_name.replace("/", "_")
            sync_record = settings.models_dir / ".sync" / safe_name
            if sync_record.exists():
                sync_record.unlink()
        ModelDeploymentRepo(session).insert_launching(model_name, user_id=user_id)
        return {"model_name": model_name, "version": version, "status": "launching"}

    @router.get("/models/public", response_model=list[ModelSummary])
    def list_public_models(
        _: None = Depends(api_key_dep),
        session: Session = Depends(session_dep),
    ):
        """Public library: models the owner has set to public AND that have been active at least once.

        Visibility is controlled via ModelOverrideRecord.visibility — setting to "private"
        immediately removes the model from this list.
        """
        from sqlmodel import select as _sel
        # Models explicitly marked public via override
        public_names: set[str] = {
            r.run_name for r in session.exec(
                _sel(ModelOverrideRecord).where(ModelOverrideRecord.visibility == "public")
            ).all()
        }
        if not public_names:
            return []
        # Must have been active at least once
        ever_active: set[str] = {
            r.run_name for r in session.exec(
                _sel(ModelDeployment).where(
                    ModelDeployment.run_name.in_(public_names),  # type: ignore[attr-defined]
                    ModelDeployment.status == "active",
                )
            ).all()
        }
        visible = public_names & ever_active
        if not visible:
            return []

        provider = build_data_provider(settings) if settings is not None else None
        mlflow_client = _try_mlflow_client()
        results: list[ModelSummary] = []
        # Enrich with MLflow metadata
        for name in visible:
            run_params: dict = {}
            owner = None
            if mlflow_client:
                try:
                    for alias in ("production", "staging"):
                        try:
                            mv = mlflow_client.get_model_version_by_alias(name, alias)
                            run = mlflow_client.get_run(mv.run_id)  # type: ignore[arg-type]
                            run_params = run.data.params
                            owner = (mv.tags or {}).get("user_id")
                            break
                        except MlflowException:
                            pass
                except MlflowException:
                    pass
            interval = run_params.get("interval")
            perf = {p.model_id: p for p in ModelPerformanceRepo(session).all()}.get(name)
            results.append(ModelSummary(
                run_name=name,
                ticker=run_params.get("ticker"),
                interval=interval,
                model_arch=run_params.get("arch"),
                n_features=int(run_params["n_features"]) if "n_features" in run_params else None,
                active=True,  # only ever-active models reach here
                resolved_ticker=None,
                trade_count=perf.trade_count if perf else 0,
                win_count=perf.win_count if perf else 0,
                rolling_pnl=perf.rolling_pnl if perf else 0.0,
                retired=perf.retired if perf else False,
                max_lookback_days=provider.max_lookback_days(interval) if provider and interval else None,
                visibility="public",
                owner_id=owner,
            ))
        return results

    class ForkRequest(BaseModel):
        new_name: Optional[str] = None  # defaults to "{model_name}_copy"

    class ForkResponse(BaseModel):
        new_model_name: str
        status: str = "registered"

    @router.post("/models/{model_name}/fork", response_model=ForkResponse)
    def fork_model(
        model_name: str,
        request: Request,
        body: "ForkRequest" = None,  # type: ignore[assignment]
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        """Fork a public model into the requesting user's registry.

        Creates a new MLflow registered model pointing to the same run artifacts —
        no re-upload. The fork starts undeployed; user must promote it independently.
        """
        from sqlmodel import select as _sel
        user_id = getattr(request.state, "user_id", None)
        if not user_id:
            raise HTTPException(status_code=401, detail="Authentication required")

        # Verify model is in the public library (override=public + ever active)
        override = ModelOverrideRepo(session).get(model_name)
        if not override or override.visibility != "public":
            raise HTTPException(status_code=403, detail="Model is not in the public library")
        ever_active = session.exec(
            _sel(ModelDeployment).where(
                ModelDeployment.run_name == model_name,
                ModelDeployment.status == "active",
            )
        ).first()
        if not ever_active:
            raise HTTPException(status_code=403, detail="Model has not been active yet")

        client = _get_mlflow_client()

        # Get the production version's run_id
        try:
            pv = client.get_model_version_by_alias(model_name, "production")
            source_run_id = pv.run_id
        except MlflowException:
            raise HTTPException(status_code=404, detail=f"No production version for {model_name!r}")

        # Determine new name — auto-resolve conflicts
        if body and body.new_name:
            candidate = body.new_name.strip()
        else:
            candidate = f"{model_name}_copy"

        final_name = candidate
        suffix = 2
        while True:
            try:
                client.get_registered_model(final_name)
                # Name taken — try next suffix
                final_name = f"{candidate}_{suffix}"
                suffix += 1
            except MlflowException:
                break  # name is available

        # Register new model pointing to same artifacts
        try:
            import mlflow as _mlflow
            result = _mlflow.register_model(
                model_uri=f"runs:/{source_run_id}/model",
                name=final_name,
            )
            client.set_model_version_tag(final_name, str(result.version), "user_id", user_id)
            client.set_model_version_tag(final_name, str(result.version), "visibility", "private")
            client.set_model_version_tag(final_name, str(result.version), "forked_from", model_name)
        except MlflowException as exc:
            raise HTTPException(status_code=502, detail=f"MLflow error: {exc}")

        # Create private override entry for the fork
        fork_override = ModelOverrideRepo(session).get(final_name) or ModelOverrideRecord(run_name=final_name)
        fork_override.visibility = "private"
        ModelOverrideRepo(session).upsert(fork_override)

        return ForkResponse(new_model_name=final_name)

    @router.post("/models/{model_name}/demote", response_model=MLflowPromoteResponse)
    def demote_model(
        model_name: str,
        request: Request,
        body: MLflowPromoteRequest = MLflowPromoteRequest(),
        _: None = Depends(api_key_dep),
    ):
        user_id = _req_user(request)
        client = _get_mlflow_client()
        model_owner = _mlflow_model_user(client, model_name)
        if not _owns_model(model_owner or "", user_id):
            raise HTTPException(status_code=403, detail="Not authorised to demote this model")
        version = body.version
        try:
            if version is None:
                try:
                    pv = client.get_model_version_by_alias(model_name, "production")
                    version = pv.version
                except MlflowException:
                    raise HTTPException(status_code=404, detail=f"No production alias for {model_name!r}")
            client.set_registered_model_alias(model_name, "staging", version)
            try:
                client.delete_registered_model_alias(model_name, "production")
            except MlflowException:
                pass  # production alias may not exist on this version
        except HTTPException:
            raise
        except MlflowException as exc:
            raise HTTPException(status_code=502, detail=f"MLflow error: {exc}")
        return MLflowPromoteResponse(model_name=model_name, version=version, stage="staging")

    return router
