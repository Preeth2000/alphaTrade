from __future__ import annotations
import os
from collections.abc import Callable
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from mlflow import MlflowClient
from mlflow.exceptions import MlflowException
from pydantic import BaseModel
from sqlmodel import Session, select
from alphaTrade.store.repos import (
    InstrumentCacheRepo,
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
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        perf_by_id: dict[str, ModelPerformance] = {
            p.model_id: p for p in ModelPerformanceRepo(session).all()
        }
        override_by_id: dict[str, ModelOverrideRecord] = {
            r.run_name: r for r in ModelOverrideRepo(session).all()
        }

        provider = build_data_provider(settings) if settings is not None else None

        seen: set[str] = set()
        results: list[ModelSummary] = []

        # Active models from registry first
        if registry is not None:
            for run_name, (manifest, _) in registry.by_run_name.items():
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
                ))
                seen.add(run_name)

        # DB-only models (removed from disk or retired) not in registry
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
                ))
                seen.add(run_name)

        # MLflow-only models (registered but never deployed to this bot)
        mlflow_client = _try_mlflow_client()
        if mlflow_client:
            try:
                for rm in mlflow_client.search_registered_models():
                    if rm.name in seen:
                        continue
                    aliases = rm.aliases or {}
                    if not aliases:
                        continue
                    # Fetch run params from the most-relevant aliased version
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
                    ))
                    seen.add(rm.name)
            except MlflowException:
                pass  # MLflow unavailable — degrade gracefully, return known models

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

    @router.get("/models/deployments", response_model=list[ModelDeploymentResponse])
    def list_deployments(
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        rows = ModelDeploymentRepo(session).latest_per_model()
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
    def list_registry_models(_: None = Depends(api_key_dep)):
        client = _get_mlflow_client()
        try:
            registered = client.search_registered_models()
        except MlflowException as exc:
            raise HTTPException(status_code=502, detail=f"MLflow error: {exc}")
        result = []
        for rm in registered:
            aliases = rm.aliases or {}  # {alias: version_str}
            if not aliases:
                result.append(MLflowModelInfo(name=rm.name, versions=[]))
                continue
            # Fetch run_id for each aliased version
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
        body: MLflowPromoteRequest = MLflowPromoteRequest(),
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        client = _get_mlflow_client()
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
                pass  # staging alias may not exist on this version
        except HTTPException:
            raise
        except MlflowException as exc:
            raise HTTPException(status_code=502, detail=f"MLflow error: {exc}")
        ModelDeploymentRepo(session).insert_launching(model_name)
        return MLflowPromoteResponse(model_name=model_name, version=version, stage="production")

    @router.post("/models/{model_name}/retry-deploy")
    def retry_deploy(
        model_name: str,
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ) -> dict:
        client = _get_mlflow_client()
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
        ModelDeploymentRepo(session).insert_launching(model_name)
        return {"model_name": model_name, "version": version, "status": "launching"}

    @router.post("/models/{model_name}/demote", response_model=MLflowPromoteResponse)
    def demote_model(
        model_name: str,
        body: MLflowPromoteRequest = MLflowPromoteRequest(),
        _: None = Depends(api_key_dep),
    ):
        client = _get_mlflow_client()
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
