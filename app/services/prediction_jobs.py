import asyncio
import logging
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


def _touch(job: PredictionJob) -> None:
    job.updated_at = datetime.now(timezone.utc)


def _prune_finished_jobs() -> None:
    cutoff = datetime.now(timezone.utc).timestamp() - 60 * 60
    old_job_ids = [
        job_id
        for job_id, job in _jobs.items()
        if job.status in {"completed", "failed"} and job.updated_at.timestamp() < cutoff
    ]
    for job_id in old_job_ids:
        _jobs.pop(job_id, None)


async def _run_preview_job(job_id: UUID) -> None:
    job = _jobs[job_id]
    job.status = "running"
    _touch(job)
    try:
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
        _touch(job)


def start_prediction_job(cycle_id: UUID, user_id: str, request: PredictionRequest) -> PredictionJob:
    _prune_finished_jobs()
    job = PredictionJob(id=uuid4(), cycle_id=cycle_id, user_id=user_id, request=request)
    _jobs[job.id] = job
    asyncio.create_task(_run_preview_job(job.id))
    return job


def get_prediction_job(job_id: UUID, cycle_id: UUID, user_id: str) -> PredictionJob | None:
    job = _jobs.get(job_id)
    if job is None or job.cycle_id != cycle_id or job.user_id != user_id:
        return None
    return job
