"""Operator API uses the caller's verified session, never a service-role credential."""

from typing import Literal
from uuid import UUID

from pydantic import Field

from app.core_cloud.store import SupabaseCoreStore
from app.stealth.schemas import StrictModel


class ReviewInput(StrictModel):
    expected_version: int = Field(ge=1, strict=True)
    action: Literal["review", "resolve", "dismiss", "cancel_post", "hide_message"]
    public_response: str = Field(min_length=5, max_length=300)
    internal_note: str = Field(default="", max_length=1000)
    request_key: UUID


class Moderation:
    def __init__(self, store: SupabaseCoreStore):
        self.store = store

    def rpc(self, action, args=None):
        return self.store._request(
            "POST",
            "/rest/v1/rpc/core_moderation",
            body={"action": action, "args": args or {}},
            token=self.store.token,
        )

    def review(self, report_id: UUID, value: ReviewInput):
        return self.rpc("review", {"report_id": str(report_id), **value.model_dump(mode="json")})

    def appeals(self, action: str, args=None):
        return self.store._request(
            "POST",
            "/rest/v1/rpc/core_appeals",
            body={"action": action, "args": args or {}},
            token=self.store.token,
        )


class AppealInput(StrictModel):
    detail: str = Field(min_length=10, max_length=1000)
    request_key: UUID


class AppealReview(StrictModel):
    expected_version: int = Field(ge=1, strict=True)
    status: Literal["resolved", "dismissed"]
    response: str = Field(min_length=5, max_length=300)
    request_key: UUID
