"""Bounded, single-shot AI summary helper for tentative match drafts with grounded fallback."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.core_cloud.openai_budget import configured_budget
from app.core_cloud.openai_provider import TEXT_MODEL, OpenAITransport
from app.core_cloud.planner import AgentTermsDraft
from app.core_cloud.stealth import CloudMatch
from app.stealth.ollama import LocalModelError


class AiDraftPolish(BaseModel):
    content: str = Field(min_length=10, max_length=240)
    recommendation_reasons: list[str] = Field(default_factory=list, max_length=4)
    needs_confirmation: list[str] = Field(default_factory=list, max_length=5)


class AiDraftResponse(BaseModel):
    draft: AgentTermsDraft
    fallback: bool = False
    notice: str = ""


_FALLBACK_NOTICES = {
    "openai_not_configured": "目前未啟用雲端 AI 潤飾，已改用系統規則整理暫定方案，仍可直接查看或手動修改。",
    "openai_budget_exceeded": "AI 額度已達上限，已改用系統規則整理暫定方案，仍可直接查看或手動修改。",
    "openai_rate_limit": "AI 請求過於頻繁，已改用系統規則整理暫定方案，仍可直接查看或手動修改。",
    "openai_quota_or_rate_limit": "AI 額度或速率受限，已改用系統規則整理暫定方案，仍可直接查看或手動修改。",
    "openai_timeout": "AI 回應逾時，已改用系統規則整理暫定方案，仍可直接查看或手動修改。",
}


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.S)
    if fence:
        cleaned = fence.group(1)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        cleaned = cleaned[start : end + 1]
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("invalid_json_object")
    return data


def _extract_response_text(raw: dict[str, Any]) -> str:
    if isinstance(raw.get("output_text"), str) and raw["output_text"].strip():
        return raw["output_text"]
    for item in raw.get("output", []) if isinstance(raw.get("output"), list) else []:
        if not isinstance(item, dict):
            continue
        for part in item.get("content", []) if isinstance(item.get("content"), list) else []:
            if isinstance(part, dict) and isinstance(part.get("text"), str) and part["text"].strip():
                return part["text"]
    raise ValueError("empty_ai_response")


def polish_match_draft(
    match: CloudMatch,
    *,
    settings: Any,
    transport: OpenAITransport | None = None,
) -> AiDraftResponse:
    """Single-shot, budget-bounded wording refinement. Never invents price, role, time, or place."""
    base = match.agent_draft
    if base is None:
        raise ValueError("draft_unavailable")

    if getattr(settings, "stealth_ai_provider", "") != "openai" or not (
        getattr(settings, "environment", "") == "local"
        or getattr(settings, "core_cloud_enabled", False)
    ):
        return AiDraftResponse(
            draft=base,
            fallback=True,
            notice=_FALLBACK_NOTICES["openai_not_configured"],
        )

    try:
        client = transport or OpenAITransport(
            settings.local_openai_api_key, budget=configured_budget(settings)
        )
        # Only sanitized structured fields enter the prompt; never raw private chat prose.
        facts_payload = {
            "category": match.category,
            "location": base.location_label,
            "known_cost_twd": base.cost_per_person_twd,
            "budget_limit_twd": base.budget_limit_twd,
            "order_member": base.order_member,
            "pickup_member": base.pickup_member,
            "confirmed_facts": base.confirmed_facts[:8],
            "missing_fields": base.missing_fields,
            "recommendation_reasons": base.recommendation_reasons[:4],
            "needs_confirmation": base.needs_confirmation[:5],
        }
        payload = {
            "model": TEXT_MODEL,
            "max_output_tokens": 220,
            "input": [
                {
                    "role": "system",
                    "content": (
                        "你是政大拼單助理。請只根據提供的已確認欄位，用繁體中文簡潔潤飾共同暫定方案說明。"
                        "嚴禁把 budget_limit_twd 當成實際費用，嚴禁猜測或補齊 null 的費用或分工，"
                        "嚴禁虛構優惠、價格、時間或地點。只回傳 JSON："
                        '{"content": string, "recommendation_reasons": string[], "needs_confirmation": string[]}'
                    ),
                },
                {"role": "user", "content": json.dumps(facts_payload, ensure_ascii=False)},
            ],
        }
        raw = client.request("responses", payload, kind="draft", units=500, maximum=12_000)
        parsed = AiDraftPolish.model_validate(_extract_json(_extract_response_text(raw)))
        updated = base.model_copy(
            update={
                "provider": "openai_summary_v1",
                "is_llm": True,
                "content": parsed.content.strip(),
                "recommendation_reasons": [
                    r.strip() for r in parsed.recommendation_reasons if r.strip()
                ][:4]
                or base.recommendation_reasons,
                "needs_confirmation": [
                    n.strip() for n in parsed.needs_confirmation if n.strip()
                ][:5]
                or base.needs_confirmation,
            }
        )
        return AiDraftResponse(draft=updated, fallback=False)
    except LocalModelError as exc:
        code = str(exc)
        return AiDraftResponse(
            draft=base,
            fallback=True,
            notice=_FALLBACK_NOTICES.get(
                code, "雲端 AI 暫時無法使用，已改用系統規則整理暫定方案，仍可直接查看或手動修改。"
            ),
        )
    except (ValidationError, ValueError, KeyError, json.JSONDecodeError):
        return AiDraftResponse(
            draft=base,
            fallback=True,
            notice="雲端 AI 回應格式不符，已保留系統規則整理的暫定方案，仍可直接查看或手動修改。",
        )
