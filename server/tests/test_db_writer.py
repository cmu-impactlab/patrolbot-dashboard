"""Ownership, bounding and draining of telemetry-path database writes.

Recording samples and battery history are written off the robot socket so a
slow disk cannot stall telemetry. They used to be `create_task` calls that
nobody held, awaited, or checked: samples could be collected mid-flight, write
failures surfaced only as "Task exception was never retrieved", `stop()`
returned a sample_count the writes had not reached yet, and shutdown closed the
database out from under tasks still using it.
"""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.database.repo import Database
from app.database.writer import BackgroundWriter
from app.main import create_app
from app.protocol.envelope import encode
from app.recordings.recorder import Recorder
from app.settings import Settings


@pytest.fixture()
async def db(tmp_path):
    database = Database(str(tmp_path / "w.db"))
    await database.init()
    yield database
    await database.close()


# -- the writer itself ------------------------------------------------------

async def test_writes_are_executed_in_order():
    done: list[int] = []
    writer = BackgroundWriter("t")
    writer.start()

    async def record(value: int) -> None:
        done.append(value)

    for value in range(20):
        writer.submit(record, value)
    await writer.flush()

    assert done == list(range(20))
    await writer.stop()


async def test_submit_never_blocks_and_drops_when_full():
    """The bound is the point: the robot socket must not wait on the disk."""
    gate = asyncio.Event()
    writer = BackgroundWriter("t", maxsize=4)
    writer.start()

    async def blocked() -> None:
        await gate.wait()

    for _ in range(50):
        writer.submit(blocked)

    assert writer.pending <= 4
    assert writer.dropped >= 40
    gate.set()
    await writer.stop()


async def test_a_failing_write_is_counted_and_does_not_kill_the_worker():
    survived: list[str] = []
    writer = BackgroundWriter("t")
    writer.start()

    async def boom() -> None:
        raise RuntimeError("disk on fire")

    async def fine() -> None:
        survived.append("ok")

    writer.submit(boom)
    writer.submit(fine)
    await writer.flush()

    assert writer.failed == 1
    assert survived == ["ok"]  # the worker kept going
    assert writer.running
    await writer.stop()


async def test_flush_waits_for_slow_writes():
    finished = False
    writer = BackgroundWriter("t")
    writer.start()

    async def slow() -> None:
        nonlocal finished
        await asyncio.sleep(0.05)
        finished = True

    writer.submit(slow)
    await writer.flush()

    assert finished is True
    await writer.stop()


async def test_stop_drains_before_cancelling():
    done: list[int] = []
    writer = BackgroundWriter("t")
    writer.start()

    async def record(value: int) -> None:
        await asyncio.sleep(0.01)
        done.append(value)

    for value in range(5):
        writer.submit(record, value)
    await writer.stop()

    assert done == list(range(5))
    assert not writer.running


async def test_a_dropped_submission_leaves_no_pending_coroutine():
    """submit() stores the callable, not a coroutine object — a dropped
    coroutine would raise "was never awaited" at an unrelated moment later."""
    writer = BackgroundWriter("t", maxsize=1)
    gate = asyncio.Event()
    writer.start()

    async def blocked() -> None:
        await gate.wait()

    for _ in range(10):
        assert writer.submit(blocked) in (True, False)  # never raises

    gate.set()
    await writer.stop()


# -- recorder integration ---------------------------------------------------

async def test_stop_flushes_every_queued_sample(db):
    recorder = Recorder(db)
    recorder.writer.start()
    row = await recorder.start("patrolbot-01", "flush test", ["pose"])

    for index in range(25):
        recorder._last_mono.clear()  # defeat the decimation, not the queueing
        recorder.offer("pose", f"2026-08-08T00:00:{index:02d}Z", {"x": float(index)})

    stopped = await recorder.stop()

    # The count reported to the operator is the count actually on disk.
    samples = await db.get_recording_samples(row["id"])
    assert stopped["sample_count"] == len(samples) == 25
    await recorder.shutdown()


async def test_samples_offered_after_stop_are_not_written(db):
    recorder = Recorder(db)
    recorder.writer.start()
    row = await recorder.start("patrolbot-01", "race", ["pose"])
    recorder.offer("pose", "2026-08-08T00:00:00Z", {"x": 1.0})

    await recorder.stop()
    recorder.offer("pose", "2026-08-08T00:00:01Z", {"x": 2.0})
    await recorder.writer.flush()

    samples = await db.get_recording_samples(row["id"])
    assert [json.loads(s["data"])["x"] for s in samples] == [1.0]
    await recorder.shutdown()


async def test_shutdown_finishes_writes_before_the_database_closes(tmp_path):
    """The shutdown race: `await db.close()` used to run while sample writes
    were still in flight against that connection."""
    database = Database(str(tmp_path / "s.db"))
    await database.init()
    recorder = Recorder(database)
    recorder.writer.start()
    row = await recorder.start("patrolbot-01", "shutdown", ["pose"])
    for index in range(10):
        recorder._last_mono.clear()
        recorder.offer("pose", f"2026-08-08T00:00:{index:02d}Z", {"x": float(index)})

    await recorder.shutdown()
    samples = await database.get_recording_samples(row["id"])
    await database.close()

    assert len(samples) == 10
    assert recorder.writer.failed == 0


# -- visibility through the app --------------------------------------------

@pytest.fixture()
def client(tmp_path):
    settings = Settings(robot_token="test-token", database_path=str(tmp_path / "t.db"))
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_health_reports_write_losses(client):
    health = client.get("/api/health").json()
    assert health["status"] == "healthy"
    assert health["writes"] == {"recording_dropped": 0, "recording_failed": 0,
                                "battery_dropped": 0, "battery_failed": 0}

    client.app.state.hub.battery_writer.dropped = 3
    degraded = client.get("/api/health").json()
    assert degraded["status"] == "degraded"
    assert degraded["writes"]["battery_dropped"] == 3


def test_battery_history_is_written_through_the_owned_writer(client):
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(encode("robot.hello", "patrolbot-01", 0, {
            "protocol_version": 1, "capabilities": ["battery"],
            "map_version": 0, "software_version": "test"}))
        robot.receive_text()
        for index in range(3):
            robot.send_text(encode("telemetry.battery", "patrolbot-01", index + 1, {
                "voltage": 24.0 + index, "percentage": 80.0, "charging": False}))
        with client.websocket_connect("/ws/ui") as ui:
            ui.receive_text()  # sync point

    rows = client.get("/api/history/battery").json()
    assert len(rows) == 3
    assert client.app.state.hub.battery_writer.dropped == 0
