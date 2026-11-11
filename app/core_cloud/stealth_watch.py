"""Bounded local matching watch using each owner's existing authenticated session.

No administrator token, third-party search, automatic acceptance, or disk credentials.
The watch ends when the agent is disabled, its signals expire, or the session ends.
"""

import asyncio
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException

from app.stealth.ollama import LocalModelError


@dataclass
class Watch:
    service: object
    due: float = 0


class StealthWatch:
    def __init__(self, interval=60, maximum=32):
        self.interval = interval
        self.maximum = maximum
        self.jobs = {}
        self.lock = threading.Lock()

    def subscribe(self, actor, service):
        with self.lock:
            if actor in self.jobs:
                self.jobs[actor].service = service
            elif len(self.jobs) < self.maximum:
                self.jobs[actor] = Watch(service)

    def unsubscribe(self, actor):
        with self.lock:
            self.jobs.pop(actor, None)

    def cycle(self):
        with self.lock:
            jobs = list(self.jobs.items())
        for actor, job in jobs:
            if job.due > time.monotonic():
                continue
            job.due = time.monotonic() + self.interval
            try:
                service = job.service
                agent = service.agent()
                signals = service.signals().items
                if (
                    not agent
                    or not agent.enabled
                    or not any(
                        s.status == "active" and s.expires_at > datetime.now(UTC) for s in signals
                    )
                ):
                    self.unsubscribe(actor)
                    continue
                service.run(3)
            except HTTPException as error:
                if error.status_code in (401, 403, 404, 429) or error.detail in (
                    "openai_budget_unavailable",
                    "openai_quota_or_rate_limit",
                ):
                    self.unsubscribe(actor)
            except (LocalModelError, ValueError, OSError):
                # Temporary model/network trouble must not erase the saved request.
                pass

    async def run(self):
        while True:
            await asyncio.to_thread(self.cycle)
            await asyncio.sleep(5)
