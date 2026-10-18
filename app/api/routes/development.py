"""Credential-free capability report for the current development connection."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.core.config import get_settings
from app.core_local.dependencies import require_local_mode

router = APIRouter(prefix="/development", tags=["development"])


@router.get("/readiness")
def readiness(
    response: Response, _: Annotated[None, Depends(require_local_mode)], request: Request = None
):
    settings = (
        getattr(request.state, "runtime_settings", None) if request else None
    ) or get_settings()
    response.headers["Cache-Control"] = "no-store"
    return {
        "profile": "cloud"
        if settings.core_cloud_enabled
        else "device_test"
        if settings.core_device_test_enabled
        else "loopback",
        "data_mode": "supabase" if settings.core_supabase_enabled else "sqlite",
        "google_ios_configured": settings.core_google_ios_enabled,
        "images_configured": settings.core_media_enabled,
        "ai_provider": settings.stealth_ai_provider,
        "voice_upload": False,
        "remote_push": False,
        "production_ready": False,
    }
