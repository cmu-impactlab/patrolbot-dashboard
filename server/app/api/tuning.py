"""Authenticated HTTP surface for robot tuning.

* ``GET  /api/robots/{robot_id}/tuning``            -> runtime snapshot (read RPC)
* ``POST /api/robots/{robot_id}/tuning``            -> admin-only canonical request
* ``GET  /api/robots/{robot_id}/tuning/jobs/{id}``  -> durable job queried on robot

The browser body is the canonical contract request only. The actor/role is
derived server-side; a client cannot assert privilege by adding fields (strict
validation rejects unknown fields) and the endpoints are permission-checked no
matter what the UI shows. Mutation POSTs are cross-origin checked and require a
currently-administrative session, re-verified on every request.
"""
from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..authentication.local import CurrentUser, User
from ..tuning.authz import fresh_user
from ..tuning.broker import TuningBroker
from ..tuning.errors import TuningError
from ..tuning.validation import MUTATION_OPERATIONS, REQUEST_MAX_BYTES

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


def _origin_allowed(request: Request) -> bool:
    settings = request.app.state.settings
    allowed = {o.strip() for o in settings.allowed_origins.split(",") if o.strip()}
    if not allowed:
        return True
    return request.headers.get("origin") in allowed


def _http_error(exc: TuningError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail,
                         headers={"x-tuning-code": exc.code})


async def _bounded_body(request: Request) -> bytes:
    length = request.headers.get("content-length")
    if length is not None:
        try:
            size = int(length)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Content-Length.") from exc
        if size > REQUEST_MAX_BYTES:
            raise HTTPException(status_code=413, detail="The tuning request is too large.")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > REQUEST_MAX_BYTES:
            raise HTTPException(status_code=413, detail="The tuning request is too large.")
        body.extend(chunk)
    return bytes(body)


async def current_tuning_user(request: Request, user: CurrentUser) -> User:
    if request.app.state.settings.auth_mode == "oidc":
        current = fresh_user(request.app.state.settings, request.cookies)
        if current is None:
            raise HTTPException(status_code=401, detail="Not signed in.")
        return current
    return user


async def administrator(user: User = Depends(current_tuning_user)) -> User:
    if not user.is_administrator:
        raise HTTPException(status_code=403, detail="This action needs an administrator account.")
    return user


def _parse_body(body: bytes) -> Any:
    def _reject_constant(name: str) -> Any:
        raise ValueError(name)

    def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        seen: dict[str, Any] = {}
        for key, value in pairs:
            if key in seen:
                raise ValueError(f"duplicate key {key!r}")
            seen[key] = value
        return seen

    try:
        return json.loads(body, parse_constant=_reject_constant,
                          object_pairs_hook=_reject_duplicate_keys)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise HTTPException(
            status_code=400,
            detail="The tuning request is not strict JSON.") from exc


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


@router.post("/api/robots/{robot_id}/tuning")
async def post_tuning(robot_id: str, request: Request,
                       user: User = Depends(administrator)) -> dict[str, Any]:
    """Forward one admin-authorized canonical tuning request.

    POST carries a change (save/apply/redeploy/restore). Reads live on GET.
    """
    _robot_id(robot_id)
    if not _origin_allowed(request):
        raise HTTPException(status_code=403, detail="Origin not allowed.")
    payload = _parse_body(await _bounded_body(request))
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="The tuning request must be an object.")
    operation = payload.get("operation")
    if operation not in MUTATION_OPERATIONS:
        raise HTTPException(
            status_code=400,
            detail="POST is for tuning changes; use GET to read tuning state.")
    # Body streaming can outlive the login credential. Revalidate immediately
    # before forwarding, not just at the HTTP dependency's initial resolution.
    user = await administrator(await current_tuning_user(request, user))
    broker = _broker(request)
    try:
        response = await broker.request(user, robot_id, payload)
    except TuningError as exc:
        raise _http_error(exc) from exc
    return response.as_dict()


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
