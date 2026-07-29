"""The per-channel CSV bundle produced by /api/recordings/{id}/export.zip."""
import csv
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.protocol.envelope import encode
from app.recordings.export import build_zip, slugify
from app.settings import Settings


@pytest.fixture()
def client(tmp_path):
    settings = Settings(robot_token="test-token", database_path=str(tmp_path / "test.db"))
    with TestClient(create_app(settings)) as test_client:
        yield test_client


RECORDING = {
    "id": 3,
    "name": "Night patrol #2",
    "robot_id": "patrolbot-01",
    "started_at": "2026-07-28 09:00:00",
    "ended_at": "2026-07-28 09:00:10",
    "sample_count": 6,
    "channels": ["pose", "battery", "event", "lidar", "path", "diagnostics", "base_state"],
}

SAMPLES = [
    {"ts": "2026-07-28T09:00:00.100Z", "kind": "pose", "data": json.dumps(
        {"x": 1.0, "y": 2.0, "yaw": 0.5, "linear_velocity": 0.3,
         "angular_velocity": 0.0, "localized": True})},
    {"ts": "2026-07-28T09:00:00.600Z", "kind": "pose", "data": json.dumps(
        {"x": 1.5, "y": 2.0, "yaw": 0.5, "linear_velocity": 0.3,
         "angular_velocity": 0.0, "covariance_trace": 0.02, "localized": False})},
    {"ts": "2026-07-28T09:00:01.000Z", "kind": "battery", "data": json.dumps(
        {"voltage": 25.1, "current": -1.2, "percentage": 88.0, "charging": False,
         "estimate": {"state": "discharging", "minutes_remaining": 120,
                      "confidence": "high"}})},
    {"ts": "2026-07-28T09:00:01.000Z", "kind": "lidar", "data": json.dumps(
        {"angle_min": -1.57, "angle_increment": 0.01, "ranges": [1.0, None, 2.5]})},
    {"ts": "2026-07-28T09:00:02.000Z", "kind": "path", "data": json.dumps(
        {"points": [[0.0, 0.0], [3.0, 4.0]], "goal": {"x": 3.0, "y": 4.0, "yaw": None}})},
    {"ts": "2026-07-28T09:00:02.000Z", "kind": "diagnostics", "data": json.dumps(
        {"items": [{"name": "motors", "level": "OK", "message": "fine",
                    "values": {"temp": "40"}}]})},
    {"ts": "2026-07-28T09:00:03.000Z", "kind": "event", "data": json.dumps(
        {"id": 4, "ts": "2026-07-28T09:00:03.000Z", "severity": "info",
         "title": "Recording started", "message": 'He said "hi"'})},
    {"ts": "2026-07-28T09:00:03.000Z", "kind": "base_state", "data": json.dumps(
        {"session_generation": 1, "link_connected": True, "telemetry_age": 0.1,
         "hardware_state_valid": True, "charge_state": "idle", "motors_enabled": True,
         "estop_pressed": False, "fault_flags": 0, "stall_value": 0,
         "bumpers_front": False, "bumpers_rear": True})},
]


def open_bundle(payload: bytes) -> tuple[zipfile.ZipFile, str]:
    """Returns the archive and its single top-level folder prefix."""
    zf = zipfile.ZipFile(io.BytesIO(payload))
    prefixes = {name.split("/")[0] for name in zf.namelist()}
    assert len(prefixes) == 1
    return zf, prefixes.pop() + "/"


def rows(zf: zipfile.ZipFile, prefix: str, filename: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(zf.read(prefix + filename).decode())))


def test_one_csv_per_channel_plus_metadata():
    payload, filename = build_zip(RECORDING, SAMPLES)
    assert filename == "recording-3-night-patrol-2.zip"
    zf, prefix = open_bundle(payload)
    assert {name[len(prefix):] for name in zf.namelist()} == {
        "README.txt", "recording.json",
        "pose.csv", "battery.csv", "events.csv", "base_state.csv",
        "diagnostics.csv", "path.csv", "path_points.csv",
        "lidar.csv", "lidar_ranges.csv",
    }
    assert zf.testzip() is None


def test_every_table_shares_the_same_leading_columns():
    payload, _ = build_zip(RECORDING, SAMPLES)
    zf, prefix = open_bundle(payload)
    for name in zf.namelist():
        if not name.endswith(".csv"):
            continue
        header = zf.read(name).decode().splitlines()[0]
        assert header.split(",")[:3] == ["recording_id", "ts", "elapsed_s"], name


def test_pose_rows_are_typed_and_time_zeroed_on_the_first_sample():
    payload, _ = build_zip(RECORDING, SAMPLES)
    zf, prefix = open_bundle(payload)
    pose = rows(zf, prefix, "pose.csv")
    assert [r["elapsed_s"] for r in pose] == ["0.000", "0.500"]
    assert pose[0]["recording_id"] == "3"
    assert pose[0]["ts"].endswith("Z")
    assert (pose[0]["x"], pose[0]["y"], pose[0]["yaw"]) == ("1.0", "2.0", "0.5")
    assert pose[0]["yaw_deg"] == "28.65"
    # Booleans are 0/1; an unreported field is empty, not zero.
    assert (pose[0]["localized"], pose[1]["localized"]) == ("1", "0")
    assert pose[0]["covariance_trace"] == ""
    assert pose[1]["covariance_trace"] == "0.02"


