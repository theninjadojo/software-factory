"""The HTTP API: uvicorn shikumi_app.main:app"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from shikumi_app.api.middleware import HEADER, RequestIdMiddleware
from shikumi_app.api.routes import health, items
from shikumi_app.config import Settings, get_settings
from shikumi_app.db import make_engine, make_sessionmaker
from shikumi_app.domain.items import InvalidItemError
from shikumi_app.logs import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = make_engine(settings.database_url)
        app.state.sessions = make_sessionmaker(app.state.engine)
        yield
        app.state.engine.dispose()

    app = FastAPI(title="Shikumi App", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[HEADER],
    )
    app.add_middleware(RequestIdMiddleware)  # added last, so it runs first

    @app.exception_handler(InvalidItemError)
    async def invalid_item(_: Request, exc: InvalidItemError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    app.include_router(health.router)
    app.include_router(items.router)
    return app


app = create_app()
