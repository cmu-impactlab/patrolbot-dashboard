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
        email = idp.state.email or f"{idp.state.username}@andrew.cmu.edu"
        return {"sub": "abc123", "preferred_username": idp.state.username,
                "name": "Test User", "email": email,
                "email_verified": idp.state.email_verified}

    idp.state.username = "yousefh"
    idp.state.email = None  # None -> {username}@andrew.cmu.edu
    idp.state.email_verified = True
    return idp


class Idp:
    def __init__(self, url: str, app: FastAPI) -> None:
        self.url = url
        self.app = app

    def sign_in_as(self, username: str, email: str | None = None,
                   email_verified: bool = True) -> None:
        self.app.state.username = username
        self.app.state.email = email
        self.app.state.email_verified = email_verified


@pytest.fixture()
def idp():
    port = _free_port()
    app = make_idp()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    yield Idp(f"http://127.0.0.1:{port}", app)
    server.should_exit = True
    thread.join(timeout=5)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def oidc_settings(tmp_path, idp, **overrides) -> Settings:
    return Settings(
        database_path=str(tmp_path / "t.db"), auth_mode="oidc",
        session_secret="s3cret", oidc_issuer=idp.url,
        oidc_client_id="dash", oidc_client_secret="shh",
        oidc_redirect_url="http://testserver/auth/callback",
        **overrides,
    )


def sign_in(client: TestClient) -> object:
    login = client.get("/auth/login", follow_redirects=False)
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    return client.get(f"/auth/callback?code=test-code&state={state}", follow_redirects=False)


def test_full_oidc_flow(tmp_path, idp):
    settings = oidc_settings(tmp_path, idp, admin_usernames="yousefh")
    with TestClient(create_app(settings)) as client:
        # 1. /auth/login redirects to the IdP with PKCE + state.
        login = client.get("/auth/login", follow_redirects=False)
        assert login.status_code == 307
        query = parse_qs(urlparse(login.headers["location"]).query)
        assert query["code_challenge_method"] == ["S256"]
        state = query["state"][0]

        # 2. The IdP redirects back with a code; the callback exchanges it,
        #    fetches userinfo, creates the user, and sets the session cookie.
        callback = client.get(f"/auth/callback?code=test-code&state={state}",
                              follow_redirects=False)
        assert callback.status_code == 307
        assert callback.headers["location"] == "/"

        me = client.get("/auth/me").json()
        assert me["username"] == "yousefh"
        assert me["role"] == "administrator"  # in admin_usernames

        # 3. Authenticated API access works, per-user layouts included.
        assert client.get("/api/layouts").status_code == 200
        body = {"widgets": ["liveMap"], "layouts": {"lg": []}}
        assert client.put("/api/layouts/current", json=body).status_code == 200

        # 4. Logout clears the session.
        client.get("/auth/logout", follow_redirects=False)
        assert client.get("/auth/me").status_code == 401


def test_allowlist(tmp_path, idp):
    settings = oidc_settings(tmp_path, idp,
                             allowed_usernames="yousefh,efeoflus",
                             admin_usernames="yousefh",
                             operator_usernames="efeoflus")
    with TestClient(create_app(settings)) as client:
        # Both listed Andrew IDs get in with the right roles.
        idp.sign_in_as("yousefh")
        assert sign_in(client).status_code == 307
        assert client.get("/auth/me").json()["role"] == "administrator"
        client.get("/auth/logout")

        idp.sign_in_as("efeoflus")
        assert sign_in(client).status_code == 307
        assert client.get("/auth/me").json()["role"] == "operator"
        client.get("/auth/logout")

        # A valid CMU account NOT on the list authenticates at the IdP but
        # is refused here, with no session issued.
        idp.sign_in_as("stranger")
        denied = sign_in(client)
        assert denied.status_code == 403
        assert "Not authorized" in denied.text
        assert "stranger" in denied.text
        assert client.get("/auth/me").status_code == 401


