from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.api.routes.health import router as health_router
from app.core.cloud_runtime import validate_cloud_settings
from app.core.cloud_security import CloudSecurity
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.stealth.service import StealthError


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    configure_logging(getattr(application.state, "cloud_settings", None))
    yield


async def validation_error_handler(_, exc: RequestValidationError) -> JSONResponse:
    # Validation errors must not echo passwords or private request bodies back to clients.
    errors = [
        {"loc": error["loc"], "msg": error["msg"], "type": error["type"]} for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": errors})


async def stealth_error_handler(_, exc: StealthError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"detail": exc.code})


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    cloud = settings.core_cloud_enabled or settings.environment == "cloud"
    if cloud:
        validate_cloud_settings(settings)
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        debug=settings.debug,
        lifespan=lifespan,
        docs_url=None if cloud else "/docs",
        redoc_url=None if cloud else "/redoc",
        openapi_url=None if cloud else "/openapi.json",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.backend_cors_origins,
        allow_credentials=not cloud,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"] if cloud else ["*"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"] if cloud else ["*"],
    )
    if cloud:
        application.state.cloud_settings = settings
        application.add_middleware(CloudSecurity, settings=settings)

        @application.get("/ready", include_in_schema=False)
        def cloud_readiness():
            return {"status": "configured", "database": "not_probed", "ai": "offline_rules"}

    application.include_router(health_router)
    application.include_router(api_router, prefix=settings.api_v1_prefix)
    application.add_exception_handler(RequestValidationError, validation_error_handler)
    application.add_exception_handler(StealthError, stealth_error_handler)
    return application


app = create_app()
