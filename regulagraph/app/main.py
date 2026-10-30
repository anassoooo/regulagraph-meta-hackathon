import logging
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

from app.api import health, investigations
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.redaction import install_log_redaction

WEB_DIR = Path(__file__).resolve().parent / "web"

# Separate JS and CSS files only: no inline scripts or styles, no third-party origins.
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    install_log_redaction(settings)
    app = FastAPI(title=settings.app_name, version="0.3.0", docs_url=None, redoc_url=None, openapi_url=None)
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(investigations.router)

    @app.middleware("http")
    async def security_headers(request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


app = create_app()
