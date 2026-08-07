"""Role authorization on the HTTP surface.

The WebSocket command path has checked `can_command` since the command-path
hardening, but the REST routes only checked that *a* session existed. That left
the observer role — documented as telemetry-only — able to start, stop and
delete recordings (which are global: stopping one ends it for every operator
watching) and to read the full command audit, including every operator's
username and source IP.

Each route is checked from all three roles, so a later refactor that drops a
dependency fails here rather than silently widening access.
"""
import pytest
from fastapi.testclient import TestClient

from app.authentication.sessions import COOKIE_NAME, issue
from app.main import create_app
from app.settings import Settings

SECRET = "s3cret"

ROLES = {
    "observer": {"id": 10, "username": "obs", "display_name": "Obs", "role": "observer"},
    "operator": {"id": 11, "username": "opr", "display_name": "Opr", "role": "operator"},
    "administrator": {"id": 12, "username": "adm", "display_name": "Adm",
                      "role": "administrator"},
}


@pytest.fixture()
def client(tmp_path):
    settings = Settings(database_path=str(tmp_path / "t.db"), auth_mode="oidc",
                        session_secret=SECRET, robot_token="test-token")
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def as_role(client: TestClient, role: str) -> TestClient:
    client.cookies.set(COOKIE_NAME, issue(SECRET, ROLES[role], 300))
    return client


# -- recordings: start/stop need an operator --------------------------------

@pytest.mark.parametrize("path", ["/api/recordings/start", "/api/recordings/stop"])
def test_observer_cannot_mutate_recordings(client, path):
    response = as_role(client, "observer").post(path, json={"name": "x"})
    assert response.status_code == 403
    assert "read-only" in response.json()["detail"]


@pytest.mark.parametrize("role", ["operator", "administrator"])
@pytest.mark.parametrize("path", ["/api/recordings/start", "/api/recordings/stop"])
def test_operators_and_admins_pass_the_recording_gate(client, role, path):
    """No robot is connected, so the request fails on state (409) — but it gets
    past authorization, which is what is under test."""
    response = as_role(client, role).post(path, json={"name": "x"})
    assert response.status_code == 409


# -- recordings: delete needs an administrator ------------------------------

@pytest.mark.parametrize("role", ["observer", "operator"])
def test_only_administrators_may_delete_recordings(client, role):
    response = as_role(client, role).delete("/api/recordings/1")
    assert response.status_code == 403
    assert "administrator" in response.json()["detail"]


def test_administrator_passes_the_delete_gate(client):
    # 404 rather than 403: authorization passed, the recording just isn't there.
    assert as_role(client, "administrator").delete("/api/recordings/1").status_code == 404


# -- command audit is administrator-only ------------------------------------

@pytest.mark.parametrize("role", ["observer", "operator"])
def test_command_audit_is_administrator_only(client, role):
    response = as_role(client, role).get("/api/commands")
    assert response.status_code == 403
    assert "administrator" in response.json()["detail"]


def test_administrator_reads_the_command_audit(client):
    response = as_role(client, "administrator").get("/api/commands")
    assert response.status_code == 200 and response.json() == []


# -- what observers keep ----------------------------------------------------

@pytest.mark.parametrize("path", [
    "/api/recordings", "/api/snapshot", "/api/events", "/api/history/battery",
    "/api/layouts", "/api/health",
])
def test_observers_keep_read_only_telemetry(client, path):
    """Observers are telemetry-only, not locked out: the read surface, and
    replaying/exporting an existing recording, all stay available."""
    assert as_role(client, "observer").get(path).status_code == 200


def test_unauthenticated_still_gets_401_not_403(client):
    """Ordering matters: no session is 'sign in', not 'you lack the role'."""
    client.cookies.clear()
    assert client.post("/api/recordings/start", json={"name": "x"}).status_code == 401
    assert client.get("/api/commands").status_code == 401
