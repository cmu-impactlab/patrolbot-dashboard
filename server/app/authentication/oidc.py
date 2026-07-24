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
from fastapi.responses import HTMLResponse, RedirectResponse

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
    """Command authorization is read-only by default: an authenticated user is
    an observer unless explicitly listed as an operator or administrator."""
    admins = {name.strip() for name in settings.admin_usernames.split(",") if name.strip()}
    operators = {name.strip() for name in settings.operator_usernames.split(",") if name.strip()}
    if username in admins:
        return "administrator"
    if username in operators:
        return "operator"
    return "observer"


def _is_allowed(settings: Settings, username: str) -> bool:
    """Authorization on top of authentication: with an allowlist configured,
    a valid Andrew ID is not enough — it must also be on the list."""
    allowed = {name.strip() for name in settings.allowed_usernames.split(",") if name.strip()}
    return not allowed or username in allowed


def _domain_ok(settings: Settings, email: str | None, email_verified: object) -> bool:
    """Google returns the Workspace email; require a *verified* address ending
    in "@<oidc_email_domain>". The leading "@" anchor rejects look-alikes such
    as ...@notandrew.cmu.edu and ...@andrew.cmu.edu.evil.com."""
    domain = settings.oidc_email_domain.strip().lower()
    if not domain:
        return True  # check disabled (production startup refuses this — see main.py)
    if not email:
        return False
    verified = email_verified is True or str(email_verified).lower() == "true"
    return verified and email.strip().lower().endswith("@" + domain)


_DENIED_PAGE = """<!doctype html><html><head><title>Not authorized</title>
<style>body{{font-family:system-ui,sans-serif;display:grid;place-items:center;
height:100vh;margin:0;background:#f4f4f5;color:#1a1a1c}}
.card{{background:#fff;border-radius:12px;padding:36px 44px;max-width:430px;
box-shadow:0 4px 18px rgb(0 0 0/.08);text-align:center}}
.mark{{width:44px;height:44px;border-radius:9px;background:#c41230;color:#fff;
display:inline-grid;place-items:center;font-weight:800;margin-bottom:14px}}
a{{color:#c41230}}</style></head><body><div class="card">
<div class="mark">PB</div><h2>Not authorized</h2>
<p>You signed in as <b>{username}</b>, but this account has not been given
access to the PatrolBot dashboard.</p>
<p>If you believe you should have access, contact the dashboard
administrator.</p><p><a href="/auth/logout">Sign out</a></p>
</div></body></html>"""


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
        # Force Google's account chooser so a shared machine can't silently
        # reuse a previous session (matches the CMU-Q oauth-example).
        "prompt": "select_account",
    })
    response = RedirectResponse(f"{config['authorization_endpoint']}?{query}")
    # The verifier/state round-trip through a short-lived signed cookie, so
    # no server-side state is needed.
    response.set_cookie("patrolbot_oidc", issue(settings.session_secret,
                        {"state": state, "verifier": verifier}, _STATE_TTL_S),
                        httponly=True, secure=settings.cookie_secure, samesite="lax",
                        max_age=_STATE_TTL_S, path="/auth")
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

    # Access gate: a verified email in the configured domain. This is the
    # primary authorization (the FastAPI equivalent of the oauth-example's
    # request.user.email.endswith("@andrew.cmu.edu") check).
    email = info.get("email")
    if not _domain_ok(settings, email, info.get("email_verified")):
        shown = email or info.get("preferred_username") or info.get("sub", "unknown")
        log.warning("user %s rejected: email not a verified %s address",
                    shown, settings.oidc_email_domain)
        return HTMLResponse(_DENIED_PAGE.format(username=shown), status_code=403)

    username = info.get("preferred_username") or email or info["sub"]
    # Andrew IDs may arrive as andrewid@andrew.cmu.edu — compare the local part.
    username = username.split("@", 1)[0].lower()
    display_name = info.get("name") or username
    if not _is_allowed(settings, username):
        log.warning("user %s authenticated but is not on the allowlist", username)
        return HTMLResponse(_DENIED_PAGE.format(username=username), status_code=403)
    user_id = await request.app.state.db.get_or_create_user(username, display_name)
    log.info("user %s logged in (id=%s)", username, user_id)

    # A fresh session cookie is issued here (session rotation on login).
    response = RedirectResponse("/")
    response.set_cookie(COOKIE_NAME, issue(settings.session_secret, {
        "id": user_id, "username": username, "display_name": display_name,
        "role": _role_for(settings, username),
    }, settings.session_ttl_s), httponly=True, secure=settings.cookie_secure,
        samesite="lax", max_age=settings.session_ttl_s, path="/")
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
