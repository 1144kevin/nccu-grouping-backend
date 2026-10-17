"""Offline-only AI boundary. No model downloads, network calls, or tool execution."""

from typing import Protocol

from app.stealth.schemas import DraftOutput


class DraftProvider(Protocol):
    def draft(self, text: str) -> DraftOutput: ...


class OfflineDraftProvider:
    """Transparent test provider; keyword hints are not advertised as semantic AI."""

    def draft(self, text: str) -> DraftOutput:
        category = None
        for keywords, value in (
            (("披薩", "pizza"), "pizza"),
            (("飲料", "奶茶"), "drink"),
            (("麵包", "可頌"), "bakery"),
            (("便當", "晚餐", "午餐"), "meal"),
            (("零食", "餅乾"), "snack"),
        ):
            if any(word in text.lower() for word in keywords):
                category = value
                break
        location = None
        for word, value in (("東側門", "east_gate"), ("正門", "main_gate"), ("南門", "south_gate")):
            if word in text:
                location = value
                break
        return DraftOutput(
            category_hint=category,
            location_hint=location,
            title=text[:160],
            needs_confirmation=[
                "這是離線關鍵字草稿，不是 LLM 理解結果",
                "請確認訊號類型、分類與取餐地點",
                "請明確填寫時間區間、有效期限與預算",
                "請確認飲食限制與願意貢獻的角色",
            ],
        )
