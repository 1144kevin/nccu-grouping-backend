from typing import Literal
from uuid import UUID

import httpx
from fastapi import HTTPException
from pydantic import Field

from app.core_cloud.store import PROJECT_URL, SupabaseCoreStore
from app.stealth.schemas import StrictModel


class DeletionCounts(StrictModel):
    owned_posts: int = Field(ge=0)
    messages: int = Field(ge=0)
    images: int = Field(ge=0)
    participations: int = Field(ge=0)
    proposals: int = Field(ge=0)


class DeletionStatus(StrictModel):
    phase: Literal["none", "prepared", "deleting"]
    recent_auth: bool
    counts: DeletionCounts


class DeletionPreview(StrictModel):
    phase: Literal["prepared"]
    challenge: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    expires_in: Literal[600]
    counts: DeletionCounts


class DeletionConfirm(StrictModel):
    confirmation: Literal["DELETE"]
    challenge: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{43}$")


class Deleted(StrictModel):
    deleted: Literal[True]


class AccountDeletion:
    def __init__(self, store: SupabaseCoreStore, actor: UUID, *, transport=None):
        self.store, self.actor, self.transport = store, actor, transport

    def rpc(self, name: str, action: str, args: dict | None = None):
        assert name in ("core_user_blocks", "core_account_deletion")
        return self.store._request(
            "POST",
            "/rest/v1/rpc/" + name,
            body={"action": action, "args": args or {}},
            token=self.store.token,
        )

    def status(self):
        return DeletionStatus.model_validate(self.rpc("core_account_deletion", "status"))

    def preview(self):
        return DeletionPreview.model_validate(self.rpc("core_account_deletion", "prepare"))

    def delete(self, value: DeletionConfirm):
        self.rpc("core_account_deletion", "begin", value.model_dump(exclude_none=True))
        assets = self.rpc("core_account_deletion", "assets")
        if (
            not isinstance(assets, dict)
            or not isinstance(assets.get("items"), list)
            or len(assets["items"]) > 40
        ):
            raise HTTPException(503, "account_deletion_cleanup_failed")
        buckets: dict[str, list[str]] = {}
        for item in assets["items"]:
            path = item.get("path") if isinstance(item, dict) else None
            bucket = item.get("bucket", "core-post-images") if isinstance(item, dict) else None
            if bucket not in ("core-post-images", "core-chat-images") or not isinstance(path, str):
                raise HTTPException(503, "account_deletion_cleanup_failed")
            prefix = f"{self.actor}/" if bucket == "core-post-images" else ""
            try:
                image_id = UUID(path[len(prefix) : -4])
            except ValueError:
                raise HTTPException(503, "account_deletion_cleanup_failed") from None
            if path != f"{prefix}{image_id}.jpg":
                raise HTTPException(503, "account_deletion_cleanup_failed")
            paths = buckets.setdefault(bucket, [])
            paths.append(path)
            if len(paths) > 20:
                raise HTTPException(503, "account_deletion_cleanup_failed")
        for bucket, paths in buckets.items():
            try:
                with httpx.Client(
                    base_url=PROJECT_URL,
                    timeout=25,
                    trust_env=False,
                    follow_redirects=False,
                    transport=self.transport,
                ) as client:
                    result = client.request(
                        "DELETE",
                        f"/storage/v1/object/{bucket}",
                        headers={
                            "apikey": self.store.key,
                            "Authorization": f"Bearer {self.store.token}",
                        },
                        json={"prefixes": paths},
                    )
                if result.status_code not in (200, 204):
                    raise HTTPException(503, "account_deletion_cleanup_failed")
            except httpx.HTTPError:
                raise HTTPException(503, "account_deletion_cleanup_failed") from None
        return Deleted.model_validate(self.rpc("core_account_deletion", "finish"))
