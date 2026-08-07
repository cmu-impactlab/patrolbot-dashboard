from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from ..authentication import User, require_administrator

router = APIRouter()

# The command audit names every operator who sent a command and the address
# they sent it from. That is access data about colleagues, not robot telemetry,
# so it is administrator-only rather than "any signed-in account".
Administrator = Annotated[User, Depends(require_administrator)]

# Upper bounds were already enforced with min(); the lower bounds matter just
# as much. A negative LIMIT means "no limit" in SQLite and is an error in
# PostgreSQL, and a negative window inverts the time range — so the bounds are
# declared on the parameter and rejected with 422 before they reach a query.
MAX_HISTORY_MINUTES = 7 * 24 * 60
MAX_COMMAND_AUDIT = 500


@router.get("/api/history/battery")
async def battery_history(
    request: Request,
    minutes: int = Query(default=120, ge=1, le=MAX_HISTORY_MINUTES),
) -> list[dict]:
    db = request.app.state.db
    return await db.get_battery_history(minutes=minutes)


@router.get("/api/commands")
async def command_audit(
    request: Request,
    user: Administrator,
    limit: int = Query(default=50, ge=1, le=MAX_COMMAND_AUDIT),
) -> list[dict]:
    return await request.app.state.db.get_command_audit(limit=limit)
