import threading
from concurrent.futures import ThreadPoolExecutor

from app.stealth.inference_queue import InferenceQueue


def test_queue_serializes_waiters_and_rejects_overflow():
    gate = InferenceQueue(max_waiting=1, wait_seconds=1)
    assert gate.acquire()
    entered = threading.Event()

    def next_job():
        entered.set()
        assert gate.acquire()
        try:
            return gate.snapshot()["active"]
        finally:
            gate.release()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(next_job)
        assert entered.wait(1)
        # Synchronize on the actual admission state, not a guessed timing sleep.
        with gate.condition:
            assert gate.condition.wait_for(lambda: len(gate.waiters) == 1, timeout=1)
        assert not gate.acquire()
        assert gate.snapshot()["waiting"] == 1
        gate.release()
        assert future.result(timeout=2) == 1
    assert gate.snapshot() == {
        "active": 0,
        "waiting": 0,
        "completed": 2,
        "rejected": 1,
        "timed_out": 0,
    }


def test_timed_out_ticket_does_not_poison_future_requests():
    gate = InferenceQueue(wait_seconds=0)
    assert gate.acquire()
    assert not gate.acquire()
    assert gate.snapshot()["timed_out"] == 1
    assert gate.snapshot()["waiting"] == 0
    gate.release()
    assert gate.acquire()
    gate.release()


def test_default_admission_can_hold_five_people_until_the_model_is_ready():
    gate = InferenceQueue()
    assert gate.acquire()
    with ThreadPoolExecutor(max_workers=4) as pool:

        def next_job():
            assert gate.acquire()
            gate.release()

        futures = [pool.submit(next_job) for _ in range(4)]
        with gate.condition:
            assert gate.condition.wait_for(lambda: len(gate.waiters) == 4, timeout=2)
        gate.release()
        for future in futures:
            future.result(timeout=2)
    assert gate.snapshot()["completed"] == 5
    assert gate.snapshot()["rejected"] == 0
