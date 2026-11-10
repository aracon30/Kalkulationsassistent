"""HETA Kalkulationsassistent – FastAPI-Anwendung.

Start: ``python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`` (siehe start.bat)
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import APP_ROOT, APP_VERSION, HOST, PORT, get_settings
from app.database import init_db
from app.routers import analysis, dashboard, import_center, offers, review, settings_router
from app.services.jobs import manager
from app.web import templates

ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]


class _SecretFilter(logging.Filter):
    """Verhindert, dass Zugangsdaten in Protokolle gelangen."""
    def filter(self, record: logging.LogRecord) -> bool:
        import os
        msg = record.getMessage()
        for key, value in os.environ.items():
            if value and len(value) >= 8 and any(t in key.upper() for t in ("KEY", "TOKEN", "SECRET", "PASSWORD")) and value in msg:
                record.msg = msg.replace(value, "***")
                record.args = ()
        return True


def setup_logging() -> None:
    settings = get_settings()
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return
    handler = RotatingFileHandler(settings.logs_dir / "app.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(_SecretFilter())
    root.addHandler(handler)
    root.setLevel(logging.INFO)


class LocalOriginMiddleware(BaseHTTPMiddleware):
    """Schutz gegen Cross-Site-Requests: schreibende Anfragen nur von der eigenen lokalen Oberfläche."""
    async def dispatch(self, request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin") or request.headers.get("referer")
            if origin:
                host = urlparse(origin).hostname
                if host not in ALLOWED_HOSTS:
                    return JSONResponse({"detail": "Anfrage von fremder Herkunft abgelehnt"}, status_code=403)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging()
    init_db()
    manager.recover_stale_jobs()
    logging.getLogger(__name__).info("HETA Kalkulationsassistent %s gestartet", APP_VERSION)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="HETA Kalkulationsassistent", version=APP_VERSION, lifespan=lifespan, docs_url="/api/docs", redoc_url=None)
    app.add_middleware(LocalOriginMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
    app.mount("/static", StaticFiles(directory=APP_ROOT / "app" / "static"), name="static")
    for r in (dashboard.router, import_center.router, offers.router, review.router, analysis.router, settings_router.router):
        app.include_router(r)

    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": getattr(exc, "detail", "Nicht gefunden")}, status_code=404)
        return templates.TemplateResponse(request, "error.html", {"title": "Nicht gefunden", "message": getattr(exc, "detail", "Seite nicht gefunden")}, status_code=404)

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=HOST, port=PORT)
