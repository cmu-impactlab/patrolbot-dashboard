"""Auth seam: resolves the current ``User`` for every API dependency.

auth_mode "local" (default) keeps the single seeded operator — the
Phases 1–4 behavior. auth_mode "oidc" requires a valid session cookie
issued by the /auth/callback flow (see oidc.py).
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request, WebSocket

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


def resolve_ws_user(settings, websocket: WebSocket) -> User | None:
    """The WebSocket equivalent of get_current_user: the seeded local operator
    in local mode, or the cookie-verified user in OIDC mode (None if unsigned)."""
    if settings.auth_mode != "oidc":
        return LOCAL_USER
    session = verify(settings.session_secret, websocket.cookies.get(COOKIE_NAME))
    return _user_from_session(session) if session is not None else None
