"""A large recording must not be loaded into memory, or block the event loop.

Replay, CSV export and zip export all called get_recording_samples() for the
entire recording and decoded every row. A lidar recording is dominated by its
scans — one row per scan, ~3 KB each, and lidar_ranges.csv expands to one row
per beam per scan — so the three paths between them held the recording several
times over while the event loop had no opportunity to serve anything else.

These tests use tracemalloc for the memory claims (peak allocation while the
response is produced) and a concurrently-running heartbeat for the
responsiveness claim, rather than asserting on wall-clock time, which is not
reproducible on shared CI.
"""
import asyncio
import io
import json
import os
import tempfile
import tracemalloc
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.api.recordings import MAX_REPLAY_SAMPLES, REPLAY_CHANNELS
from app.database.repo import Database
from app.main import create_app
from app.settings import Settings

# 400 scans x 360 beams: about 1.2 MB of JSON, enough that "all of it at once"
# is measurably different from "a page at a time" without a slow test.
LIDAR_SAMPLES = 400
BEAMS = 360


@pytest.fixture()
async def big_recording(tmp_path):
    """A recording whose lidar scans dwarf everything a replay actually uses."""
    db = Database(str(tmp_path / "big.db"))
    await db.init()
    row = await db.start_recording("patrolbot-01", "big one",
                                   ["pose", "lidar", "battery", "event"])
    recording_id = row["id"]

    scan = json.dumps({"angle_min": -3.14, "angle_increment": 0.0175,
                       "ranges": [round(1.0 + i * 0.001, 3) for i in range(BEAMS)]},
                      separators=(",", ":"))
    for index in range(LIDAR_SAMPLES):
        ts = f"2026-08-08T00:{index // 60:02d}:{index % 60:02d}Z"
        await db.add_recording_sample(recording_id, ts, "lidar", scan)
        if index % 4 == 0:
            await db.add_recording_sample(recording_id, ts, "pose", json.dumps(
                {"x": index * 0.1, "y": 0.0, "yaw": 0.0,
                 "linear_velocity": 0.2, "angular_velocity": 0.0}))
        if index % 50 == 0:
            await db.add_recording_sample(recording_id, ts, "battery", json.dumps(
                {"voltage": 25.0, "percentage": 80.0, "charging": False}))
    await db.stop_recording(recording_id)
    yield db, recording_id
    await db.close()


# -- pagination -------------------------------------------------------------

async def test_iteration_pages_rather_than_fetching_everything(big_recording):
    db, recording_id = big_recording
    seen = 0
    async for _sample in db.iter_recording_samples(recording_id, page=64):
        seen += 1
    assert seen == await db.count_recording_samples(recording_id)


async def test_pagination_returns_every_row_exactly_once(big_recording):
    db, recording_id = big_recording
    ids = [s["id"] async for s in db.iter_recording_samples(recording_id, page=37)]
    assert len(ids) == len(set(ids))
    assert ids == sorted(ids)


async def test_the_channel_filter_is_applied_in_sql(big_recording):
    db, recording_id = big_recording
    kinds = {s["kind"] async for s in db.iter_recording_samples(recording_id, ["pose"])}
    assert kinds == {"pose"}

    page = await db.get_recording_samples(recording_id, ["pose"], after_id=0, limit=10)
    assert len(page) == 10 and all(row["kind"] == "pose" for row in page)


async def test_channel_filtering_saves_reading_the_scans(big_recording):
    db, recording_id = big_recording
    everything = await db.count_recording_samples(recording_id)
    replay_only = await db.count_recording_samples(recording_id, list(REPLAY_CHANNELS))
    assert replay_only < everything / 3  # the scans are most of the recording


# -- the HTTP surface -------------------------------------------------------

