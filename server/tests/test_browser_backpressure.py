"""What a full browser queue is allowed to throw away.

The hub's contract is that a slow browser never back-pressures the robot path,
so a full queue has to drop something. It used to drop whatever was oldest,
which put the safety-relevant frames in the firing line: command.result
(the operator's command silently stays "in progress" forever), state.connection
(the robot went offline and the dashboard still shows it live), event.append
(an e-stop that never appears in the alert list). The lidar burst that caused
the backlog, meanwhile, went through untouched.
"""
import asyncio

import pytest

from app.telemetry.hub import (
    PROTECTED_TYPES,
    QUEUE_LIMIT,
    BrowserClient,
    TelemetryHub,
)
from app.settings import Settings


class FakeSocket:
    """Records what was sent and whether the server closed the connection."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed_with: tuple[int, str] | None = None

    async def send_text(self, frame: str) -> None:
        self.sent.append(frame)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed_with = (code, reason)


@pytest.fixture()
def client() -> BrowserClient:
    return BrowserClient(FakeSocket())


def fill(client: BrowserClient, count: int, protected: bool, tag: str = "t") -> None:
    for index in range(count):
        client.offer(f"{tag}{index}", protected=protected)


def queued(client: BrowserClient) -> list[str]:
    return [frame for frame, _protected in client._queue]


# -- eviction picks the expendable frame ------------------------------------

def test_a_full_queue_of_telemetry_drops_telemetry(client):
    fill(client, QUEUE_LIMIT, protected=False, tag="lidar")
    assert client.depth == QUEUE_LIMIT

    client.offer("command.result", protected=True)

    assert client.depth == QUEUE_LIMIT
    assert "command.result" in queued(client)
    assert "lidar0" not in queued(client)  # the oldest expendable frame went
    assert client.dropped == 1
    assert client.overloaded is False


def test_protected_frames_survive_a_sustained_telemetry_flood(client):
    """The realistic shape: a handful of protected frames interleaved with far
    more telemetry than the queue can hold."""
    client.offer("state.connection", protected=True)
    fill(client, QUEUE_LIMIT, protected=False, tag="a")
    client.offer("command.result", protected=True)
    fill(client, QUEUE_LIMIT * 3, protected=False, tag="b")
    client.offer("event.append", protected=True)

    remaining = queued(client)
    assert "state.connection" in remaining
    assert "command.result" in remaining
    assert "event.append" in remaining
    assert client.depth <= QUEUE_LIMIT


def test_eviction_takes_the_oldest_unprotected_frame_not_the_oldest_frame(client):
    client.offer("protected-first", protected=True)
    fill(client, QUEUE_LIMIT - 1, protected=False, tag="tele")

    client.offer("newcomer", protected=False)

    remaining = queued(client)
    assert remaining[0] == "protected-first"  # not evicted despite being oldest
    assert "tele0" not in remaining


def test_unprotected_frames_are_dropped_rather_than_growing_the_queue(client):
    fill(client, QUEUE_LIMIT * 5, protected=False)
    assert client.depth == QUEUE_LIMIT


# -- the all-protected case: disconnect rather than lose one ----------------

def test_an_all_protected_full_queue_condemns_the_client(client):
    fill(client, QUEUE_LIMIT, protected=True, tag="p")
    assert client.overloaded is False

    client.offer("one-too-many", protected=True)

    assert client.overloaded is True
    # Nothing already queued was thrown away to make the decision.
    assert client.depth == QUEUE_LIMIT
    assert "one-too-many" not in queued(client)


async def test_a_condemned_client_is_disconnected_instead_of_served(client):
    socket = client.websocket
    fill(client, QUEUE_LIMIT + 1, protected=True, tag="p")

    await asyncio.wait_for(client.sender(), timeout=2)

    assert socket.closed_with is not None
    code, reason = socket.closed_with
    assert code == 1013 and "snapshot" in reason


async def test_a_healthy_client_is_served_in_order(client):
    socket = client.websocket
    client.offer("first", protected=True)
    client.offer("second", protected=False)
    client.offer("third", protected=True)

    sender = asyncio.create_task(client.sender())
    for _ in range(20):
        await asyncio.sleep(0)
        if len(socket.sent) == 3:
            break
    sender.cancel()

    assert socket.sent == ["first", "second", "third"]


# -- the protected set itself ------------------------------------------------

@pytest.mark.parametrize("type_", [
    "command.result", "command.ack", "command.progress",
    "state.connection", "state.robot_status", "state.system_health",
    "state.capabilities", "event.append", "server.snapshot", "telemetry.map",
])
def test_the_frames_that_must_not_be_dropped_are_marked_protected(type_):
    assert type_ in PROTECTED_TYPES


@pytest.mark.parametrize("type_", [
    "telemetry.pose", "telemetry.lidar", "telemetry.battery",
    "telemetry.path", "telemetry.diagnostics", "telemetry.resources",
])
def test_high_rate_telemetry_stays_expendable(type_):
    """If these were protected, a lidar burst alone would condemn the client."""
    assert type_ not in PROTECTED_TYPES


def test_publish_marks_frames_from_the_protected_set():
    """The hub, not just the client, has to get the flag right."""
    hub = TelemetryHub(Settings())
    client = BrowserClient(FakeSocket())
    hub.browsers.add(client)

    hub.publish("telemetry.lidar", "patrolbot-01", {"angle_min": 0.0,
                                                    "angle_increment": 0.1,
                                                    "ranges": []})
    hub.publish("event.append", "patrolbot-01", {"id": 1, "ts": "2026-08-08T00:00:00Z",
                                                 "severity": "info", "title": "t",
                                                 "message": "m"})

    protection = [protected for _frame, protected in client._queue]
    assert protection == [False, True]
