"""Capability-scoped public post reads without private account or order information."""

import re
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.core_cloud.images import MAX_OUTPUT_BYTES
from app.core_cloud.store import PROJECT_URL, SupabaseCoreStore
from app.core_local.schemas import PostImage


class ShareLink(BaseModel):
    token: str = Field(pattern=r"^[0-9a-f]{64}$")


class SharedPost(BaseModel):
    id: UUID
    author_name: str
    author_badge: str
    author_level: int
    content: str
    tags: list[str]
    created_at: datetime
    deadline_at: datetime
    capacity: int
    joined: int
    status: Literal["open", "full", "expired"]
    price_per_person: Decimal | None
    location_name: str
    images: list[PostImage] = Field(default_factory=list, max_length=5)


class PostSharingService:
    def __init__(self, store: SupabaseCoreStore, *, transport=None):
        self.store, self.transport = store, transport

    def rpc(self, action: str, args: dict, *, authenticated=False):
        return self.store._request(
            "POST",
            "/rest/v1/rpc/core_post_share",
            body={"action": action, "args": args},
            # Public reads deliberately do not inherit the caller's account session.
            token=self.store.token if authenticated else None,
        )

    def create(self, post_id: UUID) -> ShareLink:
        return ShareLink.model_validate(
            self.rpc("create", {"post_id": str(post_id)}, authenticated=True)
        )

    def read(self, token: str) -> SharedPost:
        return SharedPost.model_validate(self.rpc("read", {"token": token}))

    def image(self, token: str, image_id: UUID) -> bytes:
        asset = self.rpc("image", {"token": token, "image_id": str(image_id)})
        path = asset.get("object_path", "") if isinstance(asset, dict) else ""
        if not re.fullmatch(r"[0-9a-f-]{36}/" + re.escape(str(image_id)) + r"\.jpg", path):
            raise HTTPException(503, "image_storage_unavailable")
        try:
            with httpx.Client(
                timeout=20, trust_env=False, follow_redirects=False, transport=self.transport
            ) as client:
                with client.stream(
                    "GET",
                    f"{PROJECT_URL}/storage/v1/object/core-post-images/{path}",
                    headers={
                        "apikey": self.store.key,
                        "x-post-share": token,
                        "Accept-Encoding": "identity",
                    },
                ) as response:
                    if response.status_code != 200:
                        raise HTTPException(
                            404 if response.status_code in (400, 401, 403, 404) else 503,
                            "shared_image_unavailable",
                        )
                    if (
                        response.headers.get("content-type", "").split(";", 1)[0] != "image/jpeg"
                        or response.headers.get("content-encoding", "identity") != "identity"
                    ):
                        raise HTTPException(503, "image_storage_unavailable")
                    content = bytearray()
                    for chunk in response.iter_bytes(4096):
                        content.extend(chunk)
                        if len(content) > MAX_OUTPUT_BYTES:
                            raise HTTPException(503, "image_storage_unavailable")
                    return bytes(content)
        except httpx.HTTPError:
            raise HTTPException(503, "image_storage_unavailable") from None
