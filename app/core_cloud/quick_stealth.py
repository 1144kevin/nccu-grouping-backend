"""One-sentence requests. Defaults are proposals, never invented prices or consent."""

import re
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.core_cloud.grounding import denied_roles, personal_role_evidence, supported_roles
from app.core_cloud.intake import IntakeFacts, IntakeQuestion, IntakeRequest, review
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
    confirmed_signal: CloudSignalInput | None = None

    @field_validator("text")
    @classmethod
    def safe_text(cls, value):
        if not value.strip() or "\0" in value:
            raise ValueError("invalid text")
        value.encode("utf-8")
        return value.strip()

    @model_validator(mode="after")
    def consistent_confirmation(self):
        if self.confirmed_signal and self.confirmed_signal.title != self.text:
            raise ValueError("confirmed title mismatch")
        return self


class QuickResult(StrictModel):
    created: CloudSignalCreated
    sources: list[str]
    interpretation: Literal["local_model", "keyword_fallback", "reviewed", "retry"]


class QuickInterview(StrictModel):
    facts: IntakeFacts
    signal: CloudSignalInput | None
    sources: list[str]
    questions: list[IntakeQuestion]
    interpretation: Literal["local_model", "keyword_fallback"]


def latest_brand(text):
    found = {}
    resets = list(
        re.finditer(
            r"不限品牌|不限定品牌|品牌不限|品牌都可以|哪家都可以|"
            r"(?:改成|改為|改吃|換成|換吃)\s*(?:吃|買)?\s*(?:披薩|便當|正餐|麵包|零食|pizza)",
            text,
            re.IGNORECASE,
        )
    )
    cutoff = resets[-1].end() if resets else -1
    for brand, aliases in BRANDS.items():
        for alias in aliases:
            for match in re.finditer(re.escape(alias), text):
                if match.start() < cutoff:
                    continue
                before = re.split(r"[，。；\n]", text[: match.start()])[-1]
                denied = bool(
                    re.search(
                        r"(?:不想|不要|不是|不喝|取消|不選|不)\s*(?:喝|買|要|選)?\s*$", before
                    )
                )
                if brand not in found or found[brand][0] <= match.start():
                    found[brand] = (match.start(), denied)
    allowed = [(position, brand) for brand, (position, denied) in found.items() if not denied]
    return max(allowed)[1] if allowed else None


def keyword_facts(turns):
    facts = IntakeFacts(title=turns[0][:160])
    original = "\n".join(turns)
    roles = set()
    for turn in turns:
        hint = OfflineDraftProvider().draft(turn)
        if hint.category_hint:
            facts.category = hint.category_hint
        if latest_brand(turn):
            facts.category = "drink"
        if hint.location_hint:
            facts.location_id = hint.location_hint
        for clause in re.split(r"[，。；\n]", turn):
            if not personal_role_evidence(clause, original):
                continue
            roles -= denied_roles(clause)
            if re.search(r"我(?:手上)?有|手上有|我願意|我可以|我負責|我會幫|我能幫", clause):
                roles |= supported_roles(clause) - denied_roles(clause)
    facts.roles = sorted(roles)
    facts.roles_confirmed = bool(roles)
    facts.kind = (
        "plan"
        if "pickup" in roles and re.search(r"順路|路過|經過", original)
        else "offer"
        if "coupon" in roles
        else "need"
    )
    return facts


