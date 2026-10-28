"""Check synthetic Gemini dialogue without accounts, database writes, or key output."""

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    from dotenv import dotenv_values
    from fastapi import HTTPException

    from app.core.config import Settings
    from app.core_cloud.conversation import ConversationInput, converse
    from app.core_cloud.gemini_conversation import GeminiDialogue

    config = dotenv_values(ROOT / ".local" / "gemini.env")
    for name in ("STEALTH_CONVERSATION_PROVIDER", "STEALTH_GEMINI_API_KEY", "STEALTH_GEMINI_MODEL"):
        if config.get(name):
            os.environ[name] = config[name]
    settings = Settings(_env_file=None)
    try:
        provider = GeminiDialogue(settings.stealth_gemini_api_key, "synthetic-connection-check")
        turns, assistant, fields, previous = [], [], [], None
        next_field = None
        for text in (
            "想喝50嵐，但懶得出門，可以多付10塊跑腿費。",
            "我在政大東側門，現在開始找人，飲料加跑腿費預算上限80元。",
            "可以配合半小時；我只想分食，不負責下單或取餐。",
        ):
            turns.append(text)
            fields.append(next_field)
            started = time.monotonic()

            result = converse(
                None,
                ConversationInput(
                    turns=turns, assistant_turns=assistant, answer_fields=fields, previous=previous
                ),
                dialogue=provider,
            )
            print(f"Turn {len(turns)}: {time.monotonic() - started:.2f}s / {result.interpretation}")
            print(result.message)
            print(
                f"Budget: {result.facts.budget_max_twd}; actual cost: {result.facts.known_cost_twd}"
            )
            print(f"Roles: {result.facts.roles}; review: {result.ready_for_review}")
            print(f"Usage: {provider.last_usage}")
            assistant.append(result.message)
            next_field = result.question.field if result.question else None
            previous = result.facts
        print("Synthetic conversation completed. No signals, posts or invitations were written.")
        return 0
    except HTTPException as error:
        print(f"Check failed: HTTP {error.status_code} / {error.detail}")
        return 1
    except Exception as error:
        print(f"Check failed: {type(error).__name__}. No configuration contents displayed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
