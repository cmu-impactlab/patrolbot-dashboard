"""Read-only tuning transport, including fail-closed mixed-version rollout."""
import asyncio
import json
from types import SimpleNamespace as NS

import pytest

from app.authentication.local import LOCAL_USER
from app.tuning.broker import TuningBroker
from app.tuning.errors import TuningConflict, TuningForbidden, TuningInvalid, TuningNotFound, TuningRateLimited, TuningUnavailable
from tuning_fixtures import READ_CAPABILITY, new_uuid, read_request, response, snapshot

ROBOT = "patrolbot-01"


class Socket:
    def __init__(self):
        self.frames = asyncio.Queue()
        self.fail = False

    async def send_text(self, text):
        if self.fail:
            raise ConnectionError()
        self.frames.put_nowait(json.loads(text))

    async def next_frame(self):
        return await asyncio.wait_for(self.frames.get(), timeout=.5)


def setup(*, timeout=.1, max_pending=8, capabilities=(READ_CAPABILITY,)):
    socket = Socket()
    session = NS(robot_id=ROBOT, generation=1, websocket=socket,
                 state=NS(connection="online", capabilities=capabilities))
    hub = NS(robots={ROBOT: session}, _next_seq=lambda: 1)
    return TuningBroker(hub, read_timeout_s=timeout, max_pending=max_pending), session, socket


@pytest.mark.parametrize("op", ["save", "apply", "redeploy", "restore"])
async def test_legacy_write_capabilities_do_not_allow_any_tuning_mutation(op):
    broker, _, socket = setup(capabilities=(READ_CAPABILITY, "robot_tuning_write_v1", "robot_tuning_v1"))
    request = dict(read_request(), operation=op)
    with pytest.raises(TuningInvalid, match="read-only"):
        await broker.request(LOCAL_USER, ROBOT, request)
    assert socket.frames.empty()
    assert not broker._pending


async def test_read_keeps_verified_actor_and_accepts_a_bound_reply():
    broker, session, socket = setup()
    task = asyncio.create_task(broker.snapshot(LOCAL_USER, ROBOT))
    frame = await socket.next_frame()
    request = frame["data"]["request"]
    assert request["operation"] == "read"
    assert frame["data"]["actor"]["username"] == LOCAL_USER.username
    assert "authorization" not in frame["data"]
    expected = snapshot()
    await broker.handle_response(session, NS(robot_id=ROBOT), response(request["request_id"], snapshot_data=expected))
    assert (await task)["session_id"] == expected["session_id"]
    assert not broker._pending


@pytest.mark.parametrize("scenario", ["offline", "missing", "capability", "send", "timeout"])
async def test_reads_do_not_default_on_transport_failure(scenario):
    broker, session, socket = setup(timeout=.02)
    if scenario == "offline":
        session.state.connection = "offline"
    elif scenario == "missing":
        broker.hub.robots.clear()
    elif scenario == "capability":
        session.state.capabilities = ("robot_tuning_v1",)
    elif scenario == "send":
        socket.fail = True
    with pytest.raises(TuningUnavailable):
        await broker.snapshot(LOCAL_USER, ROBOT)
    assert not broker._pending


async def test_actor_must_be_verified_and_request_cannot_supply_one():
    broker, _, socket = setup()
    with pytest.raises(TuningForbidden):
        await broker.snapshot(NS(role="forged"), ROBOT)
    with pytest.raises(TuningInvalid):
        await broker.request(LOCAL_USER, ROBOT, dict(read_request(), actor={"role": "administrator"}))
    assert socket.frames.empty()


@pytest.mark.parametrize("scenario", ["wrong-robot", "replacement", "disconnect", "shutdown", "invalid"])
async def test_wrong_or_unavailable_reply_does_not_become_current(scenario):
    broker, session, socket = setup(timeout=.05)
    task = asyncio.create_task(broker.snapshot(LOCAL_USER, ROBOT))
    request_id = (await socket.next_frame())["data"]["request"]["request_id"]
    if scenario == "disconnect":
        broker.disconnected(session)
    elif scenario == "shutdown":
        await broker.shutdown()
    else:
        if scenario == "replacement":
            session.websocket = Socket()
        reply = response(request_id, snapshot_data=snapshot())
        if scenario == "invalid":
            reply["snapshot"] = "invalid"
        await broker.handle_response(session, NS(robot_id="other" if scenario == "wrong-robot" else ROBOT), reply)
    with pytest.raises(TuningUnavailable):
        await task
    assert not broker._pending


async def test_duplicate_reads_and_concurrency_are_bounded():
    broker, session, socket = setup(max_pending=1)
    request = read_request()
    task = asyncio.create_task(broker.request(LOCAL_USER, ROBOT, request))
    await socket.next_frame()
    with pytest.raises(TuningConflict):
        await broker.request(LOCAL_USER, ROBOT, request)
    with pytest.raises(TuningRateLimited):
        await broker.snapshot(LOCAL_USER, ROBOT)
    broker.disconnected(session)
    with pytest.raises(TuningUnavailable):
        await task


async def test_historical_job_status_is_read_only():
    broker, session, socket = setup()
    job_id = new_uuid()
    task = asyncio.create_task(broker.job(LOCAL_USER, ROBOT, job_id))
    request = (await socket.next_frame())["data"]["request"]
    assert request["operation"] == "status" and request["job_id"] == job_id
    await broker.handle_response(session, NS(robot_id=ROBOT), response(request["request_id"]))
    with pytest.raises(TuningNotFound):
        await task
