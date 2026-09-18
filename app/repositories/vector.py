from typing import Protocol
from uuid import UUID


class VectorSearchResult(Protocol):
    post_id: UUID
    score: float


class VectorRepository(Protocol):
    async def upsert_post(
        self,
        post_id: UUID,
        embedding: list[float],
        model: str,
        content_hash: str,
    ) -> None: ...

    async def search_posts(
        self,
        embedding: list[float],
        limit: int,
    ) -> list[VectorSearchResult]: ...

    async def delete_post(self, post_id: UUID) -> None: ...
