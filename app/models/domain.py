from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    JoinRequestStatus,
    MediaType,
    PostStatus,
    RecommendationAction,
    ReportStatus,
    ReportTargetType,
)


class Profile(Base, TimestampMixin):
    __tablename__ = "profiles"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(40), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    bio: Mapped[str] = mapped_column(String(160), default="", server_default="")
    trust_level: Mapped[int] = mapped_column(SmallInteger, default=1, server_default="1")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Post(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "posts"
    __table_args__ = (
        CheckConstraint("capacity BETWEEN 2 AND 20", name="capacity_range"),
        Index("ix_posts_feed", "status", "deadline_at", "created_at"),
    )

    author_id: Mapped[UUID] = mapped_column(ForeignKey("profiles.id"), index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    location_text: Mapped[str | None] = mapped_column(String(160))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    capacity: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[PostStatus] = mapped_column(
        Enum(PostStatus, name="post_status"), default=PostStatus.OPEN, index=True
    )
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list, server_default="{}")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PostMedia(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "post_media"
    __table_args__ = (UniqueConstraint("post_id", "position"),)

    post_id: Mapped[UUID] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    media_type: Mapped[MediaType] = mapped_column(Enum(MediaType, name="media_type"))
    storage_path: Mapped[str] = mapped_column(String(500), unique=True)
    position: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")


class JoinRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "join_requests"
    __table_args__ = (UniqueConstraint("post_id", "requester_id"),)

    post_id: Mapped[UUID] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    requester_id: Mapped[UUID] = mapped_column(ForeignKey("profiles.id"), index=True)
    message: Mapped[str] = mapped_column(String(300), default="", server_default="")
    status: Mapped[JoinRequestStatus] = mapped_column(
        Enum(JoinRequestStatus, name="join_request_status"),
        default=JoinRequestStatus.PENDING,
        index=True,
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Conversation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "conversations"

    post_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("posts.id", ondelete="CASCADE"), unique=True, index=True
    )


class ConversationMember(Base):
    __tablename__ = "conversation_members"

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    muted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class Message(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation_sent", "conversation_id", "sent_at"),)

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    sender_id: Mapped[UUID] = mapped_column(ForeignKey("profiles.id"))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Notification(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_created", "user_id", "created_at"),)

    user_id: Mapped[UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[str] = mapped_column(String(500), nullable=False)
    target_path: Mapped[str | None] = mapped_column(String(300))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, server_default="{}")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DeviceToken(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "device_tokens"
    __table_args__ = (UniqueConstraint("user_id", "token"),)

    user_id: Mapped[UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    token: Mapped[str] = mapped_column(String(255), unique=True)
    platform: Mapped[str] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class UserPreference(Base, TimestampMixin):
    __tablename__ = "user_preferences"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True
    )
    preferred_tags: Mapped[list[str]] = mapped_column(
        ARRAY(String(40)), default=list, server_default="{}"
    )
    notify_join_requests: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_messages: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_recommendations: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_deadlines: Mapped[bool] = mapped_column(Boolean, default=True)


class RecommendationEvent(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "recommendation_events"
    __table_args__ = (Index("ix_recommendation_user_created", "user_id", "created_at"),)

    user_id: Mapped[UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    post_id: Mapped[UUID] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    action: Mapped[RecommendationAction] = mapped_column(
        Enum(RecommendationAction, name="recommendation_action")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PostEmbedding(Base):
    __tablename__ = "post_embeddings"

    post_id: Mapped[UUID] = mapped_column(
        ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(1536), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Block(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "blocks"
    __table_args__ = (UniqueConstraint("blocker_id", "blocked_id"),)

    blocker_id: Mapped[UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    blocked_id: Mapped[UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Report(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "reports"

    reporter_id: Mapped[UUID] = mapped_column(ForeignKey("profiles.id"), index=True)
    target_type: Mapped[ReportTargetType] = mapped_column(
        Enum(ReportTargetType, name="report_target_type")
    )
    target_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    reason: Mapped[str] = mapped_column(String(80), nullable=False)
    details: Mapped[str] = mapped_column(String(500), default="", server_default="")
    status: Mapped[ReportStatus] = mapped_column(
        Enum(ReportStatus, name="report_status"), default=ReportStatus.PENDING, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
