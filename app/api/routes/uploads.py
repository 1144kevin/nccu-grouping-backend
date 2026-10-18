import base64
import binascii
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from app.api.routes.engagement import EngagementDep
from app.core_cloud.images import normalize_image
from app.core_cloud.media import (
    AssetView,
    DraftAssets,
    ImagePublish,
    ImageUpload,
    ImageURL,
    MediaService,
)
from app.core_local.dependencies import UserDep
from app.core_local.schemas import PostView

router = APIRouter(prefix="/uploads", tags=["uploads"])


def media(service: EngagementDep, user: UserDep) -> MediaService:
    return MediaService(service.store, user.id)


Media = Annotated[MediaService, Depends(media)]
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=12, max_length=100)]


@router.post(
    "/images",
    response_model=AssetView,
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": ImageUpload.model_json_schema()}},
        }
    },
)
async def upload_image(request: Request, service: Media, key: Key):
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
        raise HTTPException(415, "invalid_image_input")
    # Read the stream directly: JSON/multipart parsing must not allocate an unbounded upload first.
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 6 * 1024 * 1024:
            raise HTTPException(413, "image_too_large")
        body.extend(chunk)
    try:
        value = ImageUpload.model_validate_json(body)
        raw = base64.b64decode(value.content_base64, validate=True)
    except (ValidationError, ValueError, binascii.Error):
        raise HTTPException(422, "invalid_image_input") from None
    normalized = await run_in_threadpool(normalize_image, raw)
    return await run_in_threadpool(service.upload, normalized, key)


@router.post("/publish", response_model=PostView, status_code=201)
def publish_with_image(value: ImagePublish, service: Media, key: Key):
    return service.publish(value, key)


@router.get("/images", response_model=DraftAssets)
def draft_images(service: Media):
    return service.drafts()


@router.get("/images/{image_id}", response_model=ImageURL)
def image_url(image_id: UUID, service: Media):
    return service.url(image_id)


@router.delete("/images/{image_id}")
def delete_draft_image(image_id: UUID, service: Media):
    return service.delete(image_id)
