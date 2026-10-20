import logging

from app.core.config import Settings, get_settings


def configure_logging(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    logging.basicConfig(
        level=logging.DEBUG if settings.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if settings.core_cloud_enabled or settings.core_device_test_enabled:
        # HTTP client URLs can contain signed image tokens. Never log them in cloud mode.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
