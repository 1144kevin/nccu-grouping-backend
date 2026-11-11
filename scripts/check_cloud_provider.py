"""Synthetic connectivity check; the process loads the existing key without displaying it."""

import base64
from io import BytesIO
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image

from app.core.config import get_settings
from app.core_cloud.gemini_provider import GeminiEmbeddingProvider, GeminiExtractionProvider
from app.core_cloud.post_writing import WritingInput, draft_post

load_dotenv(Path(__file__).resolve().parents[1] / ".local" / "gemini.env")
key = get_settings().stealth_gemini_api_key
schema = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
try:
    text = GeminiExtractionProvider(key)
    result = text.extract([{"role": "user", "content": "回傳測試"}], schema, "只回傳 JSON。")
    print("text:", "ok" if result else "empty")
    picture = BytesIO()
    Image.new("RGB", (20, 20), "red").save(picture, "JPEG")
    encoded = base64.b64encode(picture.getvalue()).decode("ascii")
    result = text.extract_images(
        [{"role": "user", "content": "請描述圖片", "images": [encoded]}],
        schema,
        "只回傳 JSON。",
    )
    print("image:", "ok" if result else "empty")
    vectors = GeminiEmbeddingProvider(key).embed(["測試拼單需求"], purpose="document")
    print("embedding:", "ok" if len(vectors.vectors[0]) == 512 else "invalid")
    draft = draft_post(WritingInput(mode="voice", content="想找人一起吃麥當勞薯條"), text)
    print("voice draft:", "ok" if len(draft.text) >= 50 else "invalid")
    structured = text.draft("我想找人一起喝飲料")
    print("intent draft:", "ok" if structured.provider == "gemini" else "invalid")
except Exception as error:
    print("error:", type(error).__name__, str(error))
    raise SystemExit(1) from None
