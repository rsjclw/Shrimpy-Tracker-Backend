import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid4

from app.database import SessionLocal
from app.models import Cycle
from app.schemas.prediction import PredictionRequest, PredictionResultOut
from app.services.prediction import PredictionError, preview_prediction

logger = logging.getLogger(__name__)


@dataclass
class PredictionJob:
    id: UUID
    cycle_id: UUID
    user_id: str
    request: PredictionRequest
    status: str = "pending"
    error: str | None = None
    result: PredictionResultOut | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


_jobs: dict[UUID, PredictionJob] = {}
ACTIVE_STATUSES = {"pending", "running", "completed"}
RUNNING_STATUSES = {"pending", "running"}
_preview_semaphore = asyncio.Semaphore(1)


def _touch(job: PredictionJob) -> None:
    job.updated_at = datetime.now(timezone.utc)


def _prune_finished_jobs() -> None:
    cutoff = datetime.now(timezone.utc).timestamp() - 60 * 60
    old_job_ids = [
        job_id
        for job_id, job in _jobs.items()
        if job.status in {"completed", "failed", "applied"} and job.updated_at.timestamp() < cutoff
    ]
    for job_id in old_job_ids:
        _jobs.pop(job_id, None)


async def _run_preview_job(job_id: UUID) -> None:
    job = _jobs[job_id]
    job.status = "running"
    _touch(job)
    heartbeat_task = asyncio.create_task(_heartbeat(job))
    try:
        async with _preview_semaphore:
            async with SessionLocal() as db:
                cycle = await db.get(Cycle, job.cycle_id)
                if cycle is None:
                    raise PredictionError("Cycle not found")
                job.result = await preview_prediction(
                    db,
                    cycle,
                    job.request.start_date,
                    job.request.target_doc,
                    job.request.optimize_partial_harvests,
                )
        job.status = "completed"
    except PredictionError as error:
        job.status = "failed"
        job.error = str(error)
    except Exception:
        logger.exception("Prediction job failed")
        job.status = "failed"
        job.error = "Prediction failed."
    finally:
        heartbeat_task.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat_task
        _touch(job)


async def _heartbeat(job: PredictionJob) -> None:
    while job.status == "running":
        await asyncio.sleep(2)
        if job.status == "running":
            _touch(job)


def start_prediction_job(cycle_id: UUID, user_id: str, request: PredictionRequest) -> PredictionJob:
    _prune_finished_jobs()
    existing_job = get_latest_prediction_job(cycle_id, user_id, RUNNING_STATUSES)
    if existing_job is not None:
        _touch(existing_job)
        return existing_job
    job = PredictionJob(id=uuid4(), cycle_id=cycle_id, user_id=user_id, request=request)
    _jobs[job.id] = job
    asyncio.create_task(_run_preview_job(job.id))
    return job


def get_prediction_job(job_id: UUID, cycle_id: UUID, user_id: str) -> PredictionJob | None:
    job = _jobs.get(job_id)
    if job is None or job.cycle_id != cycle_id or job.user_id != user_id:
        return None
    return job


def get_latest_prediction_job(cycle_id: UUID, user_id: str, statuses: set[str]) -> PredictionJob | None:
    _prune_finished_jobs()
    candidates = [
        job
        for job in _jobs.values()
        if job.cycle_id == cycle_id
        and job.user_id == user_id
        and job.status in statuses
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda job: job.created_at)


def get_latest_active_prediction_job(cycle_id: UUID, user_id: str) -> PredictionJob | None:
    return get_latest_prediction_job(cycle_id, user_id, ACTIVE_STATUSES)


def mark_prediction_job_applied(job: PredictionJob) -> PredictionJob:
    job.status = "applied"
    _touch(job)
    return job