def interview(service, value: IntakeRequest, *, interpreter=None, now=None):
    # No agent activation or writes. A follow-up failure must not silently erase corrections.
    if interpreter:
        facts = interpreter.interpret(value).facts
        method = "local_model"
    else:
        from fastapi import HTTPException

        if len(value.turns) > 1:
            raise HTTPException(503, "quick_interview_unavailable")
        facts = keyword_facts(value.turns)
        method = "keyword_fallback"
    history = service.signals().items
    original = "\n".join(value.turns)
    if latest_brand(original):
        # Explicit supported drink brands outrank a copied model example's category.
        facts = facts.model_copy(update={"category": "drink"})
    signal = None
    sources = []
    role_missing = (facts.kind == "offer" and not facts.roles) or (
        facts.kind == "plan" and "pickup" not in facts.roles
    )
    window_invalid = (facts.delay_minutes or 0) + (facts.duration_minutes or 120) > 10080
    if (facts.category or latest_brand(original)) and not role_missing and not window_invalid:
        signal, sources, _ = prepare(facts.title, history, now=now, facts=facts, original=original)
    questions = [
        q
        for q in review(facts, "quick").questions
        if q.field in ("category", "location_id", "delay_minutes", "budget_max_twd")
        or (q.field == "roles" and role_missing)
    ]
    if window_invalid:
        questions = [q for q in questions if q.field != "delay_minutes"]
        questions.insert(
            0,
            IntakeQuestion(
                field="delay_minutes", text="開始時間加上配合長度需要在未來七天內，請縮短時間。"
            ),
        )
    questions.sort(
        key=lambda q: (
            0
            if q.field == "category"
            or (q.field == "roles" and role_missing)
            or (q.field == "delay_minutes" and window_invalid)
            else 1
        )
    )
    questions = questions[:3]
    return QuickInterview(
        facts=facts, signal=signal, sources=sources, questions=questions, interpretation=method
    )


def prepare(text, history, *, interpreter=None, now=None, facts=None, original=None):
    now = now or datetime.now(UTC)
    # Only the caller's recent requests. Past roles and past prices NEVER authorize new duties.
    recent = sorted(
        [s for s in history if s.created_at > now - timedelta(days=30) and s.status != "revoked"],
        key=lambda s: s.created_at,
        reverse=True,
    )
    hint = OfflineDraftProvider().draft(text)
    supplied = facts is not None
    facts = facts or keyword_facts([text])
    method = "keyword_fallback"
    if interpreter and not supplied:
        try:
            facts = interpreter.interpret(IntakeRequest(turns=[text])).facts
            method = "local_model"
        except LocalModelError:
            pass
    brand = latest_brand(original or text)
    category = "drink" if brand else facts.category or hint.category_hint
    if category != "drink":
        brand = None
    if category is None:
        from fastapi import HTTPException

        raise HTTPException(422, "quick_category_unclear")
    previous = next((s for s in recent if s.category == category), None)
    location = (
        facts.location_id
        or (hint.location_hint if not supplied else None)
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
    base_tags = [tag for tag in facts.tags if not tag.startswith("品牌:")] or [category]
    tags = list(dict.fromkeys([*(["品牌:" + brand] if brand else []), *base_tags]))[:12]
    value = CloudSignalInput(
        kind=(
            facts.kind
            if (facts.kind == "offer" and facts.roles)
            or (facts.kind == "plan" and "pickup" in facts.roles)
            else "need"
        ),
        category=category,
        title=text.strip(),
        location_id=location,
        acceptable_categories=[] if facts.kind == "offer" else facts.acceptable_categories,
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
            if facts.location_id or (hint.location_hint and not supplied)
            else "沿用近30天同類需求"
            if previous
            else "首次建議政大正門，可在句子中更改"
        ),
        "時間："
        + (
            "依這次描述"
            if facts.delay_minutes is not None and facts.duration_minutes is not None
            else "開始依這次描述，未指定配合長度，先找兩小時"
            if facts.delay_minutes is not None
            else "未指定開始時間，先從現在找起；配合長度依這次描述"
            if facts.duration_minutes is not None
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
        if saved["title"] != value.text or (
            value.confirmed_signal is not None
            and CloudSignalInput.model_validate(
                {field: saved[field] for field in CloudSignalInput.model_fields}
            ).model_dump()
            != value.confirmed_signal.model_dump()
        ):
            raise HTTPException(409, "idempotency_payload_conflict")
        return QuickResult(
            created=CloudSignalCreated(signal=saved, matching="completed"),
            sources=["已恢復上次送出的需求，未重複建立。"],
            interpretation="retry",
        )
    agent = service.agent()
    if not (agent and agent.enabled) and not value.activate_agent:
        raise HTTPException(409, "agent_not_enabled")
    if value.confirmed_signal is not None:
        signal, sources, method = (
            value.confirmed_signal,
            ["已採用本人確認的聊天整理結果。"],
            "reviewed",
        )
    else:
        signal, sources, method = prepare(
            value.text, service.signals().items, interpreter=interpreter
        )
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
