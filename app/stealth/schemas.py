from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.stealth.locations import normalize_location


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SignalKind(StrEnum):
    NEED = "need"
    OFFER = "offer"
    PLAN = "plan"


class SignalStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"
    CONSUMED = "consumed"


class ProposalStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    DECLINED = "declined"
    INVALIDATED = "invalidated"
    FORMED = "formed"


Role = Literal["order", "pickup", "coupon", "share"]
Category = Literal["pizza", "meal", "drink", "bakery", "snack"]
Location = Annotated[str, Field(min_length=1, max_length=120), AfterValidator(normalize_location)]
DeclineReason = Literal["not_now", "time", "budget", "food", "privacy", "other"]
Tag = Annotated[str, Field(min_length=1, max_length=32)]
Money = Annotated[int, Field(strict=True, ge=0, le=100_000)]


class AgentSettings(StrictModel):
    name: str = Field(default="我的分身", min_length=1, max_length=40)
    enabled: bool = Field(default=False, strict=True)
    daily_invite_limit: int = Field(default=3, strict=True, ge=1, le=10)
    timezone: str = "Asia/Taipei"
    quiet_start_hour: int | None = Field(default=22, strict=True, ge=0, le=23)
    quiet_end_hour: int | None = Field(default=8, strict=True, ge=0, le=23)
    exploration: Literal["familiar", "balanced"] = "familiar"
    tone: Literal["direct", "warm", "playful"] = "warm"
    avatar_key: Literal["rice", "pizza", "tea", "cat"] = "rice"

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("unknown timezone") from exc
        return value

    @model_validator(mode="after")
    def paired_quiet_hours(self) -> AgentSettings:
        if (self.quiet_start_hour is None) != (self.quiet_end_hour is None):
            raise ValueError("both quiet hours must be provided or null")
        if self.quiet_start_hour is not None and self.quiet_start_hour == self.quiet_end_hour:
            raise ValueError("quiet hours must not be equal; use null to disable")
        return self


class AgentInput(AgentSettings):
    expected_version: int = Field(default=0, strict=True, ge=0)


class Agent(AgentSettings):
    user_id: UUID
    version: int = Field(ge=1)
    policy_version: int = Field(default=1, ge=1)
    updated_at: AwareDatetime

    def quiet_at(self, now: datetime) -> bool:
        if self.quiet_start_hour is None:
            return False
        hour = now.astimezone(ZoneInfo(self.timezone)).hour
        start, end = self.quiet_start_hour, self.quiet_end_hour
        assert end is not None
        return start <= hour < end if start < end else hour >= start or hour < end


