"""Account-scoped settings, inbox preferences and private reports."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator, model_validator

from app.core_cloud.store import SupabaseCoreStore
from app.stealth.schemas import StrictModel, Tag


class NotificationPreferences(StrictModel):
    joinRequests: bool = Field(default=True, strict=True)
    messages: bool = Field(default=True, strict=True)
    recommendations: bool = Field(default=False, strict=True)
    deadlineReminders: bool = Field(default=False, strict=True)
    stealthInvites: bool = Field(default=True, strict=True)


class AccountPreferences(StrictModel):
    notifications: NotificationPreferences = Field(default_factory=NotificationPreferences)
    preferred_tags: list[Tag] = Field(default_factory=list, max_length=24)
    custom_locations: list[Tag] = Field(default_factory=list, max_length=24)

    @model_validator(mode="after")
    def locations_are_selected(self):
        if len(set(self.custom_locations)) != len(self.custom_locations) or not set(
            self.custom_locations
        ).issubset(self.preferred_tags):
            raise ValueError("invalid custom locations")
        return self

    @field_validator("preferred_tags")
    @classmethod
    def tags(cls, values):
        if len(values) != len(set(values)) or any("\0" in value for value in values):
            raise ValueError("invalid preference tags")
        for value in values:
            value.encode("utf-8")
        return values


class PreferencesView(AccountPreferences):
    version: int = Field(ge=0)


class PreferencesUpdate(StrictModel):
    expected_version: int = Field(strict=True, ge=0, le=2147483646)
    value: AccountPreferences


class InboxInput(StrictModel):
    muted: bool = Field(strict=True)
    archived: bool = Field(strict=True)


class InboxPreference(InboxInput):
    post_id: UUID


class InboxPreferences(StrictModel):
    items: list[InboxPreference]


class SavedPostInput(StrictModel):
    hidden: bool = Field(strict=True)
    reminder: bool = Field(strict=True)


class SavedPostPreference(SavedPostInput):
    post_id: UUID
    reminder_at: AwareDatetime
    deadline_at: AwareDatetime
    post_content: str
    status: Literal["scheduled", "due", "expired", "cancelled"]


class SavedPostPreferences(StrictModel):
    items: list[SavedPostPreference]


class ReportInput(StrictModel):
    evidence_image_id: UUID
    kind: Literal["feedback", "post", "message", "user"]
    target_id: UUID | None = None
    reason: Literal["other", "spam", "harassment", "inappropriate", "fraud"] = "other"
    detail: str = Field(min_length=10, max_length=1000)
    platform: Literal["web", "ios", "android", "unknown"] = "unknown"
    app_version: str = Field(default="", max_length=40)

    @field_validator("detail", "app_version")
    @classmethod
    def safe_text(cls, value):
        if "\0" in value:
            raise ValueError("invalid report text")
        value.encode("utf-8")
        return value

    @model_validator(mode="after")
    def target(self):
        if (self.kind == "feedback") != (self.target_id is None):
            raise ValueError("report target required")
        return self


class ReportView(ReportInput):
    # Earlier reports remain readable; all new reports require an image.
    evidence_image_id: UUID | None = None
    id: UUID
    status: Literal["received", "reviewing", "resolved", "dismissed"]
    public_response: str = ""
    review_version: int = 1
    created_at: AwareDatetime


class ReportPage(StrictModel):
    items: list[ReportView]


class Engagement:
    def __init__(self, store: SupabaseCoreStore):
        self.store = store

    def rpc(self, action: str, args: dict | None = None):
        return self.store._request(
            "POST",
            "/rest/v1/rpc/core_engagement",
            body={"action": action, "args": args or {}},
            token=self.store.token,
        )

    def preferences(self):
        return PreferencesView.model_validate(self.rpc("preferences_get"))

    def save_preferences(self, value: PreferencesUpdate):
        return PreferencesView.model_validate(
            self.rpc("preferences_put", value.model_dump(exclude_unset=True))
        )

    def inbox(self):
        return InboxPreferences.model_validate(self.rpc("inbox_list"))

    def save_inbox(self, post_id: UUID, value: InboxInput):
        return InboxPreference.model_validate(
            self.rpc(
                "inbox_put",
                {"post_id": str(post_id), **value.model_dump()},
            )
        )

    def reports(self):
        if self.store.settings.core_moderation_enabled:
            from app.core_cloud.moderation import Moderation

            return ReportPage.model_validate(Moderation(self.store).rpc("mine"))
        return ReportPage.model_validate(self.rpc("reports"))

    def report(self, value: ReportInput, key: str):
        return ReportView.model_validate(
            self.rpc(
                "report",
                {"value": value.model_dump(mode="json"), "request_key": key},
            )
        )
