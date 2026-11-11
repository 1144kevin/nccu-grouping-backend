import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.api.routes.health import router as health_router
from app.core.cloud_runtime import validate_cloud_settings
from app.core.cloud_security import CloudSecurity
from app.core.config import Settings, get_settings
from app.core.device_security import DeviceSecurity
from app.core.device_test import validate_device_settings
from app.core.logging import configure_logging
from app.stealth.service import StealthError


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    configure_logging(getattr(application.state, "cloud_settings", None))
    task = None
    application.state.web_push = None
    push_thread = None
    settings = application.state.runtime_settings
    if settings.core_supabase_enabled and settings.environment == "local":
        import threading

        from app.core_cloud.web_push import WebPushDelivery

        application.state.web_push = WebPushDelivery(settings)
        push_thread = threading.Thread(
            target=application.state.web_push.run, name="web-push", daemon=True
        )
        push_thread.start()
    if application.state.stealth_watch is not None:
        task = asyncio.create_task(application.state.stealth_watch.run())
    try:
        yield
    finally:
        if application.state.web_push:
            application.state.web_push.stopped.set()
        if push_thread:
            await asyncio.to_thread(push_thread.join, 2)
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


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
    validate_device_settings(settings)
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        debug=settings.debug,
        lifespan=lifespan,
        docs_url=None if cloud else "/docs",
        redoc_url=None if cloud else "/redoc",
        openapi_url=None if cloud else "/openapi.json",
    )
    application.state.runtime_settings = settings
    from app.core_cloud.stealth_watch import StealthWatch

    application.state.stealth_watch = (
        StealthWatch()
        if settings.core_supabase_enabled and settings.environment == "local"
        else None
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.backend_cors_origins,
        allow_credentials=not cloud,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"] if cloud else ["*"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"] if cloud else ["*"],
    )
    if settings.core_device_test_enabled:
        application.state.cloud_settings = settings
        application.add_middleware(DeviceSecurity, settings=settings)
    if cloud:
        application.state.cloud_settings = settings
        application.add_middleware(CloudSecurity, settings=settings)

        @application.get("/ready", include_in_schema=False)
        def cloud_readiness():
            return {
                "status": "configured",
                "database": "not_probed",
                "ai": settings.stealth_ai_provider,
            }

    application.include_router(health_router)
    application.include_router(api_router, prefix=settings.api_v1_prefix)
    application.add_exception_handler(RequestValidationError, validation_error_handler)
    application.add_exception_handler(StealthError, stealth_error_handler)
    return application


app = create_app()