class SignalInput(StrictModel):
    kind: SignalKind
    category: Category
    acceptable_categories: list[Category] = Field(default_factory=list, max_length=5)
    title: str = Field(min_length=1, max_length=160)
    location_id: Location
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    expires_at: AwareDatetime
    budget_max_twd: Money | None = None
    known_cost_twd: Money | None = None
    tags: list[Tag] = Field(default_factory=list, max_length=12)
    dietary_requirements: list[Tag] = Field(default_factory=list, max_length=12)
    dietary_verified: list[Tag] = Field(default_factory=list, max_length=12)
    roles: list[Role] = Field(default_factory=list, max_length=4)

    @field_validator(
        "tags", "dietary_requirements", "dietary_verified", "roles", "acceptable_categories"
    )
    @classmethod
    def unique_items(cls, values: list[str]) -> list[str]:
        return sorted(set(values))

    @field_validator("starts_at", "ends_at", "expires_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def coherent_window(self) -> SignalInput:
        if self.starts_at >= self.ends_at:
            raise ValueError("starts_at must precede ends_at")
        if (self.ends_at - self.starts_at).total_seconds() > 24 * 3600:
            raise ValueError("signal window is limited to 24 hours")
        if self.expires_at > self.ends_at:
            raise ValueError("expires_at must not be later than ends_at")
        if self.kind == SignalKind.PLAN and "pickup" not in self.roles:
            raise ValueError("plan must explicitly authorize pickup")
        if self.kind == SignalKind.OFFER and not self.roles:
            raise ValueError("offer must specify at least one contributed role")
        if self.kind == SignalKind.OFFER and self.acceptable_categories:
            raise ValueError("offer must describe its actual category, not alternative resources")
        return self


class Signal(SignalInput):
    id: UUID
    owner_id: UUID
    status: SignalStatus = SignalStatus.ACTIVE
    version: int = Field(default=1, ge=1)
    created_at: AwareDatetime

    def usable_at(self, now: datetime) -> bool:
        return self.status == SignalStatus.ACTIVE and self.expires_at > now and self.ends_at > now


class ProposalMember(StrictModel):
    user_id: UUID
    signal_id: UUID
    signal_version: int = Field(strict=True, ge=1)
    agent_version: int = Field(strict=True, ge=1)
    decision: Literal["pending", "accepted", "declined"] = "pending"
    decided_at: AwareDatetime | None = None
    decline_reason: DeclineReason | None = None
    accepted_terms_revision: int | None = Field(default=None, strict=True, ge=0, le=3)


class Proposal(StrictModel):
    id: UUID
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    category: Category
    location_id: Location
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    expires_at: AwareDatetime
    budget_max_twd: Money | None
    known_cost_twd: Money | None = None
    score: int = Field(ge=0, le=100)
    reasons: list[str]
    unknowns: list[str]
    members: list[ProposalMember] = Field(min_length=2, max_length=3)
    status: ProposalStatus = ProposalStatus.PENDING
    created_at: AwareDatetime
    terms_revision: int = Field(default=0, strict=True, ge=0, le=3)
    terms: FormationInput | None = None
    terms_author_id: UUID | None = None

    @model_validator(mode="after")
    def coherent_members_and_window(self) -> Proposal:
        if len({m.user_id for m in self.members}) != len(self.members):
            raise ValueError("each participant must be distinct")
        if len({m.signal_id for m in self.members}) != len(self.members):
            raise ValueError("each participant must have a distinct signal")
        if self.starts_at >= self.ends_at or self.expires_at > self.ends_at:
            raise ValueError("invalid proposal time window")
        if self.terms_revision == 0:
            if self.terms is not None or self.terms_author_id is not None:
                raise ValueError("initial proposal cannot contain final terms")
        elif self.terms is None or self.terms_author_id not in {m.user_id for m in self.members}:
            raise ValueError("final terms must have a participating proposer")
        return self


class ProposalView(StrictModel):
    """No other user's identity, raw signal, or decision is exposed."""

    id: UUID
    category: Category
    location_id: Location
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    expires_at: AwareDatetime
    budget_max_twd: Money | None
    known_cost_twd: Money | None
    score: int
    reasons: list[str]
    unknowns: list[str]
    member_count: int
    my_decision: Literal["pending", "accepted", "declined"]
    my_decline_reason: DeclineReason | None = None
    my_roles: list[Role]
    invitation_text: str
    why_me: list[str]
    status: Literal["pending", "ready", "unavailable", "formed"]
    terms_revision: int
    terms: FormationInput | None
    my_accepted_terms_revision: int | None
    can_form: bool


class DecisionInput(StrictModel):
    decision: Literal["accepted", "declined"]
    decline_reason: DeclineReason | None = None
    expected_terms_revision: int = Field(default=0, strict=True, ge=0, le=3)

    @model_validator(mode="after")
    def reason_only_for_decline(self) -> DecisionInput:
        if self.decision != "declined" and self.decline_reason is not None:
            raise ValueError("decline_reason is only valid when declining")
        return self


class FormationInput(StrictModel):
    content: str = Field(min_length=10, max_length=500)
    deadline_at: AwareDatetime
    confirmed_cost_twd: Money
    confirmed_roles: dict[Role, Annotated[bool, Field(strict=True)]] = Field(default_factory=dict)
    host_roles: list[Role] = Field(default_factory=list, max_length=4)
    acknowledge_unverified_details: Literal[True]

    @field_validator("host_roles")
    @classmethod
    def unique_host_roles(cls, roles: list[str]) -> list[str]:
        return sorted(set(roles))

    @field_validator("deadline_at")
    @classmethod
    def utc_deadline(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @field_validator("acknowledge_unverified_details", mode="before")
    @classmethod
    def explicit_acknowledgement(cls, value: object) -> bool:
        if value is not True:
            raise ValueError("acknowledgement must be the boolean true")
        return True


class TermsInput(StrictModel):
    expected_terms_revision: int = Field(strict=True, ge=0, le=3)
    terms: FormationInput


class Formation(StrictModel):
    id: UUID
    proposal_id: UUID
    host_id: UUID
    post_id: UUID
    conversation_id: UUID
    created_at: AwareDatetime
    storage: Literal["local_sqlite", "postgresql"] = "local_sqlite"


class DraftInput(StrictModel):
    text: str = Field(min_length=1, max_length=1000)


class DraftContent(StrictModel):
    kind_hint: SignalKind | None = None
    category_hint: Category | None = None
    acceptable_categories_hint: list[Category] = Field(default_factory=list, max_length=5)
    location_hint: Location | None = None
    budget_max_twd_hint: Money | None = None
    roles_hint: list[Role] = Field(default_factory=list, max_length=4)
    title: str = Field(min_length=1, max_length=160)
    needs_confirmation: list[str] = Field(max_length=12)


class DraftOutput(DraftContent):
    provider: Literal["offline_rules", "ollama_local"] = "offline_rules"
    is_llm: bool = Field(default=False, strict=True)


class MatchRunInput(StrictModel):
    max_proposals: int = Field(default=3, strict=True, ge=1, le=5)


class MatchRunOutput(StrictModel):
    items: list[ProposalView]
    created: int
    provider: Literal["constraint_rules_v1"] = "constraint_rules_v1"


class ProposalPage(StrictModel):
    items: list[ProposalView]
    next_cursor: str | None = None


class SignalPage(StrictModel):
    items: list[Signal]
    next_cursor: str | None = None


# FormationInput is intentionally shared by final-terms review and formation.
Proposal.model_rebuild()
ProposalView.model_rebuild()
