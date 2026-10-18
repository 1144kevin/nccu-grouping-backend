"""Read-only semantic ranking of already-authorized cloud proposal summaries."""

import hashlib
import json

from pydantic import Field

from app.core_cloud.stealth import CloudMatch, CloudStealth
from app.stealth.provider_contracts import CandidateContext
from app.stealth.schemas import StrictModel
from app.stealth.semantic import SemanticMatcher, SemanticRanking


class SemanticRequest(StrictModel):
    review_limit: int = Field(default=3, strict=True, ge=0, le=3)


class SemanticCloudMatches(StrictModel):
    ranking: SemanticRanking
    items: list[CloudMatch]


def snapshot(match: CloudMatch) -> str:
    return hashlib.sha256(
        json.dumps(match.model_dump(mode="json"), sort_keys=True).encode()
    ).hexdigest()


def semantic_cloud_matches(service: CloudStealth, matcher: SemanticMatcher, review_limit=3):
    # SQL remains the privacy/eligibility authority. Never fetch other users' raw signals.
    before = [m for m in service.matches().items if m.status in ("pending", "ready")][:12]
    contexts = []
    for match in before:
        roles = {r for member in match.planning_members for r in member.roles}
        facts = ["location", "time", "category"]
        if "pickup" in roles:
            facts.append("pickup_volunteer")
        if "coupon" in roles:
            facts.append("coupon_terms")
        if match.known_cost_twd is None:
            facts.append("cost_unknown")
        if "order" not in roles:
            facts.append("order_missing")
        if "pickup" not in roles:
            facts.append("pickup_missing")
        labels = {"pickup": "取餐", "order": "下單", "coupon": "提供優惠券", "share": "分食"}
        others = [
            "、".join(labels[r] for r in m.roles)
            for m in match.planning_members
            if m.number != match.my_member_number
        ]
        food = {"pizza": "披薩", "meal": "正餐", "drink": "飲料", "bakery": "麵包", "snack": "點心"}
        document = (
            f"{food.get(match.category, match.category)}；{match.member_count}人；其他成員的志願："
            + "；".join(others)
        )
        contexts.append(CandidateContext(key=str(match.id), document=document, facts=facts))
    query = "\n".join(dict.fromkeys(match.my_signal_title for match in before))
    ranking = matcher.rank(query, contexts, review_limit=review_limit)
    old = {str(match.id): snapshot(match) for match in before}
    # Re-check auth, blocks, expiry and proposal revisions after slow inference.
    fresh = {str(m.id): m for m in service.matches().items if m.status in ("pending", "ready")}
    allowed = {key for key, m in fresh.items() if old.get(key) == snapshot(m)}
    if any(item.key not in allowed for item in ranking.items):
        ranking.warnings.append("配對條件已變更或失效，已移除舊排序；請重新整理")
    ranking.items = [item for item in ranking.items if item.key in allowed]
    return SemanticCloudMatches(ranking=ranking, items=[fresh[item.key] for item in ranking.items])
