"""APScheduler-based backtest scheduler. One job per model."""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.engine import Engine
from sqlmodel import Session

from alphaTrade.adapter.inference import OnnxModel
from alphaTrade.adapter.manifest import Manifest
from alphaTrade.backtest.engine import run_backtest
from alphaTrade.config import BacktestScheduleOverride, ModelOverride, Settings
from alphaTrade.store.repos import BacktestRepo

log = logging.getLogger(__name__)


async def _execute_backtest(
    engine: Engine,
    settings: Settings,
    models_dir: Path,
    run_id: int,
    start: str,
    end: str,
    model_filter: str | None = None,
) -> None:
    """Update run status and execute backtest. Shared by scheduler and API trigger."""
    with Session(engine) as session:
        BacktestRepo(session).update_status(run_id, "running")
    try:
        with Session(engine) as session:
            run_backtest(
                session=session,
                models_dir=models_dir,
                start=start,
                end=end,
                cfg=settings.backtest,
                run_id=run_id,
                model_filter=model_filter,
            )
        with Session(engine) as session:
            BacktestRepo(session).update_status(run_id, "done")
    except Exception as exc:
        log.error("backtest run_id=%d failed: %s", run_id, exc)
        with Session(engine) as session:
            BacktestRepo(session).update_status(run_id, "failed")


class BacktestScheduler:
    def __init__(
        self,
        engine: Engine,
        settings: Settings,
        models: list[tuple[Manifest, OnnxModel]],
        models_dir: Path,
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._models: dict[str, tuple[Manifest, OnnxModel]] = {m.run_name: (m, mo) for m, mo in models}
        self._models_dir = models_dir
        self._scheduler = AsyncIOScheduler()

    def start(self) -> None:
        self._scheduler.start()
        if self._settings.backtest.schedule_enabled:
            self._rebuild_all_jobs()

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)

    async def trigger(
        self,
        session: Session,
        start: str,
        end: str,
        model_filter: str | None = None,
    ) -> int:
        """Pre-create run record and launch background task. Returns run_id."""
        run_id = BacktestRepo(session).create_run(
            start=start,
            end=end,
            config_json=self._settings.backtest.model_dump_json(),
            status="queued",
        )
        asyncio.create_task(
            _execute_backtest(
                engine=self._engine,
                settings=self._settings,
                models_dir=self._models_dir,
                run_id=run_id,
                start=start,
                end=end,
                model_filter=model_filter,
            )
        )
        return run_id

    def update_global(
        self,
        schedule_enabled: bool | None,
        cron: str | None,
        lookback_days: int | None,
    ) -> None:
        bt = self._settings.backtest
        if schedule_enabled is not None:
            bt.schedule_enabled = schedule_enabled
        if cron is not None:
            bt.cron = cron
        if lookback_days is not None:
            bt.lookback_days = lookback_days
        self._remove_all_jobs()
        if bt.schedule_enabled:
            self._rebuild_all_jobs()
        self._persist_overrides()

    def update_model(
        self,
        model_id: str,
        disabled: bool | None,
        cron: str | None,
        lookback_days: int | None,
    ) -> None:
        if model_id not in self._settings.model_overrides:
            self._settings.model_overrides[model_id] = ModelOverride()
        ov = self._settings.model_overrides[model_id].backtest
        if disabled is not None:
            ov.disabled = disabled
        if cron is not None:
            ov.cron = cron
        if lookback_days is not None:
            ov.lookback_days = lookback_days
        job_id = f"backtest_{model_id}"
        if self._scheduler.get_job(job_id):
            self._scheduler.remove_job(job_id)
        if self._settings.backtest.schedule_enabled and not ov.disabled and model_id in self._models:
            manifest, model = self._models[model_id]
            self._add_job(manifest, model, ov)
        self._persist_overrides()

    def get_status(self) -> dict[str, Any]:
        jobs = []
        for job in self._scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
            })
        return {
            "schedule_enabled": self._settings.backtest.schedule_enabled,
            "cron": self._settings.backtest.cron,
            "lookback_days": self._settings.backtest.lookback_days,
            "jobs": jobs,
        }

    def get_model_status(self, model_id: str) -> dict[str, Any]:
        ov = self._settings.model_overrides.get(model_id, ModelOverride()).backtest
        job = self._scheduler.get_job(f"backtest_{model_id}")
        return {
            "model_id": model_id,
            "disabled": ov.disabled,
            "effective_cron": ov.cron or self._settings.backtest.cron,
            "effective_lookback_days": ov.lookback_days or self._settings.backtest.lookback_days,
            "next_run_time": job.next_run_time.isoformat() if job and job.next_run_time else None,
        }

    def _rebuild_all_jobs(self) -> None:
        for run_name, (manifest, model) in self._models.items():
            ov = self._settings.model_overrides.get(run_name, ModelOverride()).backtest
            if not ov.disabled:
                self._add_job(manifest, model, ov)

    def _add_job(self, manifest: Manifest, model: OnnxModel, ov: BacktestScheduleOverride) -> None:
        effective_cron = ov.cron or self._settings.backtest.cron
        effective_lookback = ov.lookback_days or self._settings.backtest.lookback_days
        engine = self._engine
        settings = self._settings
        models_dir = self._models_dir

        async def _job() -> None:
            end = date.today().isoformat()
            start = (date.today() - timedelta(days=effective_lookback)).isoformat()
            with Session(engine) as session:
                run_id = BacktestRepo(session).create_run(
                    start=start, end=end,
                    config_json=settings.backtest.model_dump_json(),
                    status="queued",
                )
            await _execute_backtest(engine, settings, models_dir, run_id, start, end, manifest.run_name)

        self._scheduler.add_job(
            _job,
            CronTrigger.from_crontab(effective_cron),
            id=f"backtest_{manifest.run_name}",
            replace_existing=True,
        )

    def _remove_all_jobs(self) -> None:
        for job in list(self._scheduler.get_jobs()):
            if job.id.startswith("backtest_"):
                self._scheduler.remove_job(job.id)

    def _persist_overrides(self) -> None:
        path = self._settings.overrides_path
        raw: dict = yaml.safe_load(path.read_text()) if path.exists() else {}
        raw.setdefault("backtest", {})
        raw["backtest"]["schedule_enabled"] = self._settings.backtest.schedule_enabled
        raw["backtest"]["cron"] = self._settings.backtest.cron
        raw["backtest"]["lookback_days"] = self._settings.backtest.lookback_days
        raw.setdefault("models", {})
        for run_name, override in self._settings.model_overrides.items():
            bt = override.backtest
            bt_dict: dict = {}
            if bt.disabled:
                bt_dict["disabled"] = True
            if bt.cron is not None:
                bt_dict["cron"] = bt.cron
            if bt.lookback_days is not None:
                bt_dict["lookback_days"] = bt.lookback_days
            raw["models"].setdefault(run_name, {})
            if bt_dict:
                raw["models"][run_name]["backtest"] = bt_dict
            elif "backtest" in raw["models"].get(run_name, {}):
                del raw["models"][run_name]["backtest"]
        path.write_text(yaml.dump(raw, default_flow_style=False))
