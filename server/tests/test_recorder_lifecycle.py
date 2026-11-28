"""Synthetic SQLite coverage for the recorder's single finalization owner."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from app.database.repo import Database
from app.recordings import recorder as module
from app.recordings.recorder import Recorder


@pytest.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "recordings.db"))
    await database.init()
    yield database
    await database.close()


async def test_cap_closes_intake_synchronously_drains_and_coalesces_stop(db, monkeypatch):
    monkeypatch.setattr(module, "MAX_SAMPLES", 3)
    finished = AsyncMock()
    recorder = Recorder(db, finished)
    row = await recorder.start("robot", "capped", ["event"])
    gate = asyncio.Event()
    write = db.add_recording_sample

    async def blocked(*args):
        await gate.wait()
        await write(*args)

    monkeypatch.setattr(db, "add_recording_sample", blocked)
    stop_db = AsyncMock(wraps=db.stop_recording)
    monkeypatch.setattr(db, "stop_recording", stop_db)
    for _ in range(20):
        recorder.offer("event", "2026-11-28T00:00:00Z", {})
    assert recorder._samples == 3
    assert not recorder.recording
    assert recorder.describe(row)["status"] == "stopping"
    assert await recorder.start("robot", "too soon") is None
    stops = [asyncio.create_task(recorder.stop()) for _ in range(2)]
    await asyncio.sleep(0)
    assert not any(task.done() for task in stops)
    gate.set()
    results = await asyncio.gather(*stops)
    assert results[0] == results[1]
    assert results[0]["sample_count"] == await db.count_recording_samples(row["id"]) == 3
    assert results[0]["status"] == "done"
    assert results[0]["stop_reason"] == "sample_limit"
    assert stop_db.await_count == finished.await_count == 1
    assert await recorder.stop() is None
    fresh = await recorder.start("robot", "fresh", ["event"])
    recorder.offer("event", "2026-11-28T00:00:01Z", {})
    result = await recorder.stop()
    assert result["id"] == fresh["id"] != row["id"]
    assert result["sample_count"] == 1
    assert result["stop_reason"] == "manual"
    await recorder.shutdown()


async def test_real_100000_cap(db):
    assert module.MAX_SAMPLES == 100000
    recorder = Recorder(db)
    row = await recorder.start("robot", "full", ["event"])
    # Batches exercise the actual cap without intentionally overflowing the queue.
    for _ in range(500):
        for _ in range(200):
            recorder.offer("event", "2026-11-28T00:00:00Z", {})
        await recorder.writer.flush()
    assert not recorder.recording
    await recorder._finish_task
    assert (await db.get_recording(row["id"]))["sample_count"] == 100000
    assert (await db.get_recording(row["id"]))["stop_reason"] == "sample_limit"
    await recorder.shutdown()


async def test_concurrent_start_and_cancelled_stop_waiter(db):
    recorder = Recorder(db)
    first, second = await asyncio.gather(recorder.start("r", "one"), recorder.start("r", "two"))
    assert first is not None and second is None
    gate = asyncio.Event()

    async def slow():
        await gate.wait()

    recorder.writer.submit(slow)
    waiter = asyncio.create_task(recorder.stop())
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert not recorder._finish_task.cancelled()
    gate.set()
    assert (await recorder.stop())["status"] == "done"
    await recorder.shutdown()


@pytest.mark.parametrize("operation", ["stop_recording", "get_recording"])
async def test_failed_finalization_blocks_reuse_and_can_retry(db, monkeypatch, operation):
    finished = AsyncMock()
    recorder = Recorder(db, finished)
    row = await recorder.start("r", "retry", ["event"])
    recorder.offer("event", "2026-11-28T00:00:00Z", {})
    original = getattr(db, operation)
    monkeypatch.setattr(db, operation, AsyncMock(side_effect=OSError("disk failure")))
    with pytest.raises(OSError):
        await recorder.stop()
    assert recorder.describe(row)["status"] == "stop_failed"
    assert finished.call_args.args[0]["status"] == "stop_failed"
    assert not recorder.recording
    assert await recorder.start("r", "blocked") is None
    recorder.offer("event", "2026-11-28T00:00:01Z", {})
    monkeypatch.setattr(db, operation, original)
    result = await recorder.stop()
    assert result["sample_count"] == 1
    assert result["status"] == "done"
    assert await recorder.start("r", "next")
    await recorder.shutdown()


async def test_write_failure_continues_to_cap_and_does_not_poison_next_run(db, monkeypatch):
    monkeypatch.setattr(module, "MAX_SAMPLES", 3)
    recorder = Recorder(db)
    await recorder.start("r", "write fails", ["event"])
    original = db.add_recording_sample
    calls = 0

    async def fail_once(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("disk failure")
        await original(*args)

    monkeypatch.setattr(db, "add_recording_sample", fail_once)
    recorder.offer("event", "2026-11-28T00:00:00Z", {})
    recorder.offer("event", "2026-11-28T00:00:01Z", {})
    await recorder.writer.flush()
    assert recorder.recording and recorder._finish_task is None
    recorder.offer("event", "2026-11-28T00:00:02Z", {})
    assert not recorder.recording
    result = await recorder.stop()
    assert result["stop_reason"] == "sample_limit_with_loss"
    assert result["sample_count"] == 2
    assert recorder.writer.failed == 1
    await recorder.start("r", "next", ["event"])
    recorder.offer("event", "2026-11-28T00:00:02Z", {})
    assert (await recorder.stop())["stop_reason"] == "manual"
    await recorder.shutdown()


async def test_queue_overflow_keeps_intake_until_cap_and_next_run_is_clean(db, monkeypatch):
    monkeypatch.setattr(module, "MAX_SAMPLES", 257)
    recorder = Recorder(db)
    await recorder.start("r", "overflow", ["event"])
    for _ in range(300):
        recorder.offer("event", "2026-11-28T00:00:00Z", {})
    await recorder.writer.flush()
    assert recorder.recording and recorder._finish_task is None
    assert recorder._samples == 256 and recorder.writer.dropped == 44
    recorder.offer("event", "2026-11-28T00:00:01Z", {})
    assert not recorder.recording
    result = await recorder.stop()
    assert result["stop_reason"] == "sample_limit_with_loss"
    assert result["sample_count"] == 257
    await recorder.start("r", "next", ["event"])
    recorder.offer("event", "2026-11-28T00:00:02Z", {})
    assert (await recorder.stop())["stop_reason"] == "manual"
    await recorder.shutdown()


async def test_shutdown_and_restart_recovery(db):
    recorder = Recorder(db)
    row = await recorder.start("r", "shutdown", ["event"])
    recorder.offer("event", "2026-11-28T00:00:00Z", {})
    await recorder.shutdown()
    assert not recorder.writer.running
    assert await recorder.start("r", "closed") is None
    saved = await db.get_recording(row["id"])
    assert saved["sample_count"] == 1 and saved["stop_reason"] == "shutdown"
    orphan = await db.start_recording("r", "orphan", ["event"])
    # Recovery must see unfinished rows older than the list UI's 50-row window.
    for index in range(51):
        await db.db.execute("INSERT INTO recordings (robot_id, name, status) VALUES ('r', ?, 'done')", (str(index),))
    await db.db.commit()
    recovered = Recorder(db)
    await recovered.recover()
    assert (await db.get_recording(orphan["id"]))["stop_reason"] == "restart"
    assert await recovered.start("r", "after restart")
    await recovered.shutdown()


async def test_partial_sample_failure_recounts_actual_rows(db, monkeypatch):
    recorder = Recorder(db)
    row = await recorder.start("r", "partial write", ["event"])

    async def partial(recording_id, ts, kind, data):
        await db.db.execute(
            "INSERT INTO recording_samples (recording_id, ts, kind, data) VALUES (?, ?, ?, ?)",
            (recording_id, ts, kind, data))
        raise OSError("failed before updating sample_count")

    monkeypatch.setattr(db, "add_recording_sample", partial)
    recorder.offer("event", "2026-11-28T00:00:00Z", {})
    result = await recorder.stop()
    assert result["sample_count"] == await db.count_recording_samples(row["id"]) == 1
    assert result["stop_reason"] == "write_error"
    await recorder.shutdown()


async def test_shutdown_after_finalization_failure_leaves_recoverable_row(db, monkeypatch):
    recorder = Recorder(db)
    row = await recorder.start("r", "shutdown failure", ["event"])
    recorder.offer("event", "2026-11-28T00:00:00Z", {})
    original = db.stop_recording
    monkeypatch.setattr(db, "stop_recording", AsyncMock(side_effect=OSError("disk failure")))
    await recorder.shutdown()
    assert not recorder.writer.running
    assert recorder.describe(row)["status"] == "stop_failed"
    monkeypatch.setattr(db, "stop_recording", original)
    recovered = Recorder(db)
    await recovered.recover()
    result = await db.get_recording(row["id"])
    assert result["status"] == "done" and result["sample_count"] == 1
    assert result["stop_reason"] == "restart"
    await recovered.shutdown()


async def test_slow_drain_failure_keeps_intake_closed_and_retries(db, monkeypatch):
    recorder = Recorder(db)
    row = await recorder.start("r", "slow drain", ["event"])
    gate = asyncio.Event()

    async def blocked():
        await gate.wait()

    recorder.writer.submit(blocked)
    original = recorder.writer.flush
    monkeypatch.setattr(recorder.writer, "flush", AsyncMock(side_effect=TimeoutError("drain stalled")))
    with pytest.raises(TimeoutError):
        await recorder.stop()
    assert recorder.describe(row)["status"] == "stop_failed"
    assert not recorder.recording
    gate.set()
    monkeypatch.setattr(recorder.writer, "flush", original)
    assert (await recorder.stop())["status"] == "done"
    await recorder.shutdown()


async def test_stop_reason_migrates_existing_sqlite_without_losing_recordings(tmp_path):
    import sqlite3
    path = str(tmp_path / "legacy.db")
    with sqlite3.connect(path) as connection:
        connection.execute("""CREATE TABLE recordings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, robot_id TEXT NOT NULL,
            name TEXT NOT NULL, started_at TEXT NOT NULL DEFAULT (datetime('now')),
            ended_at TEXT, status TEXT NOT NULL DEFAULT 'recording',
            sample_count INTEGER NOT NULL DEFAULT 0)""")
        connection.execute("INSERT INTO recordings (robot_id, name, status) VALUES ('r', 'legacy', 'done')")
    database = Database(path)
    await database.init()
    row = await database.get_recording(1)
    assert row["name"] == "legacy" and row["status"] == "done"
    assert row["stop_reason"] is None
    assert row["channels"] == ["pose", "battery", "event"]
    await database.close()


async def test_write_failure_keeps_intake_open_until_manual_stop(db, monkeypatch):
    recorder = Recorder(db)
    row = await recorder.start("r", "write failure", ["event"])
    original = db.add_recording_sample
    monkeypatch.setattr(db, "add_recording_sample", AsyncMock(side_effect=OSError("disk failure")))
    recorder.offer("event", "2026-11-28T00:00:00Z", {})
    await recorder.writer.flush()
    assert recorder.recording and recorder._finish_task is None
    monkeypatch.setattr(db, "add_recording_sample", original)
    recorder.offer("event", "2026-11-28T00:00:01Z", {})
    result = await recorder.stop()
    assert result["id"] == row["id"] and result["sample_count"] == 1
    assert result["status"] == "done" and result["stop_reason"] == "write_error"
    await recorder.shutdown()


async def test_cap_and_shutdown_share_finalization(db, monkeypatch):
    monkeypatch.setattr(module, "MAX_SAMPLES", 1)
    finished = AsyncMock()
    recorder = Recorder(db, finished)
    row = await recorder.start("r", "cap shutdown", ["event"])
    recorder.offer("event", "2026-11-28T00:00:00Z", {})
    await asyncio.gather(recorder.stop(), recorder.shutdown())
    result = await db.get_recording(row["id"])
    assert result["sample_count"] == 1 and result["stop_reason"] == "sample_limit"
    assert finished.await_count == 1
    assert not recorder.writer.running


@pytest.mark.parametrize("phase", ["execute", "commit"])
async def test_cancel_during_start_transaction_keeps_accepted_start_owned(db, monkeypatch, phase):
    import sqlite3
    recorder = Recorder(db)
    entered, release = asyncio.Event(), asyncio.Event()
    execute = db.db._execute

    async def blocked(fn, *args, **kwargs):
        result = await execute(fn, *args, **kwargs)
        is_insert = fn.__name__ == "execute" and args[0].startswith("INSERT INTO recordings")
        if (phase == "execute" and is_insert) or (phase == "commit" and fn.__name__ == "commit"):
            entered.set()
            await release.wait()
        return result

    monkeypatch.setattr(db.db, "_execute", blocked)
    requester = asyncio.create_task(recorder.start("r", "cancelled requester", ["event"]))
    await entered.wait()
    requester.cancel()
    with pytest.raises(asyncio.CancelledError):
        await requester
    assert not recorder._start_task.done()
    # The two cancellation points straddle the real SQLite commit, not a mock row.
    with sqlite3.connect(db.path) as independent:
        assert independent.execute("SELECT COUNT(*) FROM recordings").fetchone()[0] == (phase == "commit")
    assert await recorder.start("r", "racing start") is None
    release.set()
    row = await recorder._start_task
    assert recorder.active == row and recorder.recording
    assert (await db.get_recording(row["id"]))["status"] == "recording"
    recorder.offer("event", "2026-11-28T00:00:00Z", {})
    result = await recorder.stop()
    assert result["id"] == row["id"] and result["sample_count"] == 1
    assert result["stop_reason"] == "manual"
    assert await recorder.start("r", "next")
    await recorder.shutdown()


@pytest.mark.parametrize("finish", ["stop", "shutdown"])
async def test_stop_or_shutdown_waits_for_cancelled_requesters_accepted_start(db, monkeypatch, finish):
    recorder = Recorder(db)
    entered, release = asyncio.Event(), asyncio.Event()
    commit = db.db.commit

    async def blocked_commit():
        await commit()
        entered.set()
        await release.wait()

    monkeypatch.setattr(db.db, "commit", blocked_commit)
    requester = asyncio.create_task(recorder.start("r", "pending start", ["event"]))
    await entered.wait()
    requester.cancel()
    with pytest.raises(asyncio.CancelledError):
        await requester
    finisher = asyncio.create_task(getattr(recorder, finish)())
    await asyncio.sleep(0)
    assert not finisher.done()
    release.set()
    await finisher
    rows = await db.list_recordings()
    assert len(rows) == 1 and rows[0]["status"] == "done"
    assert rows[0]["stop_reason"] == ("manual" if finish == "stop" else "shutdown")
    assert recorder.active is None
    await recorder.shutdown()
