"""Synthetic OpenAI smoke tests; never print credentials or user content."""

import argparse
import base64
import io
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from PIL import Image
from pydantic import SecretStr

from app.core_cloud.conversation import ConversationInput
from app.core_cloud.intake import IntakeRequest, interpret
from app.core_cloud.openai_provider import (
    OpenAIDialogue,
    OpenAIEmbeddingProvider,
    OpenAIExtractionProvider,
)
from app.core_cloud.openai_speech import MODEL as SPEECH_MODEL
from app.core_cloud.openai_speech import audio_chunks
from app.core_cloud.post_writing import WritingInput, draft_post


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-billable", action="store_true")
    parser.add_argument("--extended-only", action="store_true")
    args = parser.parse_args()
    if not args.run_billable:
        print("No requests made. Use --run-billable for synthetic API tests.")
        return
    load_dotenv(Path(__file__).resolve().parents[1] / ".local" / "openai-local.env", override=True)
    secret = SecretStr(os.environ.get("OPENAI_API_KEY", ""))
    if not secret.get_secret_value().strip():
        print("No API key configured.")
        return
    try:
        with httpx.Client(timeout=15, trust_env=False, follow_redirects=False) as client:
            response = client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": "Bearer " + secret.get_secret_value()},
            )
            if response.status_code != 200:
                print("model list: unavailable, HTTP", response.status_code)
                return
            models = {x.get("id") for x in response.json().get("data", [])}
            for model in ("gpt-6-luna", "text-embedding-3-small", SPEECH_MODEL):
                print("model access:", model, "listed" if model in models else "not listed")
            if not all(
                model in models for model in ("gpt-6-luna", "text-embedding-3-small", SPEECH_MODEL)
            ):
                return
        provider = OpenAIExtractionProvider(secret)
        if args.extended_only:
            dialogue = OpenAIDialogue(secret, 'synthetic')
            answer = dialogue.respond(ConversationInput(turns=['我想找人一起喝飲料']))
            print('dialogue:', 'ok' if answer.message else 'failed')
            picture = io.BytesIO()
            Image.new("RGB", (32, 32), "yellow").save(picture, "JPEG")
            encoded = base64.b64encode(picture.getvalue()).decode("ascii")
            result = provider.extract_images(
                [{"role": "user", "content": "說明測試圖", "images": [encoded, encoded]}],
                {"type": "object", "properties": {"text": {"type": "string"}}},
                "回傳 JSON，包含 text。",
            )
            print("multi-image:", "ok" if result else "failed")
            return
        result = draft_post(WritingInput(mode="voice", content="想找同學一起吃薯條"), provider)
        print("voice polish:", "ok" if len(result.text) >= 50 else "failed")
        picture = io.BytesIO()
        Image.new("RGB", (32, 32), "yellow").save(picture, "JPEG")
        result = provider.extract_images(
            [
                {
                    "role": "user",
                    "content": "說明測試圖",
                    "images": [base64.b64encode(picture.getvalue()).decode("ascii")],
                }
            ],
            {"type": "object", "properties": {"text": {"type": "string"}}},
            "回傳 JSON，包含 text。",
        )
        print("vision:", "ok" if result else "failed")
        result = interpret(IntakeRequest(turns=["我想找人一起喝飲料"]), provider)
        print("intake:", "ok" if result.facts else "failed")
        vectors = OpenAIEmbeddingProvider(secret).embed(["一起喝飲料"], purpose="query")
        print("embedding:", "ok" if len(vectors.vectors[0]) == 512 else "failed")
        audio = b"".join(audio_chunks("你好，我們一起來拼單。", secret))
        print("speech:", "ok" if len(audio) > 0 else "failed")
    except Exception as error:
        from fastapi import HTTPException

        from app.stealth.ollama import LocalModelError

        if isinstance(error, HTTPException):
            print("test failed:", error.detail)
        elif isinstance(error, LocalModelError):
            print("test failed:", str(error))
        else:
            print("test failed:", type(error).__name__)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
