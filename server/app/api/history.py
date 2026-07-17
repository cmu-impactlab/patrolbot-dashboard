from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/history/battery")
async def battery_history(request: Request, minutes: int = 120) -> list[dict]:
    db = request.app.state.db
    return await db.get_battery_history(minutes=min(minutes, 7 * 24 * 60))


@router.get("/api/commands")
async def command_audit(request: Request, limit: int = 50) -> list[dict]:
    return await request.app.state.db.get_command_audit(limit=min(limit, 500))
