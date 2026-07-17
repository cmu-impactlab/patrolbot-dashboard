"""Stand-in OIDC identity provider for development.

Lets you exercise the dashboard's real sign-in flow (login page -> IdP ->
callback -> allowlist -> session) before CMU issues OIDC client credentials.
Instead of a password it shows a picker for which Andrew ID to "be" — so you
can try an allowed user (yousefh), the professor (efeoflus), and a stranger
to see the denial page.

Run:
    python scripts/dev_idp.py            # listens on :9100

Then start the server with:
    PATROLBOT_AUTH_MODE=oidc \
    PATROLBOT_OIDC_ISSUER=http://localhost:9100 \
    PATROLBOT_OIDC_CLIENT_ID=dashboard \
    PATROLBOT_OIDC_CLIENT_SECRET=dev-oidc-secret \
    PATROLBOT_OIDC_REDIRECT_URL=http://localhost:8000/auth/callback \
    PATROLBOT_ALLOWED_USERNAMES=yousefh,efeoflus \
    PATROLBOT_ADMIN_USERNAMES=yousefh \
    PATROLBOT_SESSION_SECRET=<random> ...

NEVER deploy this — it authenticates anyone as anyone, by design.
"""
import argparse
import secrets
from urllib.parse import urlencode

import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

app = FastAPI(title="Dev IdP (stand-in for CMU SSO)")
codes: dict[str, str] = {}
tokens: dict[str, str] = {}

PICKER = """<!doctype html><html><head><title>Dev sign-in (stand-in for CMU SSO)</title>
<style>body{{font-family:system-ui,sans-serif;display:grid;place-items:center;height:100vh;
margin:0;background:#f4f4f5}}form{{background:#fff;padding:32px 40px;border-radius:12px;
box-shadow:0 4px 18px rgb(0 0 0/.08);text-align:center}}input,button{{font:inherit;
padding:9px 14px;border-radius:8px;border:1px solid #ccc;margin-top:10px}}
button{{background:#c41230;color:#fff;border:none;cursor:pointer;margin-left:6px}}
.note{{color:#777;font-size:12px;max-width:320px;margin-top:14px}}</style></head><body>
<form method="post" action="/authorize">
<h2>Dev sign-in</h2>
<p>Stand-in for CMU SSO — enter the Andrew ID to sign in as.</p>
<input name="username" value="yousefh" autofocus>
<button type="submit">Sign in</button>
<input type="hidden" name="redirect_uri" value="{redirect_uri}">
<input type="hidden" name="state" value="{state}">
<p class="note">No password: this dev IdP authenticates anyone as anyone.
The dashboard's allowlist still decides who gets in.</p>
</form></body></html>"""


@app.get("/.well-known/openid-configuration")
def configuration(request: Request):
    base = str(request.base_url).rstrip("/")
    return {"issuer": base,
            "authorization_endpoint": f"{base}/authorize",
            "token_endpoint": f"{base}/token",
            "userinfo_endpoint": f"{base}/userinfo"}


@app.get("/authorize")
def authorize(redirect_uri: str, state: str = ""):
    return HTMLResponse(PICKER.format(redirect_uri=redirect_uri, state=state))


@app.post("/authorize")
def approve(username: str = Form(...), redirect_uri: str = Form(...), state: str = Form("")):
    code = secrets.token_urlsafe(16)
    codes[code] = username.strip() or "yousefh"
    return RedirectResponse(f"{redirect_uri}?{urlencode({'code': code, 'state': state})}",
                            status_code=303)


@app.post("/token")
def token(code: str = Form(...), grant_type: str = Form(""), redirect_uri: str = Form(""),
          client_id: str = Form(""), client_secret: str = Form(""),
          code_verifier: str = Form("")):
    username = codes.pop(code, None)
    if username is None:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    access = secrets.token_urlsafe(16)
    tokens[access] = username
    return {"access_token": access, "token_type": "Bearer"}


@app.get("/userinfo")
def userinfo(request: Request):
    access = request.headers.get("Authorization", "").removeprefix("Bearer ")
    username = tokens.get(access)
    if username is None:
        return JSONResponse({"error": "invalid_token"}, status_code=401)
    return {"sub": username, "preferred_username": username,
            "name": username, "email": f"{username}@andrew.cmu.edu"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9100)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