def test_nested_payloads_are_flattened():
    payload, _ = build_zip(RECORDING, SAMPLES)
    zf, prefix = open_bundle(payload)
    battery = rows(zf, prefix, "battery.csv")[0]
    assert battery["estimate_state"] == "discharging"
    assert battery["estimate_minutes_remaining"] == "120"
    assert battery["charging"] == "0"

    base = rows(zf, prefix, "base_state.csv")[0]
    assert (base["bumpers_front"], base["bumpers_rear"]) == ("0", "1")

    diagnostics = rows(zf, prefix, "diagnostics.csv")[0]
    assert diagnostics["name"] == "motors"
    assert json.loads(diagnostics["values_json"]) == {"temp": "40"}


def test_variable_length_payloads_split_into_long_tables():
    payload, _ = build_zip(RECORDING, SAMPLES)
    zf, prefix = open_bundle(payload)

    scan = rows(zf, prefix, "lidar.csv")[0]
    assert (scan["beam_count"], scan["valid_count"]) == ("3", "2")
    assert (scan["range_min"], scan["range_max"]) == ("1.0", "2.5")
    beams = rows(zf, prefix, "lidar_ranges.csv")
    # Every beam gets a row so the index stays meaningful; a dropout is blank.
    assert [b["beam_index"] for b in beams] == ["0", "1", "2"]
    assert [b["range_m"] for b in beams] == ["1.0", "", "2.5"]
    assert beams[2]["angle_rad"] == "-1.55"
    assert {b["sample_index"] for b in beams} == {scan["sample_index"]}

    path = rows(zf, prefix, "path.csv")[0]
    assert (path["point_count"], path["path_length_m"]) == ("2", "5.0")
    assert (path["goal_x"], path["goal_y"], path["goal_yaw"]) == ("3.0", "4.0", "")
    points = rows(zf, prefix, "path_points.csv")
    assert [(p["point_index"], p["x"], p["y"]) for p in points] == \
        [("0", "0.0", "0.0"), ("1", "3.0", "4.0")]


def test_quoting_survives_a_round_trip():
    payload, _ = build_zip(RECORDING, SAMPLES)
    zf, prefix = open_bundle(payload)
    event = rows(zf, prefix, "events.csv")[0]
    assert event["message"] == 'He said "hi"'
    assert event["title"] == "Recording started"


def test_manifest_describes_every_file():
    payload, _ = build_zip(RECORDING, SAMPLES)
    zf, prefix = open_bundle(payload)
    manifest = json.loads(zf.read(prefix + "recording.json"))
    assert manifest["recording_id"] == 3
    assert manifest["time_origin"].startswith("2026-07-28T09:00:00.100")
    csv_names = {n[len(prefix):] for n in zf.namelist() if n.endswith(".csv")}
    assert set(manifest["files"]) == csv_names
    for name in csv_names:
        entry = manifest["files"][name]
        assert entry["rows"] == len(rows(zf, prefix, name))
        assert entry["columns"] == zf.read(prefix + name).decode().splitlines()[0].split(",")


def test_channel_selection_limits_the_bundle():
    payload, _ = build_zip(RECORDING, SAMPLES, ["pose", "lidar"])
    zf, prefix = open_bundle(payload)
    assert {n[len(prefix):] for n in zf.namelist() if n.endswith(".csv")} == \
        {"pose.csv", "lidar.csv", "lidar_ranges.csv"}


def test_selected_channel_with_no_samples_still_gets_a_header():
    payload, _ = build_zip(RECORDING, [], ["pose"])
    zf, prefix = open_bundle(payload)
    text = zf.read(prefix + "pose.csv").decode()
    assert text.startswith("recording_id,ts,elapsed_s,frame_id,x,y,yaw")
    assert rows(zf, prefix, "pose.csv") == []


def test_slugify_keeps_filenames_safe():
    assert slugify("Night patrol #2") == "night-patrol-2"
    assert slugify("../../etc/passwd") == "etc-passwd"
    assert slugify("   ") == "recording"


def test_export_endpoint(client):
    with client.websocket_connect("/ws/robot?token=test-token") as robot:
        robot.send_text(encode("robot.hello", "patrolbot-01", 0, {
            "protocol_version": 1, "capabilities": ["pose"],
            "map_version": 0, "software_version": "test",
        }))
        robot.receive_text()
        started = client.post("/api/recordings/start",
                              json={"name": "Export me", "channels": ["pose", "event"]})
        rec_id = started.json()["id"]
        client.post("/api/recordings/stop")

    response = client.get(f"/api/recordings/{rec_id}/export.zip")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "export" in response.headers["content-disposition"] or \
        f"recording-{rec_id}" in response.headers["content-disposition"]
    zf, prefix = open_bundle(response.content)
    assert prefix.startswith(f"recording-{rec_id}-")
    assert prefix + "events.csv" in zf.namelist()
    # The "Recording started"/"finished" events were captured.
    assert rows(zf, prefix, "events.csv")

    # Channel selection round-trips through the query string.
    only_pose = client.get(f"/api/recordings/{rec_id}/export.zip?channels=pose")
    zf, prefix = open_bundle(only_pose.content)
    assert {n[len(prefix):] for n in zf.namelist() if n.endswith(".csv")} == {"pose.csv"}

    assert client.get(f"/api/recordings/{rec_id}/export.zip?channels=nope").status_code == 400
    assert client.get("/api/recordings/9999/export.zip").status_code == 404
