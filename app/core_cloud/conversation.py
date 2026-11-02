"""Guided chat: one grounded question per turn, with deterministic quick replies."""

import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.core_cloud.grounding import denied_roles, personal_role_evidence, supported_roles
from app.core_cloud.intake import (
    IntakeFacts,
    IntakeQuestion,
    IntakeRequest,
    ground,
    retain_supported_facts,
    review,
)
from app.core_cloud.quick_stealth import keyword_facts, latest_brand, prepare
from app.core_cloud.stealth import CloudSignalInput
from app.stealth.schemas import StrictModel

QuestionField = Literal[
    "category",
    "location_id",
    "delay_minutes",
    "duration_minutes",
    "budget_max_twd",
    "roles",
    "cost_basis",
    "known_cost_twd",
]


class ConversationPreferences(StrictModel):
    tone: Literal["warm", "direct", "playful"] = "warm"
    context: str = Field(default="", max_length=500)

    @field_validator("context")
    @classmethod
    def valid_context(cls, text):
        if "\0" in text:
            raise ValueError("invalid preference context")
        text.encode("utf-8")
        return text.strip()


class ConversationInput(IntakeRequest):
    turns: list[str] = Field(min_length=1, max_length=16)
    answer_fields: list[QuestionField | None] = Field(default_factory=list, max_length=16)
    assistant_turns: list[str] = Field(default_factory=list, max_length=15)
    preferences: ConversationPreferences | None = None

    @model_validator(mode="after")
    def aligned_answers(self):
        if self.answer_fields and len(self.answer_fields) != len(self.turns):
            raise ValueError("answer context must align with user turns")
        if self.assistant_turns and len(self.assistant_turns) != len(self.turns) - 1:
            raise ValueError("assistant context must align with user turns")
        if any(not t.strip() or len(t) > 600 or "\0" in t for t in self.assistant_turns):
            raise ValueError("invalid assistant context")
        if sum(map(len, self.assistant_turns)) > 8000:
            raise ValueError("assistant context too long")
        return self


class ChatChoice(StrictModel):
    label: str
    answer: str


class ConversationResult(StrictModel):
    facts: IntakeFacts
    signal: CloudSignalInput | None
    sources: list[str]
    message: str
    question: IntakeQuestion | None
    choices: list[ChatChoice]
    ready_for_review: bool
    interpretation: Literal["local_model", "quick_answer", "keyword_fallback", "gemini"]


class NaturalTurn(StrictModel):
    message: str = Field(min_length=1, max_length=600)
    question_field: QuestionField | None = None
    facts: IntakeFacts

    @field_validator("message")
    @classmethod
    def draft_only(cls, text):
        if (
            not text.strip()
            or "\0" in text
            or re.search(
                r"已(?:經)?(?:幫你|為你)?(?:完成)?(?:配對|成團|下單|付款|加入拼單|儲存需求)", text
            )
        ):
            raise ValueError("dialogue must not claim a completed action")
        text.encode("utf-8")
        return text.strip()


OPTIONS = {
    "category": [
        ("喝飲料", "改成飲料，想找人一起喝"),
        ("吃正餐", "改成正餐，想找人一起吃"),
        ("吃披薩", "改成披薩，想找人一起吃"),
        ("麵包／點心", "改成麵包，想找人一起吃"),
    ],
    "location_id": [
        ("政大正門", "取餐地點在政大正門"),
        ("東側門", "取餐地點在政大東側門"),
        ("南門", "取餐地點在政大南門"),
    ],
    "delay_minutes": [
        ("現在就可以", "從現在0分鐘後開始"),
        ("半小時後", "從現在30分鐘後開始"),
        ("一小時後", "從現在60分鐘後開始"),
    ],
    "duration_minutes": [
        ("半小時", "可以配合30分鐘"),
        ("一小時", "可以配合60分鐘"),
        ("先找兩小時", "可以配合120分鐘"),
    ],
    "budget_max_twd": [
        ("80 元內", "我的預算上限是80元"),
        ("120 元內", "我的預算上限是120元"),
        ("還不確定", "我的預算未知"),
    ],
    "roles": [
        ("我可以下單", "我願意下單"),
        ("我可以取餐", "我願意取餐"),
        ("我有優惠券", "我願意提供優惠券"),
        ("先聊聊再分工", "我暫不負責任何分工"),
    ],
    "cost_basis": [("費用還沒確認", "費用未知")],
    "known_cost_twd": [("重新確認費用", "費用未知")],
}
QUESTIONS = {
    "category": "我可以幫你找一起吃喝、分享優惠或順路取餐的夥伴。你現在想吃或喝什麼？",
    "location_id": "在哪裡碰面或取餐比較方便？店名、地址或地標都可以。",
    "delay_minutes": "你想從什麼時候開始？可以說「現在」或「半小時後」。",
    "duration_minutes": "開始之後，大概能配合多久？還沒決定的話，也可以先找兩小時。",
    "budget_max_twd": "每人預算大概多少？還沒決定也沒關係，我會保留待討論。",
    "roles": "你有願意幫忙的部分嗎？也可以先聊聊，再一起決定分工。",
    "cost_basis": "你剛才提到的金額是每人的費用，還是整團總價？尚未確認也可以告訴我。",
    "known_cost_twd": "目前的每人費用超過預算了。你想調整預算，還是重新確認費用？",
}
STARTERS = ("我想找人一起喝飲料", "我有優惠券想分享", "我願意順路幫忙取餐")


