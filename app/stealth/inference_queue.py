"""Bounded, process-local FIFO admission; stores no prompts or user identifiers."""

import threading
import time
from collections import deque


class InferenceQueue:
    def __init__(self, max_waiting=8, wait_seconds=90.0):
        self.max_waiting, self.wait_seconds = max_waiting, wait_seconds
        self.condition = threading.Condition()
        self.active = False
        self.waiters = deque()
        self.completed = self.rejected = self.timed_out = 0

    def acquire(self, blocking=True):
        with self.condition:
            if not self.active and not self.waiters:
                self.active = True
                return True
            if not blocking or len(self.waiters) >= self.max_waiting:
                self.rejected += 1
                return False
            ticket = object()
            self.waiters.append(ticket)
            self.condition.notify_all()
            deadline = time.monotonic() + self.wait_seconds
            try:
                while self.active or self.waiters[0] is not ticket:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self.timed_out += 1
                        return False
                    self.condition.wait(remaining)
                self.active = True
                return True
            finally:
                self.waiters.remove(ticket)
                self.condition.notify_all()

    def release(self):
        with self.condition:
            if not self.active:
                raise ValueError("No active inference")
            self.active = False
            self.completed += 1
            self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            return {
                "active": int(self.active),
                "waiting": len(self.waiters),
                "completed": self.completed,
                "rejected": self.rejected,
                "timed_out": self.timed_out,
            }
