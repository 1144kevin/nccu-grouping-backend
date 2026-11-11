"""Cloud OpenAI must use the shared budget; all values here are synthetic."""

from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.api.routes.ai import configured_provider
from app.core.cloud_runtime import validate_cloud_settings
from app.core_cloud.openai_budget import PostgresBudget
from app.core_cloud.openai_provider import OpenAIEmbeddingProvider
from app.stealth.provider_registry import local_semantic_matcher
from tests.test_cloud_runtime import configured


def cloud(**extra):
    return configured(
        stealth_ai_provider="openai",
        stealth_conversation_provider="openai",
        local_openai_api_key="synthetic",
        openai_budget_database_url="postgresql://synthetic",
        cloud_openai_schema_ready=True,
        stealth_semantic_enabled=True,
        **extra,
    )


def test_cloud_provider_and_embeddings_use_postgres_budget():
    settings = cloud()
    validate_cloud_settings(settings)
    request = Mock()
    request.state.runtime_settings = settings
    assert isinstance(configured_provider(request).budget, PostgresBudget)
    embeddings = local_semantic_matcher(settings).embeddings
    assert isinstance(embeddings, OpenAIEmbeddingProvider)
    assert isinstance(embeddings.budget, PostgresBudget)


def test_speech_uses_shared_budget(monkeypatch):
    from app.api.routes import stealth_cloud
    from app.core_cloud.reply_speech import SpeechInput

    settings = cloud()
    budget = object()
    request = Mock()
    request.state.runtime_settings = settings
    user = Mock(id="synthetic")
    response = Mock(headers={})
    synthesize = Mock(return_value={"mime_type": "audio/wav", "audio_base64": ""})
    monkeypatch.setattr(stealth_cloud, "configured_budget", lambda _: budget)
    monkeypatch.setattr(stealth_cloud.openai_speech, "synthesize", synthesize)
    stealth_cloud.reply_speech(SpeechInput(text="你好"), request, user, response)
    assert synthesize.call_args.kwargs["budget"] is budget


@pytest.mark.parametrize(
    "field,value",
    [
        ("local_openai_api_key", ""),
        ("openai_budget_database_url", ""),
        ("cloud_openai_schema_ready", False),
        ("stealth_conversation_provider", "gemini"),
    ],
)
def test_cloud_openai_rejects_incomplete_configuration(field, value):
    settings = cloud().model_copy(update={field: value})
    if field in ("local_openai_api_key", "openai_budget_database_url"):
        from pydantic import SecretStr

        settings = settings.model_copy(update={field: SecretStr(value)})
    with pytest.raises(ValueError):
        validate_cloud_settings(settings)
    if field == "openai_budget_database_url":
        request = Mock()
        request.state.runtime_settings = settings
        with pytest.raises(HTTPException) as error:
            configured_provider(request)
        assert error.value.detail == "openai_budget_unavailable"