def contextual_turn(turn, field):
    """Resolve only unambiguous short answers to the question actually shown."""
    answer = turn.strip()
    if field == "budget_max_twd" and re.fullmatch(r"\d{1,6}(?:元|塊)?", answer):
        if int(answer.rstrip("元塊")) > 100000:
            from fastapi import HTTPException

            raise HTTPException(422, "invalid_stealth_money")
        return "我的預算上限是" + answer.rstrip("元塊") + "元"
    if field in ("cost_basis", "known_cost_twd") and re.fullmatch(r"\d{1,6}(?:元|塊)?", answer):
        return "每人費用是" + answer.rstrip("元塊") + "元"
    if field == "budget_max_twd" and answer in ("不知道", "還不知道", "不確定", "還沒決定"):
        return "我的預算未知"
    if field == "delay_minutes" and answer in ("現在", "馬上", "現在就可以"):
        return "從現在0分鐘後開始"
    if field == "duration_minutes" and re.fullmatch(
        r"[一二兩三四五六七八九十半個\d]+(?:分鐘|小時)", answer
    ):
        return "可以配合" + answer
    return answer


def quick_answer(value, turns):
    """Exact button answers need no inference; previous claims are re-grounded."""
    latest = turns[-1]
    field = value.answer_fields[-1] if value.answer_fields else None
    if len(turns) == 1 and latest in STARTERS:
        facts = keyword_facts(turns)
        if facts.roles:
            facts.evidence.roles = latest
        ground(facts, latest)
        return facts
    if not value.previous or not field:
        return None
    allowed = {answer for _, answer in OPTIONS[field]}
    numeric_budget = field == "budget_max_twd" and re.fullmatch(r"我的預算上限是\d{1,6}元", latest)
    if latest not in allowed and not numeric_budget:
        return None
    facts = value.previous.model_copy(deep=True)
    prior = "\n".join(turns[:-1])
    ground(facts, prior)
    # A client snapshot cannot introduce a food category absent from the transcript.
    supported = keyword_facts(turns[:-1]).category
    if facts.category and facts.category != ("drink" if latest_brand(prior) else supported):
        return None
    if field == "category":
        facts.category = keyword_facts([latest]).category
        facts.acceptable_categories = []
        activity = {
            "drink": "喝飲料",
            "meal": "吃正餐",
            "pizza": "吃披薩",
            "bakery": "吃麵包",
            "snack": "吃點心",
        }.get(facts.category, "吃喝")
        prefix = (
            "有優惠想分享，"
            if "coupon" in facts.roles
            else "有餐點想分享，"
            if facts.kind == "offer"
            else "願意順路取餐，"
            if facts.kind == "plan"
            else "想找人一起"
        )
        facts.title = prefix + activity
    elif field == "location_id":
        facts.location_id = {
            "取餐地點在政大正門": "main_gate",
            "取餐地點在政大東側門": "east_gate",
            "取餐地點在政大南門": "south_gate",
        }[latest]
    elif field == "delay_minutes":
        facts.delay_minutes = int(re.search(r"\d+", latest).group())
        facts.evidence.start = latest
    elif field == "duration_minutes":
        facts.duration_minutes = int(re.search(r"\d+", latest).group())
        facts.evidence.duration = latest
    elif field == "budget_max_twd":
        facts.budget_unknown = "未知" in latest
        facts.budget_max_twd = (
            None if facts.budget_unknown else int(re.search(r"\d+", latest).group())
        )
        facts.evidence.budget = latest
    elif field == "roles":
        facts.roles = {
            "我願意下單": ["order"],
            "我願意取餐": ["pickup"],
            "我願意提供優惠券": ["coupon"],
            "我暫不負責任何分工": [],
        }[latest]
        facts.roles_confirmed = True
        facts.evidence.roles = latest
    else:
        facts.known_cost_twd = None
        facts.cost_needs_clarification = False
        facts.evidence.cost = latest
    ground(facts, "\n".join(turns))
    return facts


