"""Replaceable AI boundaries. Providers return drafts, never permissions or consent."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import Field, model_validator

from app.stealth.schemas import StrictModel

if TYPE_CHECKING:
    from app.core_cloud.intake import IntakeRequest, IntakeResult


class ExtractionTransport(Protocol):
    model: str

    def extract(self, messages: list[dict], schema: dict, prompt: str) -> str: ...


class IntentInterpreter(Protocol):
    def interpret(self, value: IntakeRequest) -> IntakeResult: ...


class VectorBatch(StrictModel):
    model_id: str = Field(min_length=1, max_length=240)
    dimensions: int = Field(strict=True, ge=16, le=4096)
    vectors: list[list[float]] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def finite_normalized_vectors(self):
        for vector in self.vectors:
            if len(vector) != self.dimensions or not all(math.isfinite(x) for x in vector):
                raise ValueError("invalid vector shape or non-finite coordinates")
            norm = math.sqrt(sum(x * x for x in vector))
            if not math.isfinite(norm) or norm <= 1e-12:
                raise ValueError("zero vector")
            vector[:] = [x / norm for x in vector]
        return self


class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str], *, purpose: Literal["query", "document"]) -> VectorBatch: ...


FactCode = Literal[
    "location",
    "time",
    "category",
    "shared_tags",
    "offer",
    "pickup_volunteer",
    "cost_unknown",
    "order_missing",
    "pickup_missing",
    "coupon_terms",
]


class CandidateContext(StrictModel):
    key: str = Field(min_length=1, max_length=100)
    query: str = Field(default="", max_length=2000)
    document: str = Field(min_length=1, max_length=2000)
    facts: list[FactCode] = Field(max_length=10)


class CandidateJudgment(StrictModel):
    verdict: Literal["complementary", "similar", "uncertain", "not_recommended"]
    reason_codes: list[FactCode] = Field(max_length=10)


class CandidateReviewer(Protocol):
    model: str

    def review(self, context: CandidateContext) -> CandidateJudgment: ...
