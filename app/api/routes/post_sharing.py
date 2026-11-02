from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Response

from app.core_cloud.post_sharing import PostSharingService, SharedPost, ShareLink
from app.core_cloud.store import SupabaseCoreStore
from app.core_local.dependencies import StoreDep, UserDep

router = APIRouter(tags=["post-sharing"])


def sharing(store: StoreDep, response: Response) -> PostSharingService:
    response.headers["Cache-Control"] = "no-store"
    if not isinstance(store, SupabaseCoreStore):
        raise HTTPException(503, "post_sharing_requires_supabase")
    return PostSharingService(store)


Sharing = Annotated[PostSharingService, Depends(sharing)]
ShareToken = Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")]


@router.post("/posts/{post_id}/share", response_model=ShareLink)
def create_share(post_id: UUID, service: Sharing, user: UserDep):
    return service.create(post_id)


@router.get("/public/posts/{token}", response_model=SharedPost)
def shared_post(token: ShareToken, service: Sharing):
    return service.read(token)


@router.get("/public/posts/{token}/images/{image_id}")
def shared_image(token: ShareToken, image_id: UUID, service: Sharing):
    return Response(
        service.image(token, image_id),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )
