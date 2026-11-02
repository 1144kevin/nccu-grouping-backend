import threading

import pytest
from fastapi import HTTPException

from app.core_cloud.post_writing import PostDraft
from app.core_cloud.writing_jobs import WritingJobs


def draft():
    return PostDraft(
        text="這是隔離測試的合成邀請文，歡迎一起討論餐點與取餐安排，實際店家、費用、時間和分工都會先確認，再決定是否參加拼單。"
    )


def test_five_owners_queue_without_rejection_and_remain_private():
    jobs = WritingJobs(capacity=5)
    release, entered = threading.Event(), threading.Event()
    seen = []

    def work(index):
        seen.append(index)
        entered.set()
        assert release.wait(2)
        return draft()

    try:
        first = jobs.submit("owner0", "retry-key", "same", lambda: work(0))
        assert entered.wait(1)
        assert jobs.submit("owner0", "retry-key", "same", lambda: None)["id"] == first["id"]
        for index in range(1, 5):
            view = jobs.submit(f"owner{index}", "retry-key", "same", lambda i=index: work(i))
            assert view["status"] == "queued" and view["queue_position"] == index
            with pytest.raises(HTTPException) as denied:
                jobs.get("owner0", view["id"])
            assert denied.value.status_code == 404
        with pytest.raises(HTTPException) as full:
            jobs.submit("owner5", "retry-key", "same", draft)
        assert full.value.status_code == 429
        release.set()
        with jobs.condition:
            assert jobs.condition.wait_for(
                lambda: all(j.status == "succeeded" for j in jobs.jobs.values()), timeout=2
            )
        assert seen == list(range(5))
        assert jobs.get("owner0", first["id"])["result"].text == draft().text
    finally:
        release.set()
        jobs.close()


def test_cancelled_waiter_never_runs_and_errors_do_not_expose_exception_text():
    jobs = WritingJobs()
    release, entered = threading.Event(), threading.Event()
    called = []

    def first():
        entered.set()
        release.wait(2)
        return draft()

    def fail():
        raise RuntimeError("private prompt and token must not be returned")

    try:
        jobs.submit("one", "key-first", "one", first)
        assert entered.wait(1)
        pending = jobs.submit("two", "key-second", "two", lambda: called.append(1))
        jobs.get("two", pending["id"], cancel=True)
        broken = jobs.submit("three", "key-third", "three", fail)
        release.set()
        with jobs.condition:
            assert jobs.condition.wait_for(lambda: jobs.jobs[broken["id"]].finished is not None, 2)
        assert not called
        assert jobs.get("three", broken["id"])["error"] == "post_writing_failed"
        assert jobs.get("two", pending["id"])["status"] == "cancelled"
    finally:
        release.set()
        jobs.close()


def test_keys_cannot_change_payload_and_completed_drafts_expire():
    clock = [0.0]
    jobs = WritingJobs(clock=lambda: clock[0], retention_seconds=10)
    try:
        view = jobs.submit("one", "same-key", "first-payload", draft)
        with jobs.condition:
            assert jobs.condition.wait_for(lambda: jobs.jobs[view["id"]].finished is not None, 2)
        with pytest.raises(HTTPException) as conflict:
            jobs.submit("one", "same-key", "other-payload", draft)
        assert conflict.value.status_code == 409
        clock[0] = 11
        with pytest.raises(HTTPException) as expired:
            jobs.get("one", view["id"])
        assert expired.value.status_code == 404
    finally:
        jobs.close()
