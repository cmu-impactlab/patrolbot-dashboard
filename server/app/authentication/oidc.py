"""OpenID Connect authorization-code flow with PKCE (Phase 5).

Provider-agnostic: point PATROLBOT_OIDC_ISSUER at CMU's IdP (or any OIDC
provider), register PATROLBOT_OIDC_REDIRECT_URL as the callback, and set the
client id/secret. Roles: usernames in PATROLBOT_ADMIN_USERNAMES become
administrators, everyone else operators.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..settings import Settings
from .sessions import COOKIE_NAME, issue, verify

log = logging.getLogger("auth.oidc")
router = APIRouter()

_STATE_TTL_S = 600


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


async def _discover(issuer: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(issuer.rstrip("/") + "/.well-known/openid-configuration")
        response.raise_for_status()
        return response.json()


def _role_for(settings: Settings, username: str) -> str:
    admins = {name.strip() for name in settings.admin_usernames.split(",") if name.strip()}
    return "administrator" if username in admins else "operator"


@router.get("/auth/login")
async def login(request: Request):
    settings: Settings = request.app.state.settings
    config = await _discover(settings.oidc_issuer)
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)
    query = urlencode({
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": settings.oidc_redirect_url,
        "scope": "openid profile email",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    response = RedirectResponse(f"{config['authorization_endpoint']}?{query}")
    # The verifier/state round-trip through a short-lived signed cookie, so
    # no server-side state is needed.
    response.set_cookie("patrolbot_oidc", issue(settings.session_secret,
                        {"state": state, "verifier": verifier}, _STATE_TTL_S),
                        httponly=True, max_age=_STATE_TTL_S, path="/auth")
    return response


@router.get("/auth/callback")
async def callback(request: Request, code: str = "", state: str = ""):
    settings: Settings = request.app.state.settings
    stashed = verify(settings.session_secret, request.cookies.get("patrolbot_oidc"))
    if not code or stashed is None or stashed.get("state") != state:
        raise HTTPException(status_code=400, detail="Login attempt expired — try again.")

    config = await _discover(settings.oidc_issuer)
    async with httpx.AsyncClient(timeout=10) as client:
        token_response = await client.post(config["token_endpoint"], data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.oidc_redirect_url,
            "client_id": settings.oidc_client_id,
            "client_secret": settings.oidc_client_secret,
            "code_verifier": stashed["verifier"],
        })
        token_response.raise_for_status()
        access_token = token_response.json()["access_token"]
        userinfo_response = await client.get(config["userinfo_endpoint"],
                                             headers={"Authorization": f"Bearer {access_token}"})
        userinfo_response.raise_for_status()
        info = userinfo_response.json()

    username = info.get("preferred_username") or info.get("email") or info["sub"]
    display_name = info.get("name") or username
    user_id = await request.app.state.db.get_or_create_user(username, display_name)
    log.info("user %s logged in (id=%s)", username, user_id)

    response = RedirectResponse("/")
    response.set_cookie(COOKIE_NAME, issue(settings.session_secret, {
        "id": user_id, "username": username, "display_name": display_name,
        "role": _role_for(settings, username),
    }, settings.session_ttl_s), httponly=True, max_age=settings.session_ttl_s, path="/")
    response.delete_cookie("patrolbot_oidc", path="/auth")
    return response


@router.get("/auth/logout")
async def logout():
    response = RedirectResponse("/")
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@router.get("/auth/me")
async def me(request: Request) -> dict:
    settings: Settings = request.app.state.settings
    if settings.auth_mode != "oidc":
        from .local import LOCAL_USER

        return {"id": LOCAL_USER.id, "username": LOCAL_USER.username,
                "display_name": LOCAL_USER.display_name, "role": LOCAL_USER.role,
                "auth_mode": "local"}
    session = verify(settings.session_secret, request.cookies.get(COOKIE_NAME))
    if session is None:
        raise HTTPException(status_code=401, detail="Not signed in.")
    return {**{k: session[k] for k in ("id", "username", "display_name", "role")},
            "auth_mode": "oidc"}
