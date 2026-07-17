from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import health, history, layouts, snapshot
from .database.repo import Database
from .settings import Settings
from .telemetry.hub import TelemetryHub
from .websocket import robot_gateway, ui_gateway

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

STATIC_CANDIDATES = ("static", "../frontend/dist")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.database_path)
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
    app.include_router(health.router)
    app.include_router(snapshot.router)
    app.include_router(history.router)
    app.include_router(layouts.router)
    app.include_router(robot_gateway.router)
    app.include_router(ui_gateway.router)

    for candidate in STATIC_CANDIDATES:
        path = os.path.join(os.path.dirname(__file__), "..", candidate)
        if os.path.isdir(path):
            app.mount("/", StaticFiles(directory=path, html=True), name="static")
            break
    return app


app = create_app()
