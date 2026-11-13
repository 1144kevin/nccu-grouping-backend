"""OpenAI-only local inference with bounded requests and durable spending reservations."""

import base64
import hashlib
import json
import re
import threading
import time
from collections import OrderedDict

import httpx
from pydantic import SecretStr, ValidationError

from app.core_cloud.openai_budget import DEFAULT_LEDGER, LocalBudget, check_rate
from app.stealth.ollama import LocalModelError
from app.stealth.provider_contracts import VectorBatch

TEXT_MODEL = "gpt-6-luna"
EMBED_MODEL = "text-embedding-3-small"
_embedding_cache = OrderedDict()
_embedding_lock = threading.Lock()


class OpenAITransport:
    def __init__(self, key: SecretStr, *, transport=None, budget=None):
        if not key.get_secret_value().strip():
            raise LocalModelError("openai_not_configured")
        self._key = key
        self.transport = transport
        self.budget = budget or LocalBudget(DEFAULT_LEDGER)

    def request(self, path, payload, *, kind, units, maximum=1_000_000):
        check_rate(kind)
        self.budget.reserve(units, kind)
        try:
            with httpx.Client(
                timeout=httpx.Timeout(45, connect=5),
                trust_env=False,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                with client.stream(
                    "POST",
                    "https://api.openai.com/v1/" + path,
                    headers={"Authorization": "Bearer " + self._key.get_secret_value()},
                    json=payload,
                ) as response:
                    if response.status_code == 429:
                        raise LocalModelError("openai_quota_or_rate_limit")
                    if response.status_code in (401, 403):
                        raise LocalModelError("openai_access_denied")
                    if response.status_code != 200:
                        if response.status_code == 400:
                            try:
                                response.read()
                                error = response.json().get("error", {})
                                code = error.get("code") or "unknown"
                                param = error.get("param") or "unknown"
                                if all(
                                    isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", v)
                                    for v in (code, param)
                                ):
                                    raise LocalModelError(f"openai_http_400_{code}_{param}")
                            except (ValueError, TypeError):
                                pass
                        raise LocalModelError(f"openai_http_{response.status_code}")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > maximum:
                            raise LocalModelError("openai_invalid_response")
            result = json.loads(body)
            if not isinstance(result, dict):
                raise ValueError("invalid response")
            return result
        except httpx.TimeoutException:
            raise LocalModelError("openai_timeout") from None
        except (httpx.HTTPError, ValueError, TypeError):
            raise LocalModelError("openai_unavailable") from None


class OpenAIExtractionProvider(OpenAITransport):
    model = TEXT_MODEL

    def extract(self, messages, schema, prompt):
        if (len(messages) > 30 or len(prompt) > 16000
                or len(prompt) + sum(len(str(m.get("content", ""))) for m in messages) > 32000):
            raise LocalModelError("openai_input_too_large")
        contents = []
        images = 0
        for index, msg in enumerate(messages):
            content = str(msg.get("content", ""))
            if index == 0:
                content = "請依指定格式回傳 JSON。\n" + content
            if len(content) > 8000:
                raise LocalModelError("openai_input_too_large")
            assistant = msg.get("role") == "assistant"
            parts = [{"type": "output_text" if assistant else "input_text", "text": content}]
            for image in msg.get("images", []):
                images += 1
                if images > 5 or len(image) > 5_592_408:
                    raise LocalModelError("openai_input_too_large")
                try:
                    base64.b64decode(image, validate=True)
                except (ValueError, TypeError):
                    raise LocalModelError("invalid_image_input") from None
                parts.append(
                    {
                        "type": "input_image",
                        "image_url": "data:image/jpeg;base64," + image,
                        "detail": "high",
                    }
                )
            contents.append(
                {
                    "role": "assistant" if msg.get("role") == "assistant" else "user",
                    "content": parts,
                }
            )
        result = self.request(
            "responses",
            {
                "model": self.model,
                "store": False,
                "instructions": prompt
                + "\nReturn only JSON conforming to this schema: "
                + json.dumps(schema, ensure_ascii=False),
                "input": contents,
                "max_output_tokens": 1800,
                "reasoning": {"effort": "low"},
                "text": {"format": {"type": "json_object"}},
            },
            kind="image" if images else "text",
            units=200000 if images else 50000,
        )
        try:
            if result.get("status") != "completed":
                raise ValueError("incomplete")
            parts = [
                p
                for item in result["output"]
                if item.get("type") == "message"
                for p in item.get("content", [])
                if p.get("type") == "output_text"
            ]
            text = "".join(p["text"] for p in parts)
            if not text:
                raise ValueError("empty")
            return text
        except (KeyError, TypeError, ValueError):
            raise LocalModelError("openai_invalid_response") from None

    extract_writing = extract
    extract_images = extract

    def draft(self, text):
        from app.stealth.schemas import DraftContent, DraftOutput

        try:
            result = self.extract(
                [{"role": "user", "content": text}],
                DraftContent.model_json_schema(),
                "僅抽取使用者明確提供的資訊，未知留空，不捏造承諾。回傳符合 schema 的 JSON："
                + json.dumps(DraftContent.model_json_schema(), ensure_ascii=False),
            )
            value = DraftContent.model_validate_json(result)
            return DraftOutput(**value.model_dump(), provider="openai", is_llm=True)
        except ValidationError:
            raise LocalModelError("openai_invalid_response") from None


class OpenAIEmbeddingProvider(OpenAITransport):
    model = EMBED_MODEL
    dimensions = 512
    model_id = "openai:text-embedding-3-small:512:v1"

    def embed(self, texts, *, purpose):
        if purpose not in ("query", "document") or not 1 <= len(texts) <= 16:
            raise LocalModelError("invalid_embedding_input")
        if any(
            not isinstance(t, str) or not t.strip() or len(t) > 2000 or "\0" in t for t in texts
        ):
            raise LocalModelError("invalid_embedding_input")
        scope = hashlib.sha256(self._key.get_secret_value().encode()).digest()
        keys = [
            hashlib.sha256(scope + (self.model_id + ":" + t).encode()).hexdigest() for t in texts
        ]
        with _embedding_lock:
            now = time.monotonic()
            cached = {
                k: _embedding_cache[k][1]
                for k in keys
                if k in _embedding_cache and _embedding_cache[k][0] > now
            }
            seen = set(cached)
            missing = []
            for index, key in enumerate(keys):
                if key not in seen:
                    seen.add(key)
                    missing.append(index)
        if not missing:
            return VectorBatch(
                model_id=self.model_id,
                dimensions=self.dimensions,
                vectors=[cached[k] for k in keys],
            )
        result = self.request(
            "embeddings",
            {
                "model": self.model,
                "input": [texts[i] for i in missing],
                "dimensions": self.dimensions,
            },
            kind="embedding",
            units=10000,
        )
        try:
            data = sorted(result["data"], key=lambda x: x["index"])
            if [x["index"] for x in data] != list(range(len(missing))):
                raise ValueError("wrong index")
            batch = VectorBatch(
                model_id=self.model_id,
                dimensions=self.dimensions,
                vectors=[x["embedding"] for x in data],
            )
            with _embedding_lock:
                for index, vector in zip(missing, batch.vectors, strict=True):
                    cached[keys[index]] = vector
                    _embedding_cache[keys[index]] = (time.monotonic() + 3600, vector)
                while len(_embedding_cache) > 512:
                    _embedding_cache.popitem(last=False)
            return VectorBatch(
                model_id=self.model_id,
                dimensions=self.dimensions,
                vectors=[cached[k] for k in keys],
            )
        except (KeyError, TypeError, ValueError, ValidationError):
            raise LocalModelError("invalid_embedding_response") from None


class OpenAIIntentInterpreter:
    def __init__(self, key, *, transport=None, budget=None):
        self.provider = OpenAIExtractionProvider(key, transport=transport, budget=budget)

    def interpret(self, value):
        from app.core_cloud.intake import interpret

        return interpret(value, self.provider).model_copy(update={"provider": "openai"})


class OpenAIDialogue:
    provider = "openai"

    def __init__(self, key, actor, *, transport=None, budget=None):
        self.provider_client = OpenAIExtractionProvider(key, transport=transport, budget=budget)
        self.actor = actor

    def respond(self, value):
        from app.core_cloud.conversation import NaturalTurn
        from app.core_cloud.gemini_conversation import PROMPT, tone_instruction

        messages = []
        for i, turn in enumerate(value.turns):
            messages.append({"role": "user", "content": turn})
            if i < len(value.assistant_turns):
                messages.append({"role": "assistant", "content": value.assistant_turns[i]})
        prompt = PROMPT
        if value.preferences is not None:
            tone_hint = tone_instruction(value.preferences)
            if tone_hint:
                prompt += "\n" + tone_hint
            prompt += "\n使用者偏好僅供參考，不代表已確認：" + value.preferences.model_dump_json()
        try:
            response = self.provider_client.extract(
                messages, NaturalTurn.model_json_schema(), prompt
            )
            return NaturalTurn.model_validate_json(response)
        except ValidationError:
            raise LocalModelError("openai_invalid_response") from None
