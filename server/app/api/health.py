from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/health")
async def health(request: Request) -> dict:
    hub = request.app.state.hub
    session = hub.primary()
    return {
        "status": "healthy",
        "robot_connected": session is not None and session.state.connection != "offline",
        "connection": session.state.connection if session else "offline",
        "browsers": len(hub.browsers),
    }


@router.get("/api/config")
async def config(request: Request) -> dict:
    settings = request.app.state.settings
    hub = request.app.state.hub
    session = hub.primary()
    map_data = session.state.map.data if session else None
    return {
        "robot_id": session.robot_id if session else settings.default_robot_id,
        "read_only": True,
        "protocol_version": 1,
        "map_version": map_data.map_version if map_data else 0,
        "connection_thresholds_s": {
            "online_under": settings.online_threshold_s,
            "offline_over": settings.offline_threshold_s,
        },
        "battery": {
            "low_percent": settings.battery_low_percent,
            "critical_percent": settings.battery_critical_percent,
        },
    }
