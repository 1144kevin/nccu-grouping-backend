"""Operator API uses the caller's verified session, never a service-role credential."""

from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import Field, field_validator

from app.core_cloud.store import SupabaseCoreStore
from app.stealth.schemas import StrictModel


def request_identity(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError:
        return str(uuid5(NAMESPACE_URL, "core-moderation:" + value))


class RequestIdentity(StrictModel):
    # App replay keys are opaque post-... strings, not authentication secrets.
    request_key: str = Field(min_length=12, max_length=100)

    @field_validator("request_key", mode="before")
    @classmethod
    def accept_uuid_clients(cls, value):
        return str(value) if isinstance(value, UUID) else value


class ReviewInput(RequestIdentity):
    expected_version: int = Field(ge=1, strict=True)
    action: Literal["review", "resolve", "dismiss", "cancel_post", "hide_message"]
    public_response: str = Field(min_length=5, max_length=300)
    internal_note: str = Field(default="", max_length=1000)


class PenaltyInput(RequestIdentity):
    expected_version: int = Field(ge=1, strict=True)
    points: int = Field(ge=1, le=100, strict=True)
    public_response: str = Field(min_length=5, max_length=300)
    internal_note: str = Field(default="", max_length=1000)


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
        args = {"report_id": str(report_id), **value.model_dump(mode="json")}
        args["request_key"] = request_identity(value.request_key)
        return self.rpc("review", args)

    def penalize(self, report_id: UUID, value: PenaltyInput):
        args = {"report_id": str(report_id), **value.model_dump(mode="json")}
        args["request_key"] = request_identity(value.request_key)
        return self.store._request(
            "POST",
            "/rest/v1/rpc/core_moderation_penalize",
            body={"args": args},
            token=self.store.token,
        )

    def appeals(self, action: str, args=None):
        if args and "request_key" in args:
            args = {**args, "request_key": request_identity(args["request_key"])}
        return self.store._request(
            "POST",
            "/rest/v1/rpc/core_appeals",
            body={"action": action, "args": args or {}},
            token=self.store.token,
        )


class AppealInput(RequestIdentity):
    detail: str = Field(min_length=10, max_length=1000)


class AppealReview(RequestIdentity):
    expected_version: int = Field(ge=1, strict=True)
    status: Literal["resolved", "dismissed"]
    response: str = Field(min_length=5, max_length=300)
