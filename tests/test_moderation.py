from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.routes.moderation import moderator
from app.core_cloud.engagement import ReportView
from app.core_cloud.moderation import AppealInput, AppealReview, Moderation, ReviewInput


def test_review_preserves_actor_session_and_version_contract():
    calls = []
    store = SimpleNamespace(
        token="synthetic-session", _request=lambda *a, **kw: calls.append((a, kw)) or {}
    )
    value = ReviewInput(
        expected_version=2,
        action="dismiss",
        public_response="查核後沒有足夠證據。",
        request_key=uuid4(),
    )
    report = uuid4()
    Moderation(store).review(report, value)
    assert calls[0][1]["token"] == "synthetic-session"
    assert calls[0][1]["body"]["args"]["report_id"] == str(report)
    assert calls[0][1]["body"]["args"]["expected_version"] == 2
    with pytest.raises(ValidationError):
        ReviewInput(
            expected_version=True,
            action="dismiss",
            public_response="查核後沒有足夠證據。",
            request_key=uuid4(),
        )
    with pytest.raises(ValidationError):
        ReviewInput(
            expected_version=1,
            action="ban_user",
            public_response="不可偷偷擴權。",
            request_key=uuid4(),
        )


def test_moderation_disabled_for_unconfigured_adapter():
    with pytest.raises(HTTPException) as error:
        moderator(SimpleNamespace(), SimpleNamespace(), SimpleNamespace(headers={}))
    assert error.value.status_code == 503


def test_reporter_schema_rejects_internal_operator_notes():
    with pytest.raises(ValidationError):
        ReportView(
            id=uuid4(),
            kind="feedback",
            detail="隔離測試回報內容。",
            status="resolved",
            created_at="2026-10-20T06:00:00Z",
            internal_note="never public",
        )


def test_appeals_keep_user_session_and_cannot_invent_reversal_actions():
    calls = []
    store = SimpleNamespace(token="synthetic", _request=lambda *a, **kw: calls.append(kw) or {})
    value = AppealInput(detail="重新檢視隔離測試案件的處置內容。", request_key=uuid4())
    Moderation(store).appeals("create", value.model_dump(mode="json"))
    assert calls[0]["token"] == "synthetic"
    with pytest.raises(ValidationError):
        AppealReview(
            expected_version=1,
            status="restore_group",
            response="不可自動替別人重新同意",
            request_key=uuid4(),
        )
