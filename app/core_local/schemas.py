import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginInput(Input):
    # Never normalize passwords; whitespace is part of the user's credential.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    email: str = Field(min_length=3, max_length=254)
    password: SecretStr

    @field_validator("email")
    @classmethod
    def email_format(cls, value: str) -> str:
        value = value.strip().casefold()
        if not re.fullmatch(r"[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+", value):
            raise ValueError("invalid_email")
        return value

    @field_validator("password")
    @classmethod
    def password_length(cls, value: SecretStr) -> SecretStr:
        if not 10 <= len(value.get_secret_value()) <= 128:
            raise ValueError("password_must_be_10_to_128_characters")
        try:
            value.get_secret_value().encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("password_invalid_unicode") from None
        return value


class RegisterInput(LoginInput):
    display_name: str = Field(min_length=1, max_length=40)

    @field_validator("display_name", mode="before")
    @classmethod
    def trim_display_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class ProfileView(BaseModel):
    id: UUID
    email: str
    display_name: str
    created_at: datetime
    bio: str = ""
    badge: Literal["🍜", "🥐", "🍕", "🧋", "🍱", "🍍"] = "🍱"
    version: int = 1
    auth_mode: Literal["local_development", "supabase"] = "local_development"
    email_verified: bool = False


class ProfileInput(Input):
    display_name: str = Field(min_length=1, max_length=40)
    bio: str = Field(max_length=160)
    badge: Literal["🍜", "🥐", "🍕", "🧋", "🍱", "🍍"]
    expected_version: int = Field(ge=1, strict=True)


class SessionView(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    user: ProfileView


class RefreshInput(Input):
    refresh_token: SecretStr = Field(min_length=8, max_length=2048)


class DemoCleanupView(BaseModel):
    deleted_posts: int = Field(ge=0)
    kept_posts: int = Field(ge=0)


class MessageInput(Input):
    content: str = Field(min_length=1, max_length=2000)

    @field_validator("content")
    @classmethod
    def valid_text(cls, value: str) -> str:
        if "\0" in value:
            raise ValueError("invalid_chat_input")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("invalid_chat_input") from None
        return value


class MessageView(BaseModel):
    id: UUID
    post_id: UUID
    sender_id: UUID
    sender_name: str
    sender_badge: str
    content: str
    created_at: datetime


class ConfirmationView(BaseModel):
    confirmation_required: Literal[True] = True
    message: Literal["check_email_to_confirm"] = "check_email_to_confirm"


class Location(Input):
    name: str = Field(min_length=1, max_length=100)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    address: str | None = Field(default=None, max_length=500)
    source: Literal["openstreetmap"] | None = None


class PostInput(Input):
    content: str = Field(min_length=10, max_length=500)
    tags: list[str] = Field(min_length=1, max_length=10)
    deadline_at: datetime
    capacity: int = Field(ge=2, le=20, strict=True)
    location: Location
    price_per_person: Decimal | None = Field(
        default=None, ge=0, le=Decimal("999999.99"), decimal_places=2, allow_inf_nan=False
    )

    @field_validator("deadline_at")
    @classmethod
    def aware_deadline(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone_required")
        return value.astimezone(UTC)

    @field_validator("tags")
    @classmethod
    def valid_tags(cls, values: list[str]) -> list[str]:
        tags = list(dict.fromkeys(tag.strip() for tag in values))
        if any(not tag or len(tag) > 40 for tag in tags):
            raise ValueError("invalid_tag")
        return tags


class PostView(BaseModel):
    is_demo: bool = False
    image_id: UUID | None = None
    id: UUID
    author_id: UUID
    author_name: str
    author_badge: str = "🍱"
    content: str
    tags: list[str]
    location: Location
    price_per_person: Decimal | None
    deadline_at: datetime
    capacity: int
    joined: int
    status: Literal["open", "full", "expired", "cancelled"]
    my_status: Literal["host", "none", "pending", "accepted", "rejected", "cancelled"]
    created_at: datetime
    version: int = 1
    can_edit: bool = False


class PostEditInput(PostInput):
    expected_version: int = Field(ge=1, strict=True)


class PostPage(BaseModel):
    items: list[PostView]
    next_cursor: str | None = None


class JoinView(BaseModel):
    id: UUID
    post_id: UUID
    requester_id: UUID
    requester_name: str
    status: Literal["pending", "accepted", "rejected", "cancelled"]
    created_at: datetime
    decided_at: datetime | None


class DecisionInput(Input):
    status: Literal["accepted", "rejected"]


class JoinInput(Input):
    expected_version: int = Field(ge=1, strict=True)


class ChatPage(BaseModel):
    post: PostView
    messages: list[MessageView]
    next_cursor: str | None = None
    can_send: bool


class ConversationView(BaseModel):
    post: PostView
    last_message: MessageView | None = None


class ConversationPage(BaseModel):
    items: list[ConversationView]
    next_cursor: str | None = None


class ChatUnread(BaseModel):
    post_id: UUID
    unread_count: int = Field(ge=0)


class ChatActivity(BaseModel):
    groups: list[ChatUnread]
    total_unread: int = Field(ge=0)
    pending_requests: int = Field(ge=0)


class ChatReadInput(Input):
    message_id: UUID


class ChatReadView(BaseModel):
    ok: Literal[True] = True


class PublicProfileView(BaseModel):
    id: UUID
    display_name: str
    bio: str
    badge: str
    created_at: datetime
    active_posts: int = Field(ge=0)


class ServerClockView(BaseModel):
    server_time: datetime


class OrderActivityView(BaseModel):
    id: UUID
    post_id: UUID
    post_content: str
    requester_id: UUID
    requester_name: str
    status: Literal["pending", "accepted", "rejected", "cancelled"]
    kind: Literal["hosted_request", "my_request"]
    occurred_at: datetime
    post_cancelled: bool
    post_expired: bool


class OrderActivityPage(BaseModel):
    items: list[OrderActivityView]
    next_cursor: str | None = None
