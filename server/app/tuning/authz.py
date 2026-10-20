"""Server-side actor derivation and fresh-credential checks for tuning.

The browser never supplies role or authorization. The actor stamped onto every
``tuning.request`` is derived here from the verified session, and mutations
re-check that the credential is still valid *and* currently administrative at
the moment of admission — an HTTP request is re-verified per request, and the
browser WebSocket re-verifies per tuning frame (see ``websocket/ui_gateway``).
"""
from __future__ import annotations

from typing import Any

from ..authentication.local import User
from ..authentication.sessions import COOKIE_NAME
from ..authentication.sessions import verify as verify_session
from .errors import TuningForbidden, TuningInvalid
from .validation import MAX_USERNAME

ROLES = ("administrator", "operator", "observer")


def actor_from_user(user: User) -> dict[str, Any]:
    """Build the server-derived actor block; reject an unknown role shape."""
    role = getattr(user, "role", None)
    if role not in ROLES:
        raise TuningForbidden("Your account role is not recognized for tuning.")
    username = getattr(user, "username", None)
    if not isinstance(username, str) or not 1 <= len(username) <= MAX_USERNAME:
        raise TuningForbidden("Your account identity is not usable for tuning.")
    user_id = getattr(user, "id", None)
    if isinstance(user_id, bool) or not isinstance(user_id, int):
        raise TuningForbidden("Your account identity is not usable for tuning.")
    return {"id": user_id, "username": username, "role": role}


def user_from_session(settings: Any, session: dict[str, Any] | None) -> User | None:
    """Re-derive a User from a freshly verified session payload."""
    if session is None:
        return None
    try:
        from ..authentication.oidc import _is_allowed, _role_for
        username = session["username"]
        if not _is_allowed(settings, username):
            return None
        return User(id=int(session["id"]), username=session["username"],
                    display_name=session.get("display_name", session["username"]),
                    role=_role_for(settings, username))
    except (KeyError, TypeError, ValueError):
        return None


def fresh_user(settings: Any, cookies: Any) -> User | None:
    """Re-verify the signed session cookie (signature *and* expiry) now.

    In local mode this is the seeded local administrator. In OIDC mode an
    expired or tampered cookie returns ``None`` even if it was accepted when a
    long-lived WebSocket first connected.
    """
    if settings.auth_mode != "oidc":
        from ..authentication.local import LOCAL_USER

        return LOCAL_USER
    session = verify_session(settings.session_secret, cookies.get(COOKIE_NAME))
    return user_from_session(settings, session)


def is_mutation(operation: Any) -> bool:
    from .validation import MUTATION_OPERATIONS

    return operation in MUTATION_OPERATIONS


def require_administrator(user: User, operation: Any) -> None:
    """Every mutation requires a currently-administrative account."""
    if is_mutation(operation) and not getattr(user, "is_administrator", False):
        raise TuningForbidden(
            "Changing robot tuning needs an administrator account.")


def validate_actor_shape(actor: dict[str, Any]) -> None:
    if not isinstance(actor, dict) or set(actor) != {"id", "username", "role"}:
        raise TuningInvalid("The derived actor block is malformed.")
