import logging

from fastapi import FastAPI

from app.api import health
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.redaction import install_log_redaction


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    install_log_redaction(settings)
    app = FastAPI(title=settings.app_name, version="0.1.0")
    register_error_handlers(app)
    app.include_router(health.router)
    return app


app = create_app()