def test_single_user_allowlist_admits_only_yousefh(tmp_path, idp):
    # Production intent: only yousefh@andrew.cmu.edu may sign in, as an admin.
    settings = oidc_settings(tmp_path, idp, allowed_usernames="yousefh",
                             admin_usernames="yousefh")
    with TestClient(create_app(settings)) as client:
        # yousefh gets in as administrator.
        idp.sign_in_as("yousefh", email="yousefh@andrew.cmu.edu")
        assert sign_in(client).status_code == 307
        assert client.get("/auth/me").json()["role"] == "administrator"
        client.get("/auth/logout")

        # Another perfectly valid andrew.cmu.edu account is refused — no session.
        idp.sign_in_as("efeoflus", email="efeoflus@andrew.cmu.edu")
        denied = sign_in(client)
        assert denied.status_code == 403
        assert "efeoflus" in denied.text
        assert client.get("/auth/me").status_code == 401


def test_email_style_username_is_normalized(tmp_path, idp):
    settings = oidc_settings(tmp_path, idp, allowed_usernames="yousefh")
    with TestClient(create_app(settings)) as client:
        idp.sign_in_as("YousefH@andrew.cmu.edu")
        assert sign_in(client).status_code == 307
        assert client.get("/auth/me").json()["username"] == "yousefh"


def test_callback_rejects_forged_state(tmp_path, idp):
    settings = oidc_settings(tmp_path, idp)
    with TestClient(create_app(settings)) as client:
        client.get("/auth/login", follow_redirects=False)
        assert client.get("/auth/callback?code=test-code&state=forged",
                          follow_redirects=False).status_code == 400


# -- Google domain restriction (@andrew.cmu.edu) ---------------------------

def test_login_uses_select_account_prompt(tmp_path, idp):
    settings = oidc_settings(tmp_path, idp)
    with TestClient(create_app(settings)) as client:
        login = client.get("/auth/login", follow_redirects=False)
        query = parse_qs(urlparse(login.headers["location"]).query)
        assert query["prompt"] == ["select_account"]


def test_accepts_configured_domain(tmp_path, idp):
    settings = oidc_settings(tmp_path, idp)  # default domain andrew.cmu.edu
    with TestClient(create_app(settings)) as client:
        idp.sign_in_as("newperson", email="newperson@andrew.cmu.edu")
        assert sign_in(client).status_code == 307
        assert client.get("/auth/me").json()["username"] == "newperson"


def test_rejects_foreign_domain(tmp_path, idp):
    settings = oidc_settings(tmp_path, idp)
    with TestClient(create_app(settings)) as client:
        idp.sign_in_as("outsider", email="outsider@gmail.com")
        denied = sign_in(client)
        assert denied.status_code == 403
        assert "Not authorized" in denied.text
        assert client.get("/auth/me").status_code == 401  # no session issued


@pytest.mark.parametrize("email", [
    "attacker@notandrew.cmu.edu",       # superdomain look-alike
    "attacker@andrew.cmu.edu.evil.com",  # subdomain suffix look-alike
    "attackerandrew.cmu.edu",            # missing @, endswith-only bypass attempt
    "attacker@ANDREW.CMU.EDU.evil.com",  # case variant of the above
])
def test_rejects_lookalike_domains(tmp_path, idp, email):
    settings = oidc_settings(tmp_path, idp)
    with TestClient(create_app(settings)) as client:
        idp.sign_in_as("attacker", email=email)
        assert sign_in(client).status_code == 403
        assert client.get("/auth/me").status_code == 401


def test_domain_check_is_case_insensitive(tmp_path, idp):
    settings = oidc_settings(tmp_path, idp)
    with TestClient(create_app(settings)) as client:
        idp.sign_in_as("mixedcase", email="MixedCase@Andrew.CMU.edu")
        assert sign_in(client).status_code == 307
        assert client.get("/auth/me").json()["username"] == "mixedcase"


