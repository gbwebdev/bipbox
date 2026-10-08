"""The bipbox server.

Routes are registered explicitly. telex used a catch-all
`@app.get("/{filename}")` that returned its send page for any unmatched path,
which would collide head-on with bipbox's `/{channel}` and `/{channel}/{device}`
pages and with `/admin` (architecture.md F4.2) — so there is no catch-all here,
and channel slugs are checked against a reserved list.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from . import db
from .config import Settings, get_settings
from .routers import health

log = logging.getLogger("bipbox")

# Slugs a channel may never take, because they are real routes. Checked when a
# channel is created rather than discovered as a routing collision later.
RESERVED_SLUGS = frozenset(
    {"admin", "api", "static", "health", "login", "logout", "assets", "favicon.ico", "robots.txt"}
)


def sqlite_path(database_url: str) -> Path | None:
    """The on-disk file a SQLite URL points at, or None if there isn't one."""
    if not database_url.startswith("sqlite"):
        return None
    _, _, tail = database_url.partition("///")
    if not tail or tail.startswith(":memory:"):
        return None
    return Path(tail)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings

    for warning in settings.production_warnings():
        log.warning("%s", warning)

    Path(settings.image_dir).mkdir(parents=True, exist_ok=True)
    if (path := sqlite_path(settings.database_url)) is not None:
        path.parent.mkdir(parents=True, exist_ok=True)

    db.init_engine(settings.database_url, echo=not settings.is_production)
    log.info("bipbox server ready (protocol v%d)", settings.protocol_version)
    try:
        yield
    finally:
        await db.dispose_engine()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title="bipbox",
        lifespan=lifespan,
        # No interactive docs: the admin surface is the thing we hardened with
        # TOTP, and a schema browser beside it is an invitation.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    # So routers can depend on get_settings() while tests inject their own.
    app.dependency_overrides[get_settings] = lambda: settings

    app.include_router(health.router)
    return app


app = create_app()
