import threading
import time
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class Job:
    id: str
    project_id: str
    status: str  # pending / running / completed / partial / failed
    total: int = 0
    success_count: int = 0
    failed_count: int = 0
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    error: Optional[str] = None
    has_results: bool = False  # True if result.xlsx exists
    error_log: list = field(default_factory=list)  # per-item error messages

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "status": self.status,
            "total": self.total,
            "success_count": self.success_count,
            "failed_count": self.failed_count,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "has_results": self.has_results,
            "error_log": self.error_log,
        }


_store: dict[str, Job] = {}
_lock = threading.Lock()


def create_job(job_id: str, project_id: str, total: int) -> Job:
    job = Job(id=job_id, project_id=project_id, status="pending", total=total)
    with _lock:
        _store[job_id] = job
    return job


def get_job(job_id: str) -> Optional[Job]:
    with _lock:
        return _store.get(job_id)


def update_job(job_id: str, **kwargs) -> None:
    with _lock:
        job = _store.get(job_id)
        if job:
            for k, v in kwargs.items():
                setattr(job, k, v)


def append_error_log(job_id: str, msg: str) -> None:
    with _lock:
        job = _store.get(job_id)
        if job:
            job.error_log.append(msg)


def delete_job(job_id: str) -> bool:
    with _lock:
        if job_id in _store:
            del _store[job_id]
            return True
        return False


def list_jobs_for_project(project_id: str) -> list[dict]:
    with _lock:
        jobs = [j for j in _store.values() if j.project_id == project_id]
    return [j.to_dict() for j in sorted(jobs, key=lambda j: j.created_at, reverse=True)]
