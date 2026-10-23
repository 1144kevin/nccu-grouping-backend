"""Private image attachments for post, direct and anonymous coordination chats."""

import base64
from typing import Annotated, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.api.routes.conversations import cloud_chat
from app.api.routes.engagement import EngagementDep
from app.api.routes.uploads import Key, upload_image
from app.core_cloud.media import AssetView, MediaService
from app.core_local.dependencies import StoreDep, UserDep
from app.core_local.schemas import Input

router = APIRouter(prefix="/chat-photos", tags=["chat-photos"])


class ChatMediaService(MediaService):
    bucket = "core-chat-images"

    def rpc(self, action: str, args: dict | None = None):
        return self.store._request(
            "POST",
            "/rest/v1/rpc/core_chat_media",
            body={"action": action, "args": args or {}},
            token=self.store.token,
        )


def chat_media(service: EngagementDep, user: UserDep):
    return ChatMediaService(service.store, user.id)


PhotoMedia = Annotated[ChatMediaService, Depends(chat_media)]


@router.post("/uploads", response_model=AssetView, status_code=201)
async def upload_photo(request: Request, service: PhotoMedia, key: Key):
    return await upload_image(request, service, key)


class PhotoMessage(Input):
    kind: Literal["group", "direct", "stealth"]
    thread_id: UUID
    image_id: UUID


class PhotoContent(BaseModel):
    content_base64: str
    width: int
    height: int


@router.post("", status_code=201)
def send_photo(value: PhotoMessage, store: StoreDep, _: UserDep, key: Key):
    return cloud_chat(store)._request(
        "POST",
        "/rest/v1/rpc/core_chat_photo",
        body={
            "action": "send",
            "args": {
                **value.model_dump(mode="json"),
                "request_key": str(uuid5(NAMESPACE_URL, "chat-photo:" + key)),
            },
        },
        token=store.token,
    )


@router.get("/{image_id}", response_model=PhotoContent)
def read_photo(image_id: UUID, service: PhotoMedia):
    # A server-side membership check precedes every download. The response avoids signed
    # object paths, which would reveal uploader account IDs in anonymous stealth rooms.
    asset = service.store._request(
        "POST",
        "/rest/v1/rpc/core_chat_photo",
        body={"action": "read", "args": {"image_id": str(image_id)}},
        token=service.store.token,
    )
    content = service._download(asset["object_path"])
    return PhotoContent(
        content_base64=base64.b64encode(content).decode("ascii"),
        width=asset["width"],
        height=asset["height"],
    )


@router.delete("/{image_id}")
def remove_photo_draft(image_id: UUID, service: PhotoMedia):
    return service.delete(image_id)
