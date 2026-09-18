from fastapi import APIRouter

from app.core.config import get_settings
from app.schemas.system import HealthResponse

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        service=settings.app_name,
        environment=settings.environment,
    )
