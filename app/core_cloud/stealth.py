"""Cloud stealth uses the current user's verified Supabase session, never an admin key."""

from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import HTTPException
from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator

from app.core_cloud.planner import AgentTermsDraft, PlanningMember, plan_terms
from app.core_cloud.store import SupabaseCoreStore
from app.stealth.schemas import Agent, AgentInput, Signal, SignalInput, StrictModel


class CloudTerms(StrictModel):
    content: str = Field(min_length=10, max_length=300)
    cost_per_person_twd: int = Field(ge=0, le=100000, strict=True)
    deadline_at: AwareDatetime
    order_member: int = Field(ge=1, le=3, strict=True)
    pickup_member: int = Field(ge=1, le=3, strict=True)
    cost_change_note: str = Field(default="", max_length=120)

    @field_validator("cost_change_note")
    @classmethod
    def safe_note(cls, value: str) -> str:
        _safe_text(value)
        return value.strip()

    @field_validator("content")
    @classmethod
    def database_text(cls, value: str) -> str:
        _safe_text(value)
        if len(value.strip()) < 10:
            raise ValueError("invalid content")
        return value.strip()


class CloudAgentInput(AgentInput):
    include_demo: bool = Field(default=False, strict=True)

    @field_validator("name")
    @classmethod
    def database_text(cls, value: str) -> str:
        _safe_text(value)
        return value


def _safe_text(value: str) -> None:
    if "\0" in value:
        raise ValueError("invalid text")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("invalid text") from exc


class CloudSignalInput(SignalInput):
    @field_validator("title", "tags", "dietary_requirements", "dietary_verified")
    @classmethod
    def database_text(cls, value: str | list[str]) -> str | list[str]:
        for item in [value] if isinstance(value, str) else value:
            _safe_text(item)
        return value


class CloudAgent(Agent):
    include_demo: bool = False


class CloudMatch(BaseModel):
    id: UUID
    created_at: AwareDatetime
    category: str
    location_id: str
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    expires_at: AwareDatetime
    budget_max_twd: int | None
    known_cost_twd: int | None
    member_count: int = Field(ge=2, le=3)
    score: int = Field(ge=0, le=100)
    reasons: list[str]
    unknowns: list[str]
    my_decision: Literal["pending", "accepted", "declined"]
    status: Literal["pending", "ready", "unavailable", "formed"]
    my_signal_title: str
    my_roles: list[str]
    invitation_text: str
    why_me: list[str]
    is_llm: Literal[False] = False
    is_demo: bool = False
    semantic_proposal: bool = False
    terms: CloudTerms | None = None
    terms_revision: int = 0
    my_member_number: int = 1
    host_member_number: int | None = None
    confirmed_count: int = 0
    my_terms_accepted: bool = False
    formed_post_id: UUID | None = None
    formed_at: AwareDatetime | None = None
    coordination_opened: bool = False
    planning_members: list[PlanningMember] = Field(default_factory=list, exclude=True)
    agent_draft: AgentTermsDraft | None = None
    highlights: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def draft_from_verified_snapshot(self):
        # Automatic read-only planning: does not submit terms, change revision or consent.
        self.agent_draft = None
        if self.status in ("pending", "ready"):
            self.agent_draft = plan_terms(
                category=self.category,
                location=self.location_id,
                cost=self.known_cost_twd,
                budget=self.budget_max_twd,
                ends_at=self.ends_at,
                member_count=self.member_count,
                members=self.planning_members,
                unknowns=self.unknowns,
            )
        highlights: list[str] = []
        partners = [m for m in self.planning_members if m.number != self.my_member_number]
        if any("pickup" in m.roles for m in partners):
            highlights.append("對方願意跑這一趟幫忙取餐")
        if any("order" in m.roles for m in partners):
            highlights.append("對方願意主動負責下單")
        if any("coupon" in m.roles for m in partners):
            highlights.append("對方手上有優惠券願意分享（可折抵或享買一送一）")
        if self.known_cost_twd is not None:
            if self.budget_max_twd is not None and self.known_cost_twd < self.budget_max_twd:
                highlights.append(
                    f"對方報價每人只要 NT$ {self.known_cost_twd}（比你的預算上限還省 NT$ {self.budget_max_twd - self.known_cost_twd}）"
                )
            else:
                highlights.append(f"已確認每人分攤金額約 NT$ {self.known_cost_twd}")
        place_names = {"main_gate": "政大正門", "east_gate": "東側門", "south_gate": "南門"}
        place = place_names.get(self.location_id, self.location_id)
        if self.location_id not in ("main_gate", "east_gate", "south_gate", "都可以"):
            highlights.append(f"對方今天也會去／經過「{place}」，剛好可以順路一起湊單")
        else:
            highlights.append(f"雙方都方便在「{place}」碰面分攤")
        for r in self.reasons:
            if r not in ("取餐地點一致", "可用時間有交集", "餐點分類符合本人確認的選項") and r not in highlights:
                highlights.append(r)
        self.highlights = highlights[:5]
        return self


