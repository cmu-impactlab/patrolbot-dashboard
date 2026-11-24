"""Authenticated, read-only HTTP surface for robot tuning.

* ``GET  /api/robots/{robot_id}/tuning``            -> runtime snapshot (read RPC)
* ``GET  /api/robots/{robot_id}/tuning/jobs/{id}``  -> historical job record
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..authentication.local import CurrentUser, User
from ..tuning.authz import fresh_user
from ..tuning.broker import TuningBroker
from ..tuning.errors import TuningError

router = APIRouter()

_ROBOT_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _robot_id(robot_id: str) -> str:
    if not _ROBOT_ID.match(robot_id):
        raise HTTPException(status_code=400, detail="Invalid robot id.")
    return robot_id


def _broker(request: Request) -> TuningBroker:
    broker = getattr(request.app.state.hub, "tuning", None)
    if broker is None:
        raise HTTPException(
            status_code=503,
            detail="Robot tuning is not available on this server.")
    return broker


def _http_error(exc: TuningError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail,
                         headers={"x-tuning-code": exc.code})


async def current_tuning_user(request: Request, user: CurrentUser) -> User:
    if request.app.state.settings.auth_mode == "oidc":
        current = fresh_user(request.app.state.settings, request.cookies)
        if current is None:
            raise HTTPException(status_code=401, detail="Not signed in.")
        return current
    return user


@router.get("/api/robots/{robot_id}/tuning")
async def get_tuning(robot_id: str, request: Request,
                     user: User = Depends(current_tuning_user)) -> dict[str, Any]:
    """Read the runtime snapshot. Any authenticated role may read."""
    _robot_id(robot_id)
    broker = _broker(request)
    try:
        return await broker.snapshot(user, robot_id)
    except TuningError as exc:
        raise _http_error(exc) from exc


@router.get("/api/robots/{robot_id}/tuning/jobs/{job_id}")
async def get_tuning_job(robot_id: str, job_id: str, request: Request,
                          user: User = Depends(current_tuning_user)) -> dict[str, Any]:
    """Query one durable job id on the robot."""
    _robot_id(robot_id)
    broker = _broker(request)
    try:
        job = await broker.job(user, robot_id, job_id)
    except TuningError as exc:
        raise _http_error(exc) from exc
    return {"job": job}
