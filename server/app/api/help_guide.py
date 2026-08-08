from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from ..authentication import User, get_current_user
from ..help_guide import HELP_GUIDE_VERSION

router = APIRouter()


def _status(seen_version: int) -> dict[str, int | bool]:
    return {
        "current_version": HELP_GUIDE_VERSION,
        "seen_version": seen_version,
        "should_prompt": seen_version < HELP_GUIDE_VERSION,
    }


@router.get("/api/help-guide/status")
async def get_help_guide_status(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> dict[str, int | bool]:
    seen = await request.app.state.db.get_help_guide_version_seen(user.id)
    return _status(seen)


@router.put("/api/help-guide/status")
async def acknowledge_help_guide(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> dict[str, int | bool]:
    seen = await request.app.state.db.mark_help_guide_seen(
        user.id, HELP_GUIDE_VERSION)
    return _status(seen)