def converse(
    service, value: ConversationInput, *, interpreter=None, dialogue=None, now=None, on_message=None
):
    fields = value.answer_fields or [None] * len(value.turns)
    turns = [contextual_turn(turn, field) for turn, field in zip(value.turns, fields, strict=True)]
    prepared = value.model_copy(update={"turns": turns})
    natural = dialogue.respond(prepared) if dialogue else None
    facts = natural.facts if natural else quick_answer(value, turns)
    method = "quick_answer"
    if natural:
        method = "gemini"
        original = "\n".join(turns)
        ground(facts, original)
        if value.previous is not None and len(turns) > 1:
            facts = retain_supported_facts(facts, value.previous, turns)
            ground(facts, original)
        facts.tags = [tag for tag in facts.tags if tag in original]
        facts.dietary_requirements = [tag for tag in facts.dietary_requirements if tag in original]
    if facts is None:
        if interpreter is None:
            # Preserve the existing safe one-turn fallback, never erase follow-up corrections.
            if len(turns) > 1:
                from fastapi import HTTPException

                raise HTTPException(503, "quick_interview_unavailable")
            facts, method = keyword_facts(turns), "keyword_fallback"
        else:
            facts = interpreter.interpret(value.model_copy(update={"turns": turns})).facts
            method = "local_model"
    original = "\n".join(turns)
    revoked = reconcile_role_preferences(facts, turns)
    if (
        natural
        and revoked
        and (
            natural.question_field == "roles"
            or (
                "pickup" in revoked
                and re.search(r"願意|可以|能否|是否|要不要|能不能", natural.message)
                and re.search(r"取餐|拿餐|領餐|跑腿", natural.message)
                and "pickup" not in denied_roles(natural.message)
            )
        )
    ):
        natural.question_field = None
        natural.message = (
            "了解，不需要你去取餐，我會以這個條件繼續整理，其他已說明的需求維持不變。"
            if "pickup" in revoked
            else "了解，分工以你最新的意願為準，其他已說明的需求維持不變。"
        )
    # A budget-only statement is not an ambiguous quotation from a merchant.
    if (
        facts.cost_needs_clarification
        and re.search(r"預算|上限|最多", original)
        and not re.search(
            r"費用|價格|報價|總價|整團|合計|總共|每人\s*\d|一人\s*\d|一杯\s*\d|一份\s*\d", original
        )
    ):
        facts.cost_needs_clarification = False
    if latest_brand(original):
        facts.category = "drink"
    all_questions = {q.field: q for q in review(facts, "guided").questions}
    priority = [
        "category",
        "cost_basis",
        "known_cost_twd",
        "location_id",
        "delay_minutes",
        "duration_minutes",
        "budget_max_twd",
        "roles",
    ]
    role_missing = (facts.kind == "offer" and not facts.roles) or (
        facts.kind == "plan" and "pickup" not in facts.roles
    )
    window_invalid = (facts.delay_minutes or 0) + (facts.duration_minutes or 1440) > 10080
    if role_missing:
        priority.insert(0, "roles")
    if window_invalid:
        priority.insert(0, "delay_minutes")
        all_questions["delay_minutes"] = IntakeQuestion(
            field="delay_minutes", text="開始加上配合長度超過七天了，請改成七天內的時間。"
        )
    next_field = next((f for f in priority if f in all_questions), None)
    # The model chooses the conversational direction, but may not skip unresolved
    # cost/role conflicts or treat its own previous replies as user commitments.
    essential = (
        "delay_minutes"
        if window_invalid
        else "roles"
        if role_missing
        else "cost_basis"
        if facts.cost_needs_clarification
        else "known_cost_twd"
        if "known_cost_twd" in all_questions
        else None
    )
    natural_allowed = natural is not None and (
        essential is None or natural.question_field == essential
    )
    if natural_allowed:
        next_field = natural.question_field
    question = None
    if next_field:
        question = IntakeQuestion(field=next_field, text=QUESTIONS[next_field])
        if window_invalid or (role_missing and next_field == "roles"):
            question = all_questions[next_field]
    signal, sources = None, []
    over_budget = (
        facts.known_cost_twd is not None
        and facts.budget_max_twd is not None
        and facts.known_cost_twd > facts.budget_max_twd
    )
    if (
        facts.category
        and not role_missing
        and not window_invalid
        and not facts.cost_needs_clarification
        and not over_budget
    ):
        # A new guided chat never silently inherits an old request's budget or location.
        signal, sources, _ = prepare(facts.title, [], now=now, facts=facts, original=original)
    choices = [
        ChatChoice(label=label, answer=answer) for label, answer in OPTIONS.get(next_field, [])
    ]
    if role_missing:
        choices = [c for c in choices if c.answer != "我暫不負責任何分工"]
        if facts.kind == "plan":
            choices = [c for c in choices if c.answer == "我願意取餐"]
    ready = signal is not None and question is None
    message = question.text if question else "需求整理好了！看看下方條件，確認後我就幫你找夥伴。"
    if natural_allowed:
        message = natural.message
        if question is not None:
            question.text = message
    elif len(turns) > 1 and question:
        message = "好，我更新好了。" + message
    return ConversationResult(
        facts=facts,
        signal=signal,
        sources=sources,
        message=message,
        question=question,
        choices=choices,
        ready_for_review=ready,
        interpretation=method,
    )


