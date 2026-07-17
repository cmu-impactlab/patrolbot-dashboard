"""Auth seam tests: local mode passthrough, session signing, and the full
OIDC code+PKCE flow against a fake IdP served on a real local port."""
import socket
import threading
import time
from urllib.parse import parse_qs, urlparse

import pytest
import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.authentication.sessions import issue, verify
from app.main import create_app
from app.settings import Settings


def test_local_mode_needs_no_session(tmp_path):
    settings = Settings(database_path=str(tmp_path / "t.db"))
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/layouts").status_code == 200
        me = client.get("/auth/me").json()
        assert me["username"] == "local" and me["auth_mode"] == "local"


def test_oidc_mode_rejects_without_session(tmp_path):
    settings = Settings(database_path=str(tmp_path / "t.db"), auth_mode="oidc",
                        session_secret="s3cret")
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/layouts").status_code == 401
        assert client.get("/auth/me").status_code == 401
        # Health/snapshot stay open (robot + monitoring, not user data).
        assert client.get("/api/health").status_code == 200


def test_session_tokens():
    user = {"id": 2, "username": "y", "display_name": "Y", "role": "operator"}
    token = issue("k", user, 60)
    assert verify("k", token)["username"] == "y"
    assert verify("wrong", token) is None
    assert verify("k", token[:-2]) is None
    assert verify("k", issue("k", user, -5)) is None  # expired


# -- full flow against a fake IdP ------------------------------------------

def make_idp() -> FastAPI:
    idp = FastAPI()
    issued: dict[str, str] = {}

    @idp.get("/.well-known/openid-configuration")
    def config(request: Request):
        base = str(request.base_url).rstrip("/")
        return {"authorization_endpoint": f"{base}/authorize",
                "token_endpoint": f"{base}/token",
                "userinfo_endpoint": f"{base}/userinfo"}

    @idp.post("/token")
    def token(code: str = Form(...), code_verifier: str = Form(...),
              client_id: str = Form(...), client_secret: str = Form(...),
              grant_type: str = Form(...), redirect_uri: str = Form(...)):
        if code != "test-code" or client_secret != "shh":
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        issued["verifier"] = code_verifier
        return {"access_token": "test-access"}

    @idp.get("/userinfo")
    def userinfo(request: Request):
        if request.headers.get("Authorization") != "Bearer test-access":
            return JSONResponse({"error": "bad token"}, status_code=401)
        return {"sub": "abc123", "preferred_username": "ymh1874",
                "name": "Yousef H", "email": "ymh1874@cmu.edu"}

    return idp


@pytest.fixture()
def idp_url():
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(make_idp(), host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_full_oidc_flow(tmp_path, idp_url):
    settings = Settings(
        database_path=str(tmp_path / "t.db"), auth_mode="oidc",
        session_secret="s3cret", oidc_issuer=idp_url,
        oidc_client_id="dash", oidc_client_secret="shh",
        oidc_redirect_url="http://testserver/auth/callback",
        admin_usernames="ymh1874",
    )
    with TestClient(create_app(settings)) as client:
        # 1. /auth/login redirects to the IdP with PKCE + state.
        login = client.get("/auth/login", follow_redirects=False)
        assert login.status_code == 307
        location = urlparse(login.headers["location"])
        query = parse_qs(location.query)
        assert query["code_challenge_method"] == ["S256"]
        state = query["state"][0]

        # 2. The IdP redirects back with a code; the callback exchanges it,
        #    fetches userinfo, creates the user, and sets the session cookie.
        callback = client.get(f"/auth/callback?code=test-code&state={state}",
                              follow_redirects=False)
        assert callback.status_code == 307
        assert callback.headers["location"] == "/"

        me = client.get("/auth/me").json()
        assert me["username"] == "ymh1874"
        assert me["role"] == "administrator"  # in admin_usernames

        # 3. Authenticated API access works, per-user layouts included.
        assert client.get("/api/layouts").status_code == 200
        body = {"widgets": ["liveMap"], "layouts": {"lg": []}}
        assert client.put("/api/layouts/current", json=body).status_code == 200

        # 4. Logout clears the session.
        client.get("/auth/logout", follow_redirects=False)
        assert client.get("/auth/me").status_code == 401


def test_callback_rejects_forged_state(tmp_path, idp_url):
    settings = Settings(
        database_path=str(tmp_path / "t.db"), auth_mode="oidc",
        session_secret="s3cret", oidc_issuer=idp_url,
        oidc_client_id="dash", oidc_client_secret="shh",
        oidc_redirect_url="http://testserver/auth/callback",
    )
    with TestClient(create_app(settings)) as client:
        client.get("/auth/login", follow_redirects=False)
        assert client.get("/auth/callback?code=test-code&state=forged",
                          follow_redirects=False).status_code == 400