class CloudMatches(BaseModel):
    items: list[CloudMatch]


class CloudSignals(BaseModel):
    items: list[Signal]


class CloudRun(BaseModel):
    created: int = Field(ge=0, le=5)
    is_llm: Literal[False] = False


class CloudSignalCreated(BaseModel):
    signal: Signal
    matching: Literal["completed", "paused", "quiet", "cooldown", "unavailable"]
    new_matches: int = 0


class CloudStealth:
    def __init__(self, store: SupabaseCoreStore):
        self.store = store

    def rpc(self, action: str, args: dict | None = None):
        return self.store._request(
            "POST",
            "/rest/v1/rpc/core_stealth",
            body={"action": action, "args": args or {}},
            token=self.store.token,
        )

    def agent(self) -> CloudAgent | None:
        value = self.rpc("agent_get")
        return CloudAgent.model_validate(value) if value else None

    def save_agent(self, value: CloudAgentInput) -> CloudAgent:
        return CloudAgent.model_validate(
            self.rpc(
                "agent_put",
                {
                    "value": value.model_dump(mode="json", exclude={"expected_version"}),
                    "expected_version": value.expected_version,
                },
            )
        )

    def create(self, value: SignalInput, key: str) -> CloudSignalCreated:
        if type(getattr(self.store, "_request", None)).__name__ == "method":
            now = datetime.now(UTC)
            if any(item.status == "active" and item.expires_at > now for item in self.signals().items):
                raise HTTPException(429, "active_signal_limit")
        signal = Signal.model_validate(
            self.rpc(
                "signal_create",
                {
                    "value": value.model_dump(mode="json"),
                    "request_key": str(uuid5(NAMESPACE_URL, "core-stealth:" + key)),
                },
            )
        )
        # The signal is committed first. A matching outage must never imply it was lost.
        try:
            run = self.run(3)
            return CloudSignalCreated(signal=signal, matching="completed", new_matches=run.created)
        except HTTPException as error:
            state = {
                "agent_not_enabled": "paused",
                "agent_quiet_hours": "quiet",
                "match_cooldown": "cooldown",
            }.get(str(error.detail), "unavailable")
            return CloudSignalCreated(signal=signal, matching=state)

    def signals(self) -> CloudSignals:
        return CloudSignals.model_validate(self.rpc("signals"))

    def revoke(self, signal_id: UUID) -> Signal:
        return Signal.model_validate(self.rpc("revoke", {"signal_id": str(signal_id)}))

    def matches(self) -> CloudMatches:
        return CloudMatches.model_validate(self.rpc("matches"))

    def run(self, maximum: int) -> CloudRun:
        from app.core_cloud.discovery import discover
        from app.stealth.ollama import LocalModelError
        from app.stealth.provider_registry import local_semantic_matcher

        try:
            result = discover(self, local_semantic_matcher(self.store.settings).embeddings, maximum)
        except LocalModelError as exc:
            raise HTTPException(
                429 if str(exc) == "openai_quota_or_rate_limit" else 503, str(exc)
            ) from None
        return CloudRun(created=result["created"], is_llm=False)

    def decide(self, proposal_id: UUID, decision: str) -> CloudMatch:
        return CloudMatch.model_validate(
            self.rpc("decision", {"proposal_id": str(proposal_id), "decision": decision})
        )

    def formation(self, action: str, proposal_id: UUID, revision: int, **args) -> CloudMatch:
        return CloudMatch.model_validate(
            self.store._request(
                "POST",
                "/rest/v1/rpc/core_stealth_formation",
                body={
                    "action": action,
                    "args": {
                        "proposal_id": str(proposal_id),
                        "expected_revision": revision,
                        **args,
                    },
                },
                token=self.store.token,
            )
        )
