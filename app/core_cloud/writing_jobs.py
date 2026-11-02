"""Bounded, account-owned, in-memory drafts. No prompts/results are persisted."""

import re
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from fastapi import HTTPException

from app.core_cloud.post_writing import PostDraft


@dataclass
class Job:
    id: str
    owner: str
    key: str
    fingerprint: str
    work: Callable[[], PostDraft] | None
    status: str = "queued"
    result: PostDraft | None = None
    error: str | None = None
    finished: float | None = None


class WritingJobs:
    def __init__(self, capacity=10, retention_seconds=600, clock=time.monotonic):
        self.capacity, self.retention_seconds, self.clock = capacity, retention_seconds, clock
        self.condition = threading.Condition()
        self.jobs: OrderedDict[str, Job] = OrderedDict()
        self.queue = deque()
        self.worker: threading.Thread | None = None
        self.stopping = False

    def _prune(self):
        expired = [
            key
            for key, job in self.jobs.items()
            if job.finished is not None and self.clock() - job.finished >= self.retention_seconds
        ]
        for key in expired:
            del self.jobs[key]

    def _view(self, job):
        waiting = [
            key for key in self.queue if key in self.jobs and self.jobs[key].status == "queued"
        ]
        return {
            "id": job.id,
            "status": job.status,
            "queue_position": waiting.index(job.id) + 1 if job.id in waiting else 0,
            "result": job.result,
            "error": job.error,
        }

    def submit(self, owner, key, fingerprint, work, admit=lambda: True):
        with self.condition:
            self._prune()
            for job in self.jobs.values():
                if job.owner == owner and job.key == key:
                    if job.fingerprint != fingerprint:
                        raise HTTPException(409, "post_writing_request_conflict")
                    return self._view(job)
            if any(
                j.owner == owner and j.status in ("queued", "running") for j in self.jobs.values()
            ):
                raise HTTPException(409, "post_writing_already_pending")
            if len(self.jobs) >= self.capacity:
                completed = next((j.id for j in self.jobs.values() if j.finished is not None), None)
                if completed:
                    del self.jobs[completed]
                else:
                    raise HTTPException(
                        429, "post_writing_queue_full", headers={"Retry-After": "10"}
                    )
            if not admit():
                raise HTTPException(429, "post_writing_rate_limit")
            job = Job(str(uuid4()), owner, key, fingerprint, work)
            self.jobs[job.id] = job
            self.queue.append(job.id)
            if self.worker is None:
                self.worker = threading.Thread(target=self._run, name="post-writing", daemon=True)
                self.worker.start()
            self.condition.notify_all()
            return self._view(job)

    def get(self, owner, job_id, *, cancel=False):
        with self.condition:
            self._prune()
            job = self.jobs.get(str(job_id))
            if job is None or job.owner != owner:
                raise HTTPException(404, "post_writing_job_not_found")
            if cancel and job.status in ("queued", "running"):
                if job.status == "queued":
                    self.queue.remove(job.id)
                job.status, job.work, job.result = "cancelled", None, None
                job.finished = self.clock()
                self.condition.notify_all()
            return self._view(job)

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.queue or self.stopping)
                if self.stopping:
                    return
                job = self.jobs.get(self.queue.popleft())
                if job is None or job.status != "queued":
                    continue
                work, job.work, job.status = job.work, None, "running"
                self.condition.notify_all()
            try:
                result, error = work(), None
            except Exception as exc:
                detail = getattr(exc, "detail", "post_writing_failed")
                error = (
                    detail
                    if isinstance(detail, str) and re.fullmatch(r"[a-z_]{1,80}", detail)
                    else "post_writing_failed"
                )
                result = None
            with self.condition:
                if job.status != "cancelled":
                    job.result, job.error = result, error
                    job.status = "failed" if error else "succeeded"
                    job.finished = self.clock()
                self.condition.notify_all()
            # Drop the closure (image bytes / token) before idling for another job.
            work = None

    def close(self):
        with self.condition:
            self.stopping = True
            self.condition.notify_all()
        if self.worker:
            self.worker.join(timeout=2)


writing_jobs = WritingJobs()
