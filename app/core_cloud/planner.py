"""Deterministic, free proposal planner. No model, network, private prose, or consent writes."""

from itertools import product
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field


class PlanningMember(BaseModel):
    number: int = Field(ge=1, le=3)
    roles: list[Literal["order", "pickup", "coupon", "share"]]


class RoleSuggestion(BaseModel):
    role: Literal["order", "pickup"]
    member_number: int | None
    candidates: list[int]
    reason: str


class AgentTermsDraft(BaseModel):
    provider: Literal["offline_rules_v1", "openai_summary_v1"] = "offline_rules_v1"
    is_llm: bool = False
    content: str
    cost_per_person_twd: int | None
    total_cost_twd: int | None
    cost_basis: Literal["known_cost", "unknown"]
    budget_limit_twd: int | None
    starts_at: AwareDatetime | None = None
    deadline_at: AwareDatetime
    location_label: str = ""
    order_member: int | None
    pickup_member: int | None
    role_suggestions: list[RoleSuggestion]
    missing_fields: list[Literal["cost", "order", "pickup"]]
    confirmed_facts: list[str] = Field(default_factory=list)
    recommendation_reasons: list[str] = Field(default_factory=list)
    needs_confirmation: list[str]
    ready_to_submit: bool


def plan_terms(
    *,
    category: str,
    location: str,
    cost: int | None,
    budget: int | None,
    ends_at: AwareDatetime,
    member_count: int,
    members: list[PlanningMember],
    unknowns: list[str],
    starts_at: AwareDatetime | None = None,
    reasons: list[str] | None = None,
) -> AgentTermsDraft:
    """Same snapshot yields the same proposal for every participant. Unknown != zero."""
    # Fail closed if a partial/malformed context is supplied; never guess other volunteers.
    facts_valid = len(members) == member_count and {m.number for m in members} == set(
        range(1, member_count + 1)
    )
    order = sorted(m.number for m in members if facts_valid and "order" in m.roles)
    pickup = sorted(m.number for m in members if facts_valid and "pickup" in m.roles)
    chosen_order: int | None = order[0] if order else None
    chosen_pickup: int | None = pickup[0] if pickup else None
    if order and pickup:
        # Prefer sharing work when volunteers allow it; ordinal tie-break stays stable.
        chosen_order, chosen_pickup = min(
            product(order, pickup), key=lambda pair: (pair[0] == pair[1], *pair)
        )
    suggestions = []
    missing = []
    for role, candidates, chosen, label in (
        ("order", order, chosen_order, "下單"),
        ("pickup", pickup, chosen_pickup, "取餐"),
    ):
        if chosen is None:
            missing.append(role)
            reason = f"目前沒有人明確表示願意{label}，請協調後由本人確認。"
        else:
            reason = f"成員 {chosen} 已在需求中表示願意{label}，仍需確認本次分工。"
            if chosen_order == chosen_pickup:
                reason += "目前建議由同一人兼任，請確認能否負擔。"
        suggestions.append(
            RoleSuggestion(role=role, member_number=chosen, candidates=candidates, reason=reason)
        )
    # Never quote a budget as a price, or deduct an unverified coupon.
    if cost is None:
        missing.insert(0, "cost")
    category_label = {
        "pizza": "披薩",
        "meal": "正餐",
        "drink": "飲料",
        "bakery": "麵包",
        "snack": "零食",
    }.get(category, "餐點")
    place = {"main_gate": "政大正門", "east_gate": "政大東側門", "south_gate": "政大南門"}.get(
        location, location
    )
    # Keep editable money in structured fields: a changed amount must not leave a stale
    # original quote embedded in the generated public description.
    content = (
        f"一起分享{category_label}，在{place}協調集合。"
        "每人費用、下單與取餐依全員最後確認的方案處理。"
    )
    confirmations = list(
        dict.fromkeys(
            [
                *unknowns,
                "已知價格仍需確認是否包含本次所有費用；未驗證優惠不另行折抵。",
                "草案不代表任何人同意；請逐人確認費用、分工、公開內容與截止時間。",
                "截止暫用共同配合區間結束，可在區間內調整；不會代為訂餐或付款。",
                "分工僅為建議，送出前可修改；新增承諾需由本人確認。",
            ]
        )
    )
    confirmed_facts = [
        f"需求品類：{category_label}",
        f"共同地點：{place}",
    ]
    if starts_at is not None:
        confirmed_facts.append("已確認雙方有重疊的可配合時段")
    if cost is not None:
        confirmed_facts.append(f"已提供每人費用：NT$ {cost}")
    elif budget is not None:
        confirmed_facts.append(f"已提供預算上限：NT$ {budget}（僅作預算參考，非實際費用）")
    if facts_valid:
        for member in members:
            if "order" in member.roles:
                confirmed_facts.append(f"夥伴 {member.number} 表示願意負責購買／下單")
            if "pickup" in member.roles:
                confirmed_facts.append(f"夥伴 {member.number} 表示願意負責取餐")
            if "coupon" in member.roles:
                confirmed_facts.append(f"夥伴 {member.number} 表示手上有優惠券可分享")

    ignored = {
        "取餐地點一致",
        "可用時間有交集",
        "餐點分類符合本人確認的選項",
        "需求內容的語義相近，細節仍需雙方確認",
        "透過需求向量搜尋找到，仍須本人確認",
        "方案預算上限採所有已提供上限中的最低值",
        "透過本人明確接受的替代類型形成互補方案",
    }
    recommendation_reasons = [
        r
        for r in dict.fromkeys(reasons or [])
        if r and r not in ignored and "向量搜尋" not in r and "語義相近" not in r
    ][:4]

    return AgentTermsDraft(
        content=content,
        cost_per_person_twd=cost,
        total_cost_twd=None if cost is None else cost * member_count,
        cost_basis="unknown" if cost is None else "known_cost",
        budget_limit_twd=budget,
        starts_at=starts_at,
        deadline_at=ends_at,
        location_label=place,
        order_member=chosen_order,
        pickup_member=chosen_pickup,
        role_suggestions=suggestions,
        missing_fields=missing,
        confirmed_facts=confirmed_facts,
        recommendation_reasons=recommendation_reasons,
        needs_confirmation=confirmations,
        ready_to_submit=not missing,
    )
