"""PostgreSQL schema for the additive stealth feature.

The local prototype uses its own adapter. These tables are not created on import.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class StealthAgent(Base, TimestampMixin):
    __tablename__ = "stealth_agents"
    __table_args__ = (
        CheckConstraint("version >= 1", name="agent_version_positive"),
        CheckConstraint("policy_version >= 1", name="agent_policy_version_positive"),
        CheckConstraint("daily_invite_limit BETWEEN 1 AND 10", name="invite_limit_range"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    daily_invite_limit: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Taipei")
    settings: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    policy_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class StealthSignal(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "stealth_signals"
    __table_args__ = (
        CheckConstraint("kind IN ('need','offer','plan')", name="signal_kind_valid"),
        CheckConstraint("status IN ('active','revoked','consumed')", name="signal_status_valid"),
        CheckConstraint("starts_at < ends_at", name="signal_window_valid"),
        CheckConstraint("expires_at <= ends_at", name="signal_expiry_valid"),
        CheckConstraint("budget_max_twd >= 0", name="signal_budget_nonnegative"),
        CheckConstraint("known_cost_twd >= 0", name="signal_cost_nonnegative"),
        CheckConstraint("version >= 1", name="signal_version_positive"),
        UniqueConstraint("id", "owner_id"),
        Index("ix_stealth_signal_candidates", "status", "location_id", "category", "expires_at"),
    )

    owner_id: Mapped[UUID] = mapped_column(ForeignKey("profiles.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    category: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(160))
    location_id: Mapped[str] = mapped_column(String(40))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    budget_max_twd: Mapped[int | None] = mapped_column(Integer)
    known_cost_twd: Mapped[int | None] = mapped_column(Integer)
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class StealthProposal(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "stealth_proposals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','ready','declined','invalidated','formed')",
            name="proposal_status_valid",
        ),
        CheckConstraint("score BETWEEN 0 AND 100", name="proposal_score_range"),
        CheckConstraint("starts_at < ends_at", name="proposal_window_valid"),
        Index("ix_stealth_proposal_status_expiry", "status", "expires_at"),
    )

    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    category: Mapped[str] = mapped_column(String(32))
    location_id: Mapped[str] = mapped_column(String(40))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    budget_max_twd: Mapped[int | None] = mapped_column(Integer)
    known_cost_twd: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[int] = mapped_column(Integer)
    explanation: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    algorithm_version: Mapped[str] = mapped_column(String(40), default="constraint_rules_v1")


class StealthProposalMember(Base):
    __tablename__ = "stealth_proposal_members"
    __table_args__ = (
        CheckConstraint("decision IN ('pending','accepted','declined')", name="decision_valid"),
        CheckConstraint(
            "decline_reason IS NULL OR (decision = 'declined' AND decline_reason IN "
            "('not_now','time','budget','food','privacy','other'))",
            name="decline_reason_valid",
        ),
        UniqueConstraint("proposal_id", "signal_id"),
        ForeignKeyConstraint(
            ["signal_id", "user_id"],
            ["stealth_signals.id", "stealth_signals.owner_id"],
            name="fk_stealth_member_signal_owner",
        ),
        Index("ix_stealth_members_user", "user_id", "proposal_id"),
    )

    proposal_id: Mapped[UUID] = mapped_column(
        ForeignKey("stealth_proposals.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("profiles.id"), primary_key=True)
    signal_id: Mapped[UUID] = mapped_column(ForeignKey("stealth_signals.id"), index=True)
    signal_version: Mapped[int] = mapped_column(Integer)
    agent_version: Mapped[int] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    decline_reason: Mapped[str | None] = mapped_column(String(32))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StealthFormation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "stealth_formations"

    proposal_id: Mapped[UUID] = mapped_column(ForeignKey("stealth_proposals.id"), unique=True)
    host_id: Mapped[UUID] = mapped_column(ForeignKey("profiles.id"))
    post_id: Mapped[UUID] = mapped_column(ForeignKey("posts.id"), unique=True)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"), unique=True)
    confirmed_terms: Mapped[dict] = mapped_column(JSONB)


class StealthEvent(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "stealth_events"
    __table_args__ = (Index("ix_stealth_events_actor_kind_at", "actor_id", "kind", "at"),)

    actor_id: Mapped[UUID] = mapped_column(ForeignKey("profiles.id"))
    proposal_id: Mapped[UUID | None] = mapped_column(ForeignKey("stealth_proposals.id"))
    kind: Mapped[str] = mapped_column(String(40))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Codes/metrics only; no raw prompts, chat content, tokens, or exact routes.
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")


class StealthMatchJob(Base):
    __tablename__ = "stealth_match_jobs"
    __table_args__ = (
        CheckConstraint("generation >= 1", name="job_generation_positive"),
        Index("ix_stealth_jobs_due", "next_attempt_at"),
    )

    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    generation: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
