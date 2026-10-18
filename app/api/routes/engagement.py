from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from app.core_cloud.engagement import (
    Engagement,
    InboxInput,
    InboxPreference,
    InboxPreferences,
    PreferencesUpdate,
    PreferencesView,
    SavedPostInput,
    SavedPostPreference,
    SavedPostPreferences,
)
from app.core_cloud.store import SupabaseCoreStore
from app.core_local.dependencies import StoreDep, UserDep

router = APIRouter(prefix="/account", tags=["account"])


def engagement(store: StoreDep, _: UserDep, response: Response) -> Engagement:
    response.headers["Cache-Control"] = "no-store"
    if not isinstance(store, SupabaseCoreStore):
        raise HTTPException(503, "engagement_requires_supabase")
    return Engagement(store)


EngagementDep = Annotated[Engagement, Depends(engagement)]


@router.get("/preferences", response_model=PreferencesView)
def preferences(service: EngagementDep):
    return service.preferences()


@router.put("/preferences", response_model=PreferencesView)
def save_preferences(value: PreferencesUpdate, service: EngagementDep):
    return service.save_preferences(value)


@router.get("/inbox", response_model=InboxPreferences)
def inbox(service: EngagementDep):
    return service.inbox()


@router.put("/inbox/{post_id}", response_model=InboxPreference)
def save_inbox(post_id: UUID, value: InboxInput, service: EngagementDep):
    return service.save_inbox(post_id, value)


@router.get("/saved-posts", response_model=SavedPostPreferences)
def saved_posts(service: EngagementDep):
    return SavedPostPreferences.model_validate(
        service.store._request(
            "POST",
            "/rest/v1/rpc/core_saved_posts",
            body={"action": "list", "args": {}},
            token=service.store.token,
        )
    )


@router.put("/saved-posts/{post_id}", response_model=SavedPostPreference)
def save_post_preference(post_id: UUID, value: SavedPostInput, service: EngagementDep):
    return SavedPostPreference.model_validate(
        service.store._request(
            "POST",
            "/rest/v1/rpc/core_saved_posts",
            body={"action": "put", "args": {"post_id": str(post_id), **value.model_dump()}},
            token=service.store.token,
        )
    )
