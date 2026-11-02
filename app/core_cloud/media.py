"""User-session Storage access with narrow backend image attestation, no admin key."""

import hashlib
import hmac
import re
from typing import Literal
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import httpx
from fastapi import HTTPException
from pydantic import AwareDatetime, BaseModel, Field, model_validator

from app.core.config import get_settings
from app.core_cloud.images import MAX_OUTPUT_BYTES, NormalizedImage
from app.core_cloud.store import PROJECT_URL, SupabaseCoreStore
from app.core_local.schemas import PostInput, PostView
from app.stealth.schemas import StrictModel

BUCKET = "core-post-images"
MAX_BASE64 = 5_592_408


class ImageUpload(StrictModel):
    content_base64: str = Field(min_length=1, max_length=MAX_BASE64)


class AssetView(BaseModel):
    id: UUID
    width: int = Field(ge=1, le=1600)
    height: int = Field(ge=1, le=1600)
    state: Literal["pending", "ready"]


class DraftAsset(AssetView):
    created_at: AwareDatetime


class DraftAssets(BaseModel):
    items: list[DraftAsset]


class ImagePublish(StrictModel):
    image_id: UUID | None = None
    image_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=5)
    value: PostInput

    @model_validator(mode="after")
    def valid_album(self):
        if (self.image_id is None) == (self.image_ids is None):
            raise ValueError("choose_one_image_input")
        if self.image_ids is not None and len(set(self.image_ids)) != len(self.image_ids):
            raise ValueError("duplicate_images")
        return self


class ImageEdit(StrictModel):
    value: PostInput
    image_ids: list[UUID] = Field(max_length=5)
    expected_version: int = Field(ge=1, strict=True)

    @model_validator(mode="after")
    def distinct_images(self):
        if len(set(self.image_ids)) != len(self.image_ids):
            raise ValueError("duplicate_images")
        return self


class ImageURL(BaseModel):
    id: UUID
    url: str
    expires_in: int = 60
    width: int
    height: int


class ImageContent(BaseModel):
    content_base64: str
    width: int
    height: int


class MediaService:
    bucket = BUCKET

    def __init__(self, store: SupabaseCoreStore, owner_id: UUID, *, transport=None):
        settings = get_settings()
        key = settings.core_media_signing_key.get_secret_value()
        if not settings.core_media_enabled or not re.fullmatch(r"[0-9a-f]{64}", key):
            raise HTTPException(503, "image_storage_not_configured")
        self.store, self.owner_id, self.transport = store, owner_id, transport
        self.signing_key = bytes.fromhex(key)

    def rpc(self, action: str, args: dict | None = None):
        return self.store._request(
            "POST",
            "/rest/v1/rpc/core_media",
            body={"action": action, "args": args or {}},
            token=self.store.token,
        )

    def _headers(self):
        return {"apikey": self.store.key, "Authorization": f"Bearer {self.store.token}"}

    def _client(self):
        return httpx.Client(
            base_url=PROJECT_URL,
            headers=self._headers(),
            timeout=30,
            trust_env=False,
            follow_redirects=False,
            transport=self.transport,
        )

    @staticmethod
    def _failure(response):
        if response.status_code in (401, 403):
            raise HTTPException(403, "image_storage_access_denied")
        raise HTTPException(503, "image_storage_unavailable")

    def _storage_json(self, method: str, path: str, value: dict):
        try:
            with self._client() as client:
                response = client.request(method, path, json=value)
            if response.status_code >= 300:
                self._failure(response)
            result = response.json()
            if not isinstance(result, (dict, list)):
                raise ValueError("invalid response")
            return result
        except (httpx.HTTPError, ValueError):
            raise HTTPException(503, "image_storage_unavailable") from None

    def _upload(self, path: str, content: bytes):
        try:
            with self._client() as client:
                result = client.post(
                    f"/storage/v1/object/{self.bucket}/{path}",
                    content=content,
                    headers={
                        "Content-Type": "image/jpeg",
                        "x-upsert": "false",
                        "Cache-Control": "max-age=60",
                    },
                )
            if result.status_code >= 300:
                # A previous attempt may have uploaded successfully before its response was lost.
                try:
                    error = result.json()
                except ValueError:
                    error = {}
                if (
                    result.status_code in (400, 409)
                    and isinstance(error, dict)
                    and error.get("error")
                    in ("Duplicate", "AssetAlreadyExists", "ResourceAlreadyExists")
                ):
                    return
                self._failure(result)
        except httpx.HTTPError:
            raise HTTPException(503, "image_storage_unavailable") from None

    def _download(self, path: str) -> bytes:
        try:
            with (
                self._client() as client,
                client.stream(
                    "GET",
                    f"/storage/v1/object/authenticated/{self.bucket}/{path}",
                    headers={"Accept-Encoding": "identity"},
                ) as response,
            ):
                if response.status_code != 200:
                    self._failure(response)
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise HTTPException(503, "image_storage_unavailable")
                content = bytearray()
                for chunk in response.iter_bytes(4096):
                    content.extend(chunk)
                    if len(content) > MAX_OUTPUT_BYTES:
                        raise HTTPException(413, "image_too_large")
                return bytes(content)
        except httpx.HTTPError:
            raise HTTPException(503, "image_storage_unavailable") from None

    def upload(self, image: NormalizedImage, key: str) -> AssetView:
        asset = self.rpc(
            "reserve",
            {
                "request_key": key,
                "sha256": image.sha256,
                "width": image.width,
                "height": image.height,
            },
        )
        if asset["state"] == "ready":
            return AssetView.model_validate(self.rpc("read", {"image_id": asset["id"]}))
        self._upload(asset["object_path"], image.content)
        snapshot = self.rpc("snapshot", {"image_id": asset["id"]})
        actual = self._download(asset["object_path"])
        if not hmac.compare_digest(hashlib.sha256(actual).hexdigest(), image.sha256):
            raise HTTPException(409, "image_upload_conflict")
        # Include the Storage row ID so delete/re-upload races cannot reuse an attestation.
        message = f"{asset['id']}:{self.owner_id}:{image.sha256}:{snapshot['storage_id']}"
        proof = hmac.new(self.signing_key, message.encode(), hashlib.sha256).hexdigest()
        return AssetView.model_validate(
            self.rpc(
                "ready",
                {"image_id": asset["id"], "storage_id": snapshot["storage_id"], "proof": proof},
            )
        )

    def publish(self, value: ImagePublish, key: str) -> PostView:
        return PostView.model_validate(
            self.rpc(
                "publish_many" if value.image_ids is not None else "publish",
                {
                    **(
                        {"image_ids": [str(i) for i in value.image_ids]}
                        if value.image_ids is not None
                        else {"image_id": str(value.image_id)}
                    ),
                    "value": value.value.model_dump(mode="json"),
                    "request_key": key,
                },
            )
        )

    def edit(self, post_id: UUID, value: ImageEdit) -> PostView:
        return PostView.model_validate(
            self.store._request(
                "POST",
                "/rest/v1/rpc/core_edit_post_media",
                body={"args": {"post_id": str(post_id), **value.model_dump(mode="json")}},
                token=self.store.token,
            )
        )

    def url(self, image_id: UUID) -> ImageURL:
        asset = self.rpc("read", {"image_id": str(image_id)})
        result = self._storage_json(
            "POST",
            f"/storage/v1/object/sign/{self.bucket}/{asset['object_path']}",
            {"expiresIn": 60},
        )
        signed = result.get("signedURL") if isinstance(result, dict) else None
        if not isinstance(signed, str):
            raise HTTPException(503, "image_storage_unavailable")
        url = PROJECT_URL + "/storage/v1" + signed if signed.startswith("/object/sign/") else signed
        parsed = urlsplit(url)
        query = parse_qs(parsed.query)
        if (
            parsed.scheme != "https"
            or parsed.netloc != urlsplit(PROJECT_URL).netloc
            or parsed.username
            or parsed.password
            or parsed.fragment
            or parsed.path != f"/storage/v1/object/sign/{self.bucket}/{asset['object_path']}"
            or len(query.get("token", [])) != 1
            or len(query["token"][0]) > 4096
        ):
            raise HTTPException(503, "image_storage_unavailable")
        return ImageURL(id=image_id, url=url, width=asset["width"], height=asset["height"])

    def drafts(self) -> DraftAssets:
        return DraftAssets.model_validate(self.rpc("list"))

    def delete(self, image_id: UUID):
        asset = self.rpc("delete_prepare", {"image_id": str(image_id)})
        self._storage_json(
            "DELETE", f"/storage/v1/object/{self.bucket}", {"prefixes": [asset["object_path"]]}
        )
        return self.rpc("delete_finish", {"image_id": str(image_id)})
