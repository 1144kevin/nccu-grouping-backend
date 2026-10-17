"""Personal invitation copy derived only from validated facts, not invented claims."""

from app.stealth.schemas import Agent, Proposal, Signal, SignalKind

CATEGORY_LABELS = {
    "pizza": "披薩",
    "meal": "餐點",
    "drink": "飲料",
    "bakery": "麵包",
    "snack": "點心",
}
LOCATION_LABELS = {"east_gate": "東側門", "main_gate": "正門", "south_gate": "南門"}


def invitation_copy(proposal: Proposal, agent: Agent, own_signal: Signal) -> tuple[str, list[str]]:
    why = [
        f"你確認可在{LOCATION_LABELS[proposal.location_id]}取餐或合作",
        "方案時間落在你提供的可用區間內",
    ]
    if own_signal.kind == SignalKind.NEED:
        why.append(f"你有{CATEGORY_LABELS[own_signal.category]}需求")
    elif own_signal.kind == SignalKind.OFFER:
        why.append("你表示願意提供資源，方案仍需確認資源有效性")
    else:
        why.append("你明確表示願意在這段行程順路取餐")
    if own_signal.budget_max_twd is not None:
        why.append(f"方案確認費用時不得超過你的 {own_signal.budget_max_twd} 元上限")
    intro = {
        "direct": "有一個符合你已提供條件的合作提案。",
        "warm": "幫你留意到一個可能適合的合作機會。",
        "playful": "偷偷發現一個能一起完成的小計畫！",
    }[agent.tone]
    message = (
        f"{intro}目前共 {len(proposal.members)} 人，"
        f"主題是{CATEGORY_LABELS[proposal.category]}。看過條件後再決定，拒絕也沒關係。"
    )
    return message, why
