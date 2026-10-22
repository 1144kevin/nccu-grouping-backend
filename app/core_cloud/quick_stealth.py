"""One-sentence requests. Defaults are proposals, never invented prices or consent."""

from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import Field, field_validator

from app.core_cloud.intake import IntakeFacts, IntakeRequest
from app.core_cloud.stealth import CloudAgentInput, CloudSignalCreated, CloudSignalInput
from app.stealth.ai import OfflineDraftProvider
from app.stealth.ollama import LocalModelError
from app.stealth.schemas import StrictModel

BRANDS = {
    "50嵐": ("50嵐", "50 嵐", "五十嵐"),
    "清心": ("清心",),
    "可不可": ("可不可",),
    "迷客夏": ("迷客夏",),
}


class QuickStart(StrictModel):
    text: str = Field(min_length=1, max_length=160)
    activate_agent: bool = Field(default=False, strict=True)

    @field_validator("text")
    @classmethod
    def safe_text(cls, value):
        if not value.strip() or "\0" in value:
            raise ValueError("invalid text")
        value.encode("utf-8")
        return value.strip()


class QuickResult(StrictModel):
    created: CloudSignalCreated
    sources: list[str]
    interpretation: Literal["local_model", "keyword_fallback", "retry"]


def prepare(text, history, *, interpreter=None, now=None):
    now = now or datetime.now(UTC)
    # Only the caller's recent requests. Past roles and past prices NEVER authorize new duties.
    recent = [
        s for s in history if s.created_at > now - timedelta(days=30) and s.status != "revoked"
    ]
    hint = OfflineDraftProvider().draft(text)
    facts = IntakeFacts(title=text, category=hint.category_hint, location_id=hint.location_hint)
    method = "keyword_fallback"
    if interpreter:
        try:
            facts = interpreter.interpret(IntakeRequest(turns=[text])).facts
            method = "local_model"
        except LocalModelError:
            pass
    brand = next(
        (brand for brand, aliases in BRANDS.items() if any(a in text for a in aliases)), None
    )
    category = "drink" if brand else facts.category or hint.category_hint
    if category is None:
        from fastapi import HTTPException

        raise HTTPException(422, "quick_category_unclear")
    previous = next((s for s in recent if s.category == category), None)
    location = (
        facts.location_id
        or hint.location_hint
        or (previous.location_id if previous else "main_gate")
    )
    delay = facts.delay_minutes if facts.delay_minutes is not None else 0
    duration = facts.duration_minutes if facts.duration_minutes is not None else 120
    # Relative dates must still pass the original seven-day and one-day window validators.
    starts = now + timedelta(minutes=delay)
    ends = starts + timedelta(minutes=duration)
    budget = facts.budget_max_twd
    if budget is None and not facts.budget_unknown and previous:
        budget = previous.budget_max_twd
    tags = list(
        dict.fromkeys([*(facts.tags or [category]), *(["品牌:" + brand] if brand else [])])
    )[:12]
    value = CloudSignalInput(
        kind=(
            facts.kind
            if (facts.kind == "offer" and facts.roles)
            or (facts.kind == "plan" and "pickup" in facts.roles)
            else "need"
        ),
        category=category,
        title=text,
        location_id=location,
        acceptable_categories=facts.acceptable_categories,
        starts_at=starts,
        ends_at=ends,
        expires_at=ends,
        budget_max_twd=budget,
        known_cost_twd=facts.known_cost_twd,
        roles=facts.roles,
        tags=tags,
        dietary_requirements=list(
            dict.fromkeys(
                [*facts.dietary_requirements, *(previous.dietary_requirements if previous else [])]
            )
        )[:12],
        dietary_verified=[],
    )
    sources = [
        "地點："
        + (
            "依這次描述"
            if facts.location_id or hint.location_hint
            else "沿用近30天同類需求"
            if previous
            else "首次建議政大正門，可在句子中更改"
        ),
        "時間："
        + (
            "依這次描述"
            if facts.delay_minutes is not None or facts.duration_minutes is not None
            else "暫找接下來兩小時，可在句子中更改"
        ),
        "預算："
        + (
            "依這次描述"
            if facts.budget_max_twd is not None
            else "沿用近30天同類需求"
            if budget is not None
            else "尚未設定，價格留待聊天室協調"
        ),
    ]
    return value, sources, method


def start(service, value: QuickStart, key: str, *, interpreter=None):
    from uuid import NAMESPACE_URL, uuid5

    from fastapi import HTTPException

    request_key = str(uuid5(NAMESPACE_URL, "core-stealth:" + key))
    saved = service.store._request(
        "POST",
        "/rest/v1/rpc/core_stealth_quick",
        body={"action": "lookup", "args": {"request_key": request_key}},
        token=service.store.token,
    )
    if saved:
        if saved["title"] != value.text:
            raise HTTPException(409, "idempotency_payload_conflict")
        return QuickResult(
            created=CloudSignalCreated(signal=saved, matching="completed"),
            sources=["已恢復上次送出的需求，未重複建立。"],
            interpretation="retry",
        )
    agent = service.agent()
    if not (agent and agent.enabled) and not value.activate_agent:
        raise HTTPException(409, "agent_not_enabled")
    signal, sources, method = prepare(value.text, service.signals().items, interpreter=interpreter)
    if not (agent and agent.enabled):
        data = (
            agent.model_dump(exclude={"user_id", "version", "policy_version", "updated_at"})
            if agent
            else dict(
                name="我的分身", quiet_start_hour=None, quiet_end_hour=None, exploration="balanced"
            )
        )
        service.save_agent(
            CloudAgentInput(
                **{**data, "enabled": True, "expected_version": agent.version if agent else 0}
            )
        )
    return QuickResult(created=service.create(signal, key), sources=sources, interpretation=method)