def reconcile_role_preferences(facts: IntakeFacts, turns: list[str]) -> set[str]:
    """Replay explicit personal role choices in order; the latest consent wins.

    This does not infer consent from an assistant suggestion, a third party or an
    old client snapshot. In particular, withdrawing pickup also withdraws plan kind.
    """
    original = "\n".join(turns)
    roles: set[str] = set()
    last_explicit: set[str] = set()
    proof = None
    revoked: set[str] = set()
    for index, turn in enumerate(turns):
        for clause in re.split(r"[，。；\n]|但是|不過|但", turn):
            clause = clause.strip()
            if not clause or not personal_role_evidence(clause, original):
                continue
            denied = denied_roles(clause)
            if re.search(
                r"我.{0,4}(?:不想|不願意|不要|無法|不能).{0,4}(?:出門|跑腿)|我.{0,4}(?:待|留)在宿舍",
                clause,
            ):
                denied.add("pickup")
            if (
                re.fullmatch(
                    r"(?:我)?(?:其實|現在|改成)?(?:不願意|不想|不要)(?:了|啦|喔|！|!)?", clause
                )
                and len(last_explicit) == 1
            ):
                denied |= last_explicit
            cleared = bool(
                re.search(r"暫不負責|沒有任何角色|沒有角色|不負責任何|取消所有分工", clause)
            )
            granted = (
                supported_roles(clause) - denied
                if re.search(
                    r"我(?:手上)?有|手上有|我願意|我可以|我負責|我會幫|我能幫|^願意|^可以", clause
                )
                else set()
            )
            if not denied and not granted and not cleared:
                continue
            if cleared:
                denied |= roles
                roles.clear()
            roles -= denied
            roles |= granted
            if granted:
                last_explicit = granted
            proof = clause[:160]
            if index == len(turns) - 1:
                revoked |= denied
                revoked -= granted
    if proof is not None:
        facts.roles = sorted(roles)
        facts.roles_confirmed = True
        facts.evidence.roles = proof
        if (facts.kind == "plan" and "pickup" not in roles) or (
            facts.kind == "offer" and not roles
        ):
            facts.kind = "need"
    return revoked
