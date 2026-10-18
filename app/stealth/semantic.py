"""Semantic retrieval AFTER hard eligibility; output never carries consent or prices."""

from typing import Literal

from pydantic import Field

from app.stealth.ollama import LocalModelError
from app.stealth.provider_contracts import (
    CandidateContext,
    CandidateJudgment,
    CandidateReviewer,
    EmbeddingProvider,
)
from app.stealth.schemas import StrictModel

EXPLANATIONS = {
    "location": "取餐地點一致",
    "time": "可用時間有交集",
    "category": "符合本人確認的餐點選項",
    "shared_tags": "有共同的已確認偏好",
    "offer": "包含明確提供的資源",
    "pickup_volunteer": "有人明確願意取餐",
    "cost_unknown": "實際費用仍待確認",
    "order_missing": "下單者仍待確認",
    "pickup_missing": "取餐者仍待確認",
    "coupon_terms": "優惠券條件仍待持有人確認",
}


class SemanticItem(StrictModel):
    key: str
    similarity: float | None = None
    review: CandidateJudgment | None = None
    review_model: str | None = None
    review_status: Literal["not_requested", "completed", "unavailable"] = "not_requested"
    explanations: list[str]


class SemanticRanking(StrictModel):
    status: Literal["completed", "unavailable", "empty"]
    embedding_model: str | None = None
    items: list[SemanticItem]
    warnings: list[str] = Field(default_factory=list)
    requires_unanimous_confirmation: Literal[True] = True


class SemanticMatcher:
    def __init__(self, embeddings: EmbeddingProvider, reviewer: CandidateReviewer | None = None):
        self.embeddings, self.reviewer = embeddings, reviewer

    def rank(
        self, query: str, contexts: list[CandidateContext], *, review_limit=3
    ) -> SemanticRanking:
        if (
            not 0 <= review_limit <= 3
            or len(contexts) > 12
            or len({c.key for c in contexts}) != len(contexts)
        ):
            raise LocalModelError("invalid_semantic_request")
        if not contexts:
            return SemanticRanking(status="empty", items=[])
        items = [
            SemanticItem(key=c.key, explanations=[EXPLANATIONS[f] for f in c.facts])
            for c in contexts
        ]
        try:
            query_batch = self.embeddings.embed([query], purpose="query")
            documents = self.embeddings.embed([c.document for c in contexts], purpose="document")
            if (query_batch.model_id, query_batch.dimensions) != (
                documents.model_id,
                documents.dimensions,
            ):
                raise LocalModelError("embedding_space_changed")
            if len(documents.vectors) != len(contexts) or len(query_batch.vectors) != 1:
                raise LocalModelError("invalid_embedding_response")
            for item, vector in zip(items, documents.vectors, strict=True):
                item.similarity = round(
                    max(
                        -1.0,
                        min(
                            1.0,
                            sum(a * b for a, b in zip(query_batch.vectors[0], vector, strict=True)),
                        ),
                    ),
                    6,
                )
        except LocalModelError as error:
            return SemanticRanking(status="unavailable", items=items, warnings=[str(error)])
        order = {item.key: i for i, item in enumerate(items)}
        items.sort(key=lambda item: (-item.similarity, order[item.key]))
        result = SemanticRanking(
            status="completed", embedding_model=query_batch.model_id, items=items
        )
        by_key = {context.key: context for context in contexts}
        if self.reviewer:
            for item in items[:review_limit]:
                try:
                    judgment = self.reviewer.review(
                        by_key[item.key].model_copy(update={"query": query})
                    )
                    # Guard arbitrary future adapters, not just the present Ollama implementation.
                    if not judgment.reason_codes or not set(judgment.reason_codes) <= set(
                        by_key[item.key].facts
                    ):
                        raise LocalModelError("ungrounded_candidate_review")
                    if any(
                        f in by_key[item.key].facts
                        for f in ("cost_unknown", "order_missing", "pickup_missing", "coupon_terms")
                    ):
                        judgment.verdict = "uncertain"
                    item.review, item.review_model = judgment, self.reviewer.model
                    item.review_status = "completed"
                except LocalModelError as error:
                    item.review_status = "unavailable"
                    result.warnings.append(str(error))
        return result
