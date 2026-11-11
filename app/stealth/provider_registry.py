"""Provider selection for semantic matching."""

from fastapi import HTTPException

from app.core.config import Settings, get_settings
from app.stealth.local_providers import OllamaCandidateReviewer, OllamaEmbeddingProvider
from app.stealth.ollama import LocalModelError, OllamaDraftProvider
from app.stealth.semantic import SemanticMatcher


def local_semantic_matcher(settings: Settings | None = None) -> SemanticMatcher:
    settings = settings or get_settings()
    if (
        settings.stealth_ai_provider == "openai"
        and settings.stealth_semantic_enabled
        and (settings.environment == "local" or settings.core_cloud_enabled)
    ):
        from app.core_cloud.openai_budget import configured_budget
        from app.core_cloud.openai_provider import OpenAIEmbeddingProvider, OpenAIExtractionProvider

        try:
            return SemanticMatcher(
                OpenAIEmbeddingProvider(
                    settings.local_openai_api_key, budget=configured_budget(settings)
                ),
                OllamaCandidateReviewer(
                    OpenAIExtractionProvider(
                        settings.local_openai_api_key, budget=configured_budget(settings)
                    )
                ),
            )
        except LocalModelError as error:
            raise HTTPException(503, str(error)) from None
    if settings.stealth_ai_provider == "gemini" and settings.stealth_semantic_enabled:
        from app.core_cloud.gemini_provider import GeminiEmbeddingProvider, GeminiExtractionProvider

        try:
            return SemanticMatcher(
                GeminiEmbeddingProvider(settings.stealth_gemini_api_key),
                OllamaCandidateReviewer(GeminiExtractionProvider(settings.stealth_gemini_api_key)),
            )
        except LocalModelError as error:
            raise HTTPException(503, str(error)) from None
    if (
        settings.environment != "local"
        or settings.core_cloud_enabled
        or not settings.stealth_semantic_enabled
        or not settings.stealth_ollama_local_only_confirmed
    ):
        raise HTTPException(503, "local_semantic_not_configured")
    try:
        return SemanticMatcher(
            OllamaEmbeddingProvider(settings.stealth_embedding_model, local_only_confirmed=True),
            OllamaCandidateReviewer(
                OllamaDraftProvider(settings.stealth_ollama_model, local_only_confirmed=True)
            ),
        )
    except LocalModelError as error:
        raise HTTPException(503, str(error)) from None
