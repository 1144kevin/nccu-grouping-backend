"""Account-owned avatars and private evidence; no privileged credentials."""

import base64
from io import BytesIO
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from PIL import Image
from pydantic import BaseModel, Field

from app.api.routes.engagement import EngagementDep
from app.api.routes.uploads import Key, upload_image
from app.core_cloud.images import normalize_image
from app.core_cloud.media import AssetView, MediaService
from app.core_local.dependencies import UserDep
from app.stealth.schemas import StrictModel

router = APIRouter(tags=["profile-media"])


class PersonalMedia(MediaService):
    bucket = "core-personal-images"
    purpose = "evidence"

    def rpc(self, action, args=None):
        value = dict(args or {})
        if action == "reserve":
            value["purpose"] = self.purpose
        return self.store._request(
            "POST",
            "/rest/v1/rpc/core_personal_media",
            body={"action": action, "args": value},
            token=self.store.token,
        )

    def upload(self, image, key):
        if self.purpose == "avatar":
            with Image.open(BytesIO(image.content)) as photo:
                photo.thumbnail((320, 320))
                output = BytesIO()
                photo.convert("RGB").save(output, format="JPEG", quality=85)
            image = normalize_image(output.getvalue())
        return super().upload(image, key)


def personal_media(service: EngagementDep, user: UserDep):
    return PersonalMedia(service.store, user.id)


Media = Annotated[PersonalMedia, Depends(personal_media)]


class PhotoContent(BaseModel):
    content_base64: str
    width: int
    height: int


def read_image(service, image_id):
    asset = service.rpc("read", {"image_id": str(image_id)})
    return PhotoContent(
        content_base64=base64.b64encode(service._download(asset["object_path"])).decode("ascii"),
        width=asset["width"],
        height=asset["height"],
    )


@router.post("/profile-images/{purpose}", response_model=AssetView, status_code=201)
async def upload_personal(
    purpose: Literal["avatar", "evidence"], request: Request, service: Media, key: Key
):
    service.purpose = purpose
    return await upload_image(request, service, key)


@router.get("/profile-images/{image_id}", response_model=PhotoContent)
def personal_image(image_id: UUID, service: Media, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return read_image(service, image_id)


@router.delete("/profile-images/{image_id}")
def remove_personal(image_id: UUID, service: Media):
    return service.delete(image_id)


def profile_rpc(service, action, args):
    return service.store._request(
        "POST",
        "/rest/v1/rpc/core_profile_social",
        body={"action": action, "args": args},
        token=service.store.token,
    )


class LikeInput(StrictModel):
    liked: bool = Field(strict=True)


class LikesView(BaseModel):
    likes: int
    liked: bool


@router.get("/users/{user_id}/avatar", response_model=PhotoContent | None)
def user_avatar(user_id: UUID, service: Media, response: Response):
    response.headers["Cache-Control"] = "no-store"
    result = profile_rpc(service, "avatar", {"user_id": str(user_id)})
    return read_image(service, result["image_id"]) if result["image_id"] else None


@router.get("/users/{user_id}/likes", response_model=LikesView)
def user_likes(user_id: UUID, service: EngagementDep, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return profile_rpc(service, "get", {"user_id": str(user_id)})


@router.put("/users/{user_id}/likes", response_model=LikesView)
def like_user(user_id: UUID, value: LikeInput, service: EngagementDep):
    return profile_rpc(service, "like", {"user_id": str(user_id), "liked": value.liked})
