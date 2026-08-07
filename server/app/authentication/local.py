"""Auth seam: resolves the current ``User`` for every API dependency.

auth_mode "local" (default) keeps the single seeded operator — the
Phases 1–4 behavior. auth_mode "oidc" requires a valid session cookie
issued by the /auth/callback flow (see oidc.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, WebSocket

from .sessions import COOKIE_NAME, verify


@dataclass(frozen=True)
class User:
    id: int
    username: str
    display_name: str
    role: str  # operator | observer | administrator

    @property
    def can_command(self) -> bool:
        """Command authorization is read-only by default; only operators and
        administrators may send navigation/pose/stop commands."""
        return self.role in ("operator", "administrator")

    @property
    def is_administrator(self) -> bool:
        return self.role == "administrator"


LOCAL_USER = User(id=1, username="local", display_name="Local Operator", role="administrator")


def _user_from_session(session: dict) -> User:
    return User(id=int(session["id"]), username=session["username"],
                display_name=session["display_name"], role=session["role"])


async def get_current_user(request: Request) -> User:
    settings = request.app.state.settings
    if settings.auth_mode != "oidc":
        return LOCAL_USER
    session = verify(settings.session_secret, request.cookies.get(COOKIE_NAME))
    if session is None:
        raise HTTPException(status_code=401, detail="Not signed in.")
    return _user_from_session(session)


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_operator(user: CurrentUser) -> User:
    """Actions that change what the robot or the dashboard is doing.

    The WebSocket command path has enforced this since the hardening work
    (CommandBroker checks `can_command`), but the HTTP surface did not: a
    signed-in observer could start and stop recordings, which are global —
    one observer stopping a recording ends it for everyone.
    """
    if not user.can_command:
        raise HTTPException(
            status_code=403,
            detail="Your account has read-only access — you cannot change recordings.")
    return user


async def require_administrator(user: CurrentUser) -> User:
    """Destructive actions and the command audit.

    The audit is not telemetry: it names who sent every command, from which IP
    address, which is exactly the data an observer account should not be able
    to enumerate. Deletion destroys a recording for every user at once.
    """
    if not user.is_administrator:
        raise HTTPException(
            status_code=403,
            detail="This action needs an administrator account.")
    return user


def resolve_ws_user(settings, websocket: WebSocket) -> User | None:
    """The WebSocket equivalent of get_current_user: the seeded local operator
    in local mode, or the cookie-verified user in OIDC mode (None if unsigned)."""
    if settings.auth_mode != "oidc":
        return LOCAL_USER
    session = verify(settings.session_secret, websocket.cookies.get(COOKIE_NAME))
    return _user_from_session(session) if session is not None else None
