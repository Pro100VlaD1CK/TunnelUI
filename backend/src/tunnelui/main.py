from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from tunnelui.api.routes import router
from tunnelui.config import Settings
from tunnelui.db import database
from tunnelui.domain.errors import DomainError
from tunnelui.services.auth import LoginLimiter
from tunnelui.services.sandbox import SandboxRuntime


def create_app(settings: Settings | None = None):
    settings = settings or Settings()
    engine, sessions = database(settings.database_url)

    @asynccontextmanager
    async def lifespan(_):
        if app.state.sandbox:
            app.state.sandbox.coordinator.recover_all()
        yield
        engine.dispose()

    app = FastAPI(title="TunnelUI", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.state.settings, app.state.sessions = settings, sessions
    app.state.engine, app.state.limiter = engine, LoginLimiter()
    app.state.sandbox = (
        SandboxRuntime(settings.sandbox_root, sessions, settings.master_key_file)
        if settings.development and settings.sandbox_root else None
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        return response

    @app.exception_handler(DomainError)
    async def domain_error(_, error: DomainError):
        return JSONResponse({"code": error.code}, status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_, error: RequestValidationError):
        # Pydantic's default detail includes input values, which can be passwords.
        return JSONResponse({"code": "validation_failed", "fields": [
            {"loc": e["loc"], "type": e["type"]} for e in error.errors()
        ]}, status_code=422)

    app.include_router(router)
    if (settings.static_dir / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=settings.static_dir / "assets"))

        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(settings.static_dir / "index.html")

    return app
