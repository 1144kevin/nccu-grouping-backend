"""Explicit local registry. Apple/OpenAI intentionally have no executable adapters yet."""

from fastapi import HTTPException

from app.core.config import Settings, get_settings
from app.stealth.local_providers import OllamaCandidateReviewer, OllamaEmbeddingProvider
from app.stealth.ollama import LocalModelError, OllamaDraftProvider
from app.stealth.semantic import SemanticMatcher


def local_semantic_matcher(settings: Settings | None = None) -> SemanticMatcher:
    settings = settings or get_settings()
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
