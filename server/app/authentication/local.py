"""Auth seam: resolves the current ``User`` for every API dependency.

auth_mode "local" (default) keeps the single seeded operator — the
Phases 1–4 behavior. auth_mode "oidc" requires a valid session cookie
issued by the /auth/callback flow (see oidc.py).
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request

from .sessions import COOKIE_NAME, verify


@dataclass(frozen=True)
class User:
    id: int
    username: str
    display_name: str
    role: str  # operator | observer | administrator


LOCAL_USER = User(id=1, username="local", display_name="Local Operator", role="administrator")


async def get_current_user(request: Request) -> User:
    settings = request.app.state.settings
    if settings.auth_mode != "oidc":
        return LOCAL_USER
    session = verify(settings.session_secret, request.cookies.get(COOKIE_NAME))
    if session is None:
        raise HTTPException(status_code=401, detail="Not signed in.")
    return User(id=int(session["id"]), username=session["username"],
                display_name=session["display_name"], role=session["role"])
