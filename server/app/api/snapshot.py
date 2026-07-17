from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.get("/api/snapshot")
async def snapshot(request: Request) -> dict:
    hub = request.app.state.hub
    session = hub.primary()
    if session is None:
        from ..telemetry.store import RobotState

        return RobotState(request.app.state.settings,
                          request.app.state.settings.default_robot_id).snapshot().model_dump(mode="json")
    return session.state.snapshot().model_dump(mode="json")


@router.get("/api/map")
async def get_map(request: Request) -> dict:
    hub = request.app.state.hub
    session = hub.primary()
    map_data = session.state.map.data if session else None
    if map_data is None:
        raise HTTPException(status_code=404, detail="No map received from the robot yet")
    return map_data.model_dump(mode="json")


@router.get("/api/events")
async def events(request: Request, limit: int = 100) -> list[dict]:
    db = request.app.state.db
    return await db.get_events(limit=min(limit, 500))
