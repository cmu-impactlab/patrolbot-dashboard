"""Read-only tuning HTTP surface: authentication, errors and refusal."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.authentication import sessions
from app.authentication.sessions import COOKIE_NAME, issue
from app.main import create_app
from app.tuning.errors import (
    TuningConflict,
    TuningInvalid,
    TuningNotFound,
    TuningRateLimited,
    TuningRegistryError,
    TuningUnavailable,
)
from app.settings import Settings

from tuning_fixtures import change, new_uuid, save_request, snapshot

SECRET = "s3cret"
ROBOT = "patrolbot-01"
ROLES = {
    "observer": {"id": 10, "username": "obs", "display_name": "Obs", "role": "observer"},
    "operator": {"id": 11, "username": "opr", "display_name": "Opr", "role": "operator"},
    "administrator": {"id": 12, "username": "adm", "display_name": "Adm",
                      "role": "administrator"},
}
SNAPSHOT = snapshot()


def build_client(tmp_path, **overrides) -> TestClient:
    settings = Settings(database_path=str(tmp_path / "t.db"), auth_mode="oidc",
                        session_secret=SECRET, robot_token="test-token",
                        admin_usernames="adm", operator_usernames="opr", **overrides)
    return TestClient(create_app(settings))


@pytest.fixture()
def client(tmp_path):
    with build_client(tmp_path) as test_client:
        yield test_client


def as_role(client: TestClient, role: str, *, ttl: int = 300) -> TestClient:
    client.cookies.set(COOKIE_NAME, issue(SECRET, ROLES[role], ttl))
    return client


def broker(client: TestClient):
    return client.app.state.hub.tuning


# -- authentication ----------------------------------------------------------

def test_unauthenticated_is_401(client):
    assert client.get(f"/api/robots/{ROBOT}/tuning").status_code == 401
    assert client.post(f"/api/robots/{ROBOT}/tuning", json=save_request(
        [change("amcl.alpha1", 0.1)])).status_code == 401


def test_expired_cookie_is_401(client):
    as_role(client, "administrator", ttl=-10)
    assert client.get(f"/api/robots/{ROBOT}/tuning").status_code == 401


def test_any_authenticated_role_may_read(client, monkeypatch):
    async def fake_snapshot(user, robot_id):
        return SNAPSHOT

    monkeypatch.setattr(broker(client), "snapshot", fake_snapshot)
    for role in ROLES:
        response = as_role(client, role).get(f"/api/robots/{ROBOT}/tuning")
        assert response.status_code == 200
        assert response.json()["session_id"] == SNAPSHOT["session_id"]


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_tuning_mutation_route_is_absent_for_every_role(client, monkeypatch, role, method):
    async def must_not_dispatch(*args):
        pytest.fail("removed write reached the tuning broker")

    monkeypatch.setattr(broker(client), "request", must_not_dispatch)
    response = as_role(client, role).request(
        method, f"/api/robots/{ROBOT}/tuning", json=save_request([change("amcl.alpha1", 0.1)]))
    assert response.status_code == 405


# -- caches / error mapping --------------------------------------------------

@pytest.mark.parametrize("exc,status", [
    (TuningInvalid("bad"), 400),
    (TuningConflict("stale"), 409),
    (TuningRateLimited("busy"), 429),
    (TuningUnavailable("offline"), 503),
    (TuningRegistryError("no registry"), 503),
])
def test_snapshot_errors_map_to_status(client, monkeypatch, exc, status):
    async def boom(user, robot_id):
        raise exc

    monkeypatch.setattr(broker(client), "snapshot", boom)
    response = as_role(client, "observer").get(f"/api/robots/{ROBOT}/tuning")
    assert response.status_code == status
    assert response.headers.get("x-tuning-code") == exc.code


def test_unknown_snapshot_is_503_not_defaults(client, monkeypatch):
    async def unavailable(user, robot_id):
        raise TuningUnavailable("The robot has not reported tuning state yet.")

    monkeypatch.setattr(broker(client), "snapshot", unavailable)
    response = as_role(client, "observer").get(f"/api/robots/{ROBOT}/tuning")
    assert response.status_code == 503
    assert "detail" in response.json()


def test_missing_broker_is_503(client):
    client.app.state.hub.tuning = None
    assert as_role(client, "observer").get(f"/api/robots/{ROBOT}/tuning").status_code == 503


def test_invalid_robot_id_is_400(client):
    assert as_role(client, "observer").get("/api/robots/bad!/tuning").status_code == 400


# -- jobs --------------------------------------------------------------------

def test_job_endpoint_returns_robot_job(client, monkeypatch):
    async def fake_job(user, robot_id, job_id):
        return {"job_id": job_id, "state": "completed"}

    monkeypatch.setattr(broker(client), "job", fake_job)
    response = as_role(client, "observer").get(
        f"/api/robots/{ROBOT}/tuning/jobs/{new_uuid()}")
    assert response.status_code == 200
    assert response.json()["job"]["state"] == "completed"


def test_unknown_job_is_404(client, monkeypatch):
    async def missing(user, robot_id, job_id):
        raise TuningNotFound("no such job")

    monkeypatch.setattr(broker(client), "job", missing)
    response = as_role(client, "observer").get(
        f"/api/robots/{ROBOT}/tuning/jobs/{new_uuid()}")
    assert response.status_code == 404


# -- browser WebSocket: no tuning channel, fresh auth per message ------------

def test_tuning_frames_over_ws_ui_are_refused(client, monkeypatch):
    calls = []

    class Recorder:
        available = True

        async def handle_response(self, *args, **kwargs):
            calls.append("handle_response")

    monkeypatch.setattr(client.app.state.hub, "tuning", Recorder())
    as_role(client, "administrator")  # a valid session is required to connect at all
    with client.websocket_connect("/ws/ui") as ui:
        ui.receive_text()  # server.snapshot
        ui.send_text(json.dumps({
            "version": 1, "type": "tuning.request", "robot_id": ROBOT,
            "sequence": 1, "timestamp": "now",
            "data": {"request": save_request([change("amcl.alpha1", 0.1)]),
                     "actor": ROLES["administrator"]},
        }))
        # The frame is dropped, never routed to the broker, and the socket
        # survives: another tuning frame plus a health check both succeed.
        ui.send_text(json.dumps({"version": 1, "type": "tuning.response",
                                 "robot_id": ROBOT, "sequence": 2,
                                 "timestamp": "now", "data": {}}))
        assert as_role(client, "administrator").get("/api/health").status_code == 200
    assert calls == []


def test_ws_message_with_expired_session_is_rejected(client, monkeypatch):
    clock = [1_000_000.0]
    monkeypatch.setattr(sessions, "time", SimpleNamespace(time=lambda: clock[0]))
    client.cookies.set(COOKIE_NAME, issue(SECRET, ROLES["administrator"], ttl_s=5))
    with client.websocket_connect("/ws/ui") as ui:
        ui.receive_text()  # snapshot (session still valid at connect)
        clock[0] += 10  # the cookie now expired mid-connection
        ui.send_text(json.dumps({
            "version": 1, "type": "tuning.request", "robot_id": ROBOT,
            "sequence": 1, "timestamp": "now", "data": {},
        }))
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ui.receive_text()
        assert excinfo.value.code == 4401