def test_requires_verified_email(tmp_path, idp):
    settings = oidc_settings(tmp_path, idp)
    with TestClient(create_app(settings)) as client:
        idp.sign_in_as("spoofer", email="spoofer@andrew.cmu.edu",
                       email_verified=False)
        assert sign_in(client).status_code == 403
        assert client.get("/auth/me").status_code == 401


# -- Roles: read-only (observer) by default --------------------------------

def test_default_role_is_observer(tmp_path, idp):
    # No admin_usernames / operator_usernames configured: a valid andrew.cmu.edu
    # user gets in, but as a read-only observer.
    settings = oidc_settings(tmp_path, idp)
    with TestClient(create_app(settings)) as client:
        idp.sign_in_as("viewer", email="viewer@andrew.cmu.edu")
        assert sign_in(client).status_code == 307
        assert client.get("/auth/me").json()["role"] == "observer"


def test_operator_username_grants_operator(tmp_path, idp):
    settings = oidc_settings(tmp_path, idp, operator_usernames="driver")
    with TestClient(create_app(settings)) as client:
        idp.sign_in_as("driver", email="driver@andrew.cmu.edu")
        assert sign_in(client).status_code == 307
        assert client.get("/auth/me").json()["role"] == "operator"


# -- Fail-closed production startup & secure cookies -----------------------

def _prod_settings(tmp_path, **overrides) -> Settings:
    base = dict(
        database_path=str(tmp_path / "t.db"),
        environment="production", auth_mode="oidc",
        session_secret="a-real-secret", robot_token="a-real-robot-token",
        oidc_issuer="https://accounts.google.com", oidc_client_id="cid",
        oidc_client_secret="csecret",
        oidc_redirect_url="https://dash.example.edu/auth/callback",
        oidc_email_domain="andrew.cmu.edu",
        allowed_origins="https://dash.example.edu",
    )
    base.update(overrides)
    return Settings(**base)


def test_production_startup_ok(tmp_path):
    create_app(_prod_settings(tmp_path))  # a complete secure config must not raise


def test_development_tolerates_dev_defaults(tmp_path):
    # Default environment=development: dev secrets are not fatal.
    create_app(Settings(database_path=str(tmp_path / "t.db"), auth_mode="oidc",
                        session_secret="dev-session-secret"))


@pytest.mark.parametrize("overrides,needle", [
    ({"session_secret": "dev-session-secret"}, "SESSION_SECRET"),
    ({"robot_token": "dev-token"}, "ROBOT_TOKEN"),
    ({"allowed_origins": ""}, "ALLOWED_ORIGINS"),
    ({"oidc_client_secret": ""}, "OIDC_CLIENT_SECRET"),
    ({"auth_mode": "local"}, "AUTH_MODE"),
    ({"oidc_email_domain": "", "allowed_usernames": ""}, "sign in"),
    ({"oidc_redirect_url": "http://dash.example.edu/auth/callback"}, "https"),
])
def test_production_fails_closed(tmp_path, overrides, needle):
    with pytest.raises(RuntimeError, match=needle):
        create_app(_prod_settings(tmp_path, **overrides))


def test_cookie_secure_property():
    assert Settings(environment="production").cookie_secure is True
    assert Settings(oidc_redirect_url="https://x/cb").cookie_secure is True
    assert Settings().cookie_secure is False


def test_session_cookie_flags(tmp_path, idp):
    settings = oidc_settings(tmp_path, idp)  # dev over http
    with TestClient(create_app(settings)) as client:
        login = client.get("/auth/login", follow_redirects=False)
        state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
        cb = client.get(f"/auth/callback?code=test-code&state={state}",
                        follow_redirects=False)
        cookies = " ".join(cb.headers.get_list("set-cookie")).lower()
        assert "httponly" in cookies
        assert "samesite=lax" in cookies
        # Dev over http: NOT Secure, so the http TestClient still returns it.
        assert "secure" not in cookies
