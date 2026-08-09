from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import health, help_guide, history, layouts, recordings, snapshot
from .authentication import oidc
from .database import create_database
from .settings import Settings, validate_startup
from .telemetry.hub import TelemetryHub
from .websocket import robot_gateway, ui_gateway

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

STATIC_CANDIDATES = ("static", "../frontend/dist")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    validate_startup(settings)  # fail closed on insecure production config

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = create_database(settings)
        await db.init()
        hub = TelemetryHub(settings, db)
        hub.set_event_seed(await db.next_event_id())
        await hub.start()
        app.state.settings = settings
        app.state.db = db
        app.state.hub = hub
        app.state.static_map = None
        if settings.static_map_yaml:
            from .telemetry.static_map import load_static_map

            try:
                app.state.static_map = load_static_map(
                    settings.static_map_yaml, settings.static_map_name)
            except Exception:
                logging.getLogger("static_map").exception(
                    "failed to load static map %s", settings.static_map_yaml)
        try:
            yield
        finally:
            await hub.stop()
            await db.close()

    app = FastAPI(title="PatrolBot Dashboard Server", lifespan=lifespan)

    if settings.auth_mode == "oidc":
        from fastapi.responses import JSONResponse

        from .authentication.sessions import COOKIE_NAME, verify

        @app.middleware("http")
        async def require_session(request, call_next):
            # /api/health stays open for monitoring; /auth/* is the login
            # flow itself; everything else under /api needs a session.
            path = request.url.path
            if path.startswith("/api/") and path != "/api/health":
                if verify(settings.session_secret, request.cookies.get(COOKIE_NAME)) is None:
                    return JSONResponse({"detail": "Not signed in."}, status_code=401)
            return await call_next(request)

    app.include_router(health.router)
    app.include_router(snapshot.router)
    app.include_router(history.router)
    app.include_router(help_guide.router)
    app.include_router(layouts.router)
    app.include_router(recordings.router)
    app.include_router(oidc.router)
    app.include_router(robot_gateway.router)
    app.include_router(ui_gateway.router)

    for candidate in STATIC_CANDIDATES:
        path = os.path.join(os.path.dirname(__file__), "..", candidate)
        if os.path.isdir(path):
            app.mount("/", StaticFiles(directory=path, html=True), name="static")
            break
    return app


app = create_app()
