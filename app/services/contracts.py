from typing import Protocol


class AIService(Protocol):
    async def create_post_draft(self, text: str, media_urls: list[str]) -> dict[str, object]: ...

    async def create_embedding(self, text: str) -> list[float]: ...


class NotificationService(Protocol):
    async def send_push(
        self,
        tokens: list[str],
        title: str,
        body: str,
        data: dict[str, object],
    ) -> None: ...