@pytest.fixture()
def client(tmp_path):
    settings = Settings(robot_token="test-token", database_path=str(tmp_path / "http.db"))
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def run(coro):
    """Drive a coroutine against the app's database from a sync test.

    TestClient owns its own loop, so seeding has to happen on a separate
    one; aiosqlite runs its connection on a dedicated thread, so this is
    safe."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def seed(app_db, samples_per_kind: dict[str, int]) -> int:
    row = await app_db.start_recording("patrolbot-01", "seeded", list(samples_per_kind))
    scan = json.dumps({"angle_min": -3.14, "angle_increment": 0.0175,
                       "ranges": [1.0] * BEAMS}, separators=(",", ":"))
    payloads = {
        "lidar": scan,
        "pose": json.dumps({"x": 1.0, "y": 2.0, "yaw": 0.0,
                            "linear_velocity": 0.1, "angular_velocity": 0.0}),
        "battery": json.dumps({"voltage": 25.0, "percentage": 80.0, "charging": False}),
        "event": json.dumps({"id": 1, "severity": "info", "title": "t", "message": "m"}),
    }
    for kind, count in samples_per_kind.items():
        for index in range(count):
            await app_db.add_recording_sample(
                row["id"], f"2026-08-08T00:00:{index % 60:02d}Z", kind, payloads[kind])
    await app_db.stop_recording(row["id"])
    return row["id"]


def test_replay_returns_only_the_channels_it_draws(client):
    db = client.app.state.db
    recording_id = run(seed(db, {"lidar": 50, "pose": 20, "battery": 5}))

    body = client.get(f"/api/recordings/{recording_id}").json()

    kinds = {sample["kind"] for sample in body["samples"]}
    assert kinds == {"pose", "battery"}  # no lidar, and no event in this seed
    assert "lidar" not in body["channels_returned"]
    assert len(body["samples"]) == 25


def test_replay_can_be_asked_for_other_channels(client):
    db = client.app.state.db
    recording_id = run(seed(db, {"lidar": 10, "pose": 10}))

    body = client.get(f"/api/recordings/{recording_id}", params={"channels": "lidar"}).json()
    assert {s["kind"] for s in body["samples"]} == {"lidar"}


def test_replay_caps_its_response_and_says_so(client, monkeypatch):
    import app.api.recordings as recordings_api

    monkeypatch.setattr(recordings_api, "MAX_REPLAY_SAMPLES", 10)
    db = client.app.state.db
    recording_id = run(seed(db, {"pose": 40}))

    body = client.get(f"/api/recordings/{recording_id}").json()

    assert len(body["samples"]) == 10
    assert body["truncated"] is True
    assert body["available_sample_count"] == 40


def test_an_ordinary_recording_is_not_marked_truncated(client):
    db = client.app.state.db
    recording_id = run(seed(db, {"pose": 5, "battery": 2}))

    body = client.get(f"/api/recordings/{recording_id}").json()
    assert body["truncated"] is False
    assert MAX_REPLAY_SAMPLES > 7


def test_csv_export_streams_and_stays_complete(client):
    db = client.app.state.db
    recording_id = run(seed(db, {"pose": 300}))

    with client.stream("GET", f"/api/recordings/{recording_id}/export.csv") as response:
        assert response.status_code == 200
        text = "".join(response.iter_text())

    lines = [line for line in text.splitlines() if line]
    assert lines[0].startswith("ts,kind,")
    assert len(lines) == 301  # header + every sample


def test_zip_export_contains_every_channel(client):
    db = client.app.state.db
    recording_id = run(seed(db, {"pose": 20, "lidar": 5, "battery": 3}))

    response = client.get(f"/api/recordings/{recording_id}/export.zip")
    assert response.status_code == 200

    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        names = {name.split("/", 1)[1] for name in zf.namelist()}
        assert {"pose.csv", "lidar.csv", "lidar_ranges.csv", "battery.csv",
                "README.txt", "recording.json"} <= names
        pose_rows = zf.read(
            [n for n in zf.namelist() if n.endswith("pose.csv")][0]
        ).decode().strip().splitlines()
        assert len(pose_rows) == 21  # header + 20
        # The long-format table is the one that used to blow up in memory.
        ranges = zf.read(
            [n for n in zf.namelist() if n.endswith("lidar_ranges.csv")][0]
        ).decode().strip().splitlines()
        assert len(ranges) == 1 + 5 * BEAMS


def test_zip_export_refuses_an_oversized_recording(client, monkeypatch):
    import app.recordings.export as export_module

    monkeypatch.setattr(export_module, "MAX_EXPORT_BYTES", 20_000)
    monkeypatch.setattr(export_module, "SIZE_CHECK_EVERY", 10)
    db = client.app.state.db
    recording_id = run(seed(db, {"lidar": 40}))

    response = client.get(f"/api/recordings/{recording_id}/export.zip")

    assert response.status_code == 413
    assert "fewer channels" in response.json()["detail"]


def test_the_oversized_recording_still_exports_without_the_scans(client, monkeypatch):
    """The refusal has to leave a way through, or it is just a broken export."""
    import app.recordings.export as export_module

    monkeypatch.setattr(export_module, "MAX_EXPORT_BYTES", 20_000)
    monkeypatch.setattr(export_module, "SIZE_CHECK_EVERY", 10)
    db = client.app.state.db
    recording_id = run(seed(db, {"lidar": 40, "pose": 10}))

    response = client.get(f"/api/recordings/{recording_id}/export.zip",
                          params={"channels": "pose"})
    assert response.status_code == 200


def test_export_scratch_files_are_cleaned_up(client):
    db = client.app.state.db
    recording_id = run(seed(db, {"pose": 10, "lidar": 3}))

    before = set(os.listdir(tempfile.gettempdir()))
    client.get(f"/api/recordings/{recording_id}/export.zip")
    leaked = {name for name in set(os.listdir(tempfile.gettempdir())) - before
              if name.startswith("patrolbot-export-")}
    assert leaked == set()


# -- memory and responsiveness ----------------------------------------------

async def test_walking_a_large_recording_does_not_hold_it_all(big_recording):
    """The memory claim: peak allocation while walking the whole recording is
    a page, not the recording."""
    db, recording_id = big_recording
    everything = await db.get_recording_samples(recording_id, after_id=0, limit=10_000)
    whole_recording_bytes = sum(len(row["data"]) for row in everything)
    del everything

    tracemalloc.start()
    tracemalloc.reset_peak()
    count = 0
    async for _sample in db.iter_recording_samples(recording_id, page=100):
        count += 1
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert count > 400
    # Generous factor: this asserts the shape (bounded by page size), not a
    # tuned number that would make the test fragile.
    assert peak < whole_recording_bytes / 2, (
        f"peak {peak} vs whole recording {whole_recording_bytes}")


async def test_paging_yields_to_the_event_loop(big_recording):
    """The responsiveness claim: something else on the loop keeps running
    while a large recording is walked."""
    db, recording_id = big_recording
    ticks = 0
    stop = False

    async def heartbeat():
        nonlocal ticks
        while not stop:
            ticks += 1
            await asyncio.sleep(0)

    beater = asyncio.create_task(heartbeat())
    async for _sample in db.iter_recording_samples(recording_id, page=50):
        pass
    stop = True
    await beater

    assert ticks > 10, "the event loop never got control back during the walk"
