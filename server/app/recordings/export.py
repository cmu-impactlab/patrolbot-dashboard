"""Recording export: one CSV per channel, bundled into a zip.

The single flat CSV that came before mashed every channel into one wide table,
so most cells in any given row were blank and a lidar scan had nowhere to go.
This module writes one narrow, fully-populated table per channel instead, plus
a machine-readable manifest, so a recording drops straight into pandas/R/Excel
without a parsing step first.

Conventions that hold for every table (also stated in the bundled README):

* Columns are fixed and ordered; a channel with no samples still gets a file
  with its header row, so downstream code never has to branch on absence.
* Every row starts with ``recording_id, ts, elapsed_s`` — the same three
  columns everywhere, so tables join on ``ts`` and concatenating exports from
  several recordings stays unambiguous.
* ``elapsed_s`` is seconds since the recording's first sample, which keeps the
  whole bundle on one clock (robot timestamps and the server's ``started_at``
  come from different clocks and must not be mixed).
* Booleans are 0/1 and empty means "not reported", never zero.

Variable-length payloads (lidar scans, planned paths) are split into a summary
row per sample plus a long-format companion table, which is the shape analysis
tools actually want.
"""
from __future__ import annotations

import contextlib
import csv
import json
import math
import os
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

# Channel -> the files it produces, in the order they are written.
CHANNEL_FILES: dict[str, tuple[str, ...]] = {
    "pose": ("pose.csv",),
    "battery": ("battery.csv",),
    "event": ("events.csv",),
    "base_state": ("base_state.csv",),
    "diagnostics": ("diagnostics.csv",),
    "path": ("path.csv", "path_points.csv"),
    "lidar": ("lidar.csv", "lidar_ranges.csv"),
}

LEAD_COLUMNS = ("recording_id", "ts", "elapsed_s")

COLUMNS: dict[str, tuple[str, ...]] = {
    "pose.csv": LEAD_COLUMNS + (
        "frame_id", "x", "y", "yaw", "yaw_deg",
        "linear_velocity", "angular_velocity", "covariance_trace", "localized",
    ),
    "battery.csv": LEAD_COLUMNS + (
        "voltage", "current", "percentage", "charging",
        "estimate_state", "estimate_minutes_remaining", "estimate_confidence",
    ),
    "events.csv": LEAD_COLUMNS + ("event_id", "severity", "title", "message"),
    "base_state.csv": LEAD_COLUMNS + (
        "session_generation", "link_connected", "telemetry_age",
        "hardware_state_valid", "charge_state", "motors_enabled", "estop_pressed",
        "fault_flags", "stall_value", "bumpers_front", "bumpers_rear",
    ),
    "diagnostics.csv": LEAD_COLUMNS + (
        "sample_index", "item_index", "name", "level", "message", "values_json",
    ),
    "path.csv": LEAD_COLUMNS + (
        "sample_index", "frame_id", "point_count", "path_length_m",
        "goal_x", "goal_y", "goal_yaw",
    ),
    "path_points.csv": LEAD_COLUMNS + ("sample_index", "point_index", "x", "y"),
    "lidar.csv": LEAD_COLUMNS + (
        "sample_index", "angle_min", "angle_increment", "beam_count",
        "valid_count", "range_min", "range_max",
    ),
    "lidar_ranges.csv": LEAD_COLUMNS + ("sample_index", "beam_index", "angle_rad", "range_m"),
}

# One line per file, used to build the README's contents listing.
FILE_NOTES: dict[str, str] = {
    "pose.csv": "Robot position and velocity in the map frame (~2 Hz).",
    "battery.csv": "Pack voltage, current and charge state (~0.2 Hz).",
    "events.csv": "Operator-visible alerts, exactly as they appeared in the dashboard.",
    "base_state.csv": "Drive-base hardware state: motors, e-stop, bumpers, faults (~1 Hz).",
    "diagnostics.csv": "One row per diagnostic item per sample (long format).",
    "path.csv": "One row per planned path: its length, point count and goal.",
    "path_points.csv": "The waypoints of each planned path, long format; join on sample_index.",
    "lidar.csv": "One row per laser scan: geometry and range summary.",
    "lidar_ranges.csv": "One row per beam per scan, long format; join on sample_index.",
}


def _parse_ts(value: str | None) -> datetime | None:
    """Parse the ISO-8601 stamps used on the wire and SQLite's datetime('now')."""
    if not value:
        return None
    text = value.strip().replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# Public alias: the API needs it to establish a recording's time origin
# without reading every sample.
parse_timestamp = _parse_ts


def _num(value: Any) -> str:
    """Numbers pass through; anything non-numeric becomes an empty cell."""
    if value is None or isinstance(value, bool):
        return ""
    return str(value) if isinstance(value, (int, float)) else ""


def _bool(value: Any) -> str:
    return "" if value is None else ("1" if value else "0")


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _round(value: Any, places: int) -> str:
    return "" if not isinstance(value, (int, float)) or isinstance(value, bool) \
        else str(round(float(value), places))


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:48] or "recording"


class _Table:
    """A CSV being written straight to disk in the export's scratch directory.

    It used to accumulate in a StringIO and the finished archive was returned
    as one bytes object, so a large recording was held in memory three times
    over — the decoded samples, every CSV, and the zip. lidar_ranges.csv alone
    is one row per beam per scan: a ten-minute scan recording at 1 Hz and 360
    beams is 216 000 rows.
    """

    def __init__(self, directory: str, filename: str) -> None:
        self.filename = filename
        self.path = os.path.join(directory, filename)
        # newline="" per the csv module; \r\n + QUOTE_MINIMAL is RFC 4180,
        # which every reader agrees on.
        self._handle = open(self.path, "w", newline="", encoding="utf-8")
        self.writer = csv.writer(self._handle, lineterminator="\r\n")
        self.writer.writerow(COLUMNS[filename])
        self.rows = 0

    def write(self, lead: list[str], rest: Iterable[str]) -> None:
        self.writer.writerow(lead + list(rest))
        self.rows += 1

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.close()

    @property
    def bytes_written(self) -> int:
        self._handle.flush()
        return os.path.getsize(self.path)


def _path_length(points: list[Any]) -> float:
    total = 0.0
    for i in range(1, len(points)):
        (ax, ay), (bx, by) = points[i - 1][:2], points[i][:2]
        total += ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
    return total


class ExportTooLarge(Exception):
    """Raised when an export exceeds the configured uncompressed byte budget."""

    def __init__(self, written: int, limit: int) -> None:
        super().__init__(f"export exceeded {limit} bytes (reached {written})")
        self.written = written
        self.limit = limit


# An export is one HTTP response held open while it is produced, so it needs a
# ceiling that does not depend on how long someone left a recording running.
# 512 MB uncompressed is far above any real recording here (the entire live
# database is ~30 MB) and far below anything that would trouble the host.
MAX_EXPORT_BYTES = 512 * 1024 * 1024
# How often to re-measure. Checking every row would stat() per row.
SIZE_CHECK_EVERY = 5000


class ZipExportBuilder:
    """Builds a recording export incrementally on disk.

    Split in two on purpose. `add()` is called from the event loop as samples
    are paged out of the database — it is cheap per row and touches no
    compression. `finalize()` does the DEFLATE work and is meant to be handed
    to a worker thread, because compressing hundreds of megabytes is the part
    that would otherwise stall every telemetry socket on the server.

    Use as a context manager, or call close(); the scratch directory has to go
    away even when the download fails halfway through.
    """

    def __init__(self, recording: dict[str, Any], selected: list[str],
                 t0: datetime | None, max_bytes: int | None = None) -> None:
        self.recording = recording
        self.recording_id = recording["id"]
        self.selected = selected
        self.t0 = t0
        # Read at construction, not as a default argument: a default is bound
        # at import and would ignore any later change to the module setting.
        self.max_bytes = MAX_EXPORT_BYTES if max_bytes is None else max_bytes
        self.folder = f"recording-{self.recording_id}-{slugify(recording.get('name', ''))}"
        self._directory = tempfile.mkdtemp(prefix="patrolbot-export-")
        self._closed = False
        self._rows_since_check = 0
        self.tables: dict[str, _Table] = {}
        for channel in selected:
            for filename in CHANNEL_FILES[channel]:
                self.tables[filename] = _Table(self._directory, filename)
        # Per-channel sample counters, so the long-format tables can be joined
        # back to their summary row without relying on timestamp equality.
        self._indices: dict[str, int] = {channel: -1 for channel in selected}

    def __enter__(self) -> "ZipExportBuilder":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def add(self, sample: dict[str, Any]) -> None:
        kind = sample["kind"]
        if kind not in self._indices:
            return
        self._indices[kind] += 1
        index = self._indices[kind]
        stamp = _parse_ts(sample["ts"])
        elapsed = ("" if stamp is None or self.t0 is None
                   else f"{(stamp - self.t0).total_seconds():.3f}")
        iso = stamp.isoformat().replace("+00:00", "Z") if stamp else _text(sample["ts"])
        lead = [str(self.recording_id), iso, elapsed]
        try:
            data = (json.loads(sample["data"]) if isinstance(sample["data"], str)
                    else sample["data"])
        except (TypeError, ValueError):
            return
        if not isinstance(data, dict):
            return
        _WRITERS[kind](self.tables, lead, index, data)

        self._rows_since_check += 1
        if self._rows_since_check >= SIZE_CHECK_EVERY:
            self._rows_since_check = 0
            written = sum(table.bytes_written for table in self.tables.values())
            if written > self.max_bytes:
                raise ExportTooLarge(written, self.max_bytes)

    def finalize(self) -> str:
        """Compress everything written so far. Blocking — run in a thread."""
        for table in self.tables.values():
            table.close()
        archive_path = os.path.join(self._directory, "export.zip")
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{self.folder}/README.txt",
                        _readme(self.recording, self.selected, self.tables, self.t0))
            zf.writestr(f"{self.folder}/recording.json",
                        json.dumps(_manifest(self.recording, self.selected,
                                             self.tables, self.t0), indent=2) + "\n")
            for channel in self.selected:
                for filename in CHANNEL_FILES[channel]:
                    zf.write(self.tables[filename].path, f"{self.folder}/{filename}")
        return archive_path

    @property
    def filename(self) -> str:
        return f"{self.folder}.zip"

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for table in self.tables.values():
            with contextlib.suppress(Exception):
                table.close()
        shutil.rmtree(self._directory, ignore_errors=True)


def select_channels(recording: dict[str, Any], present: Iterable[str],
                    channels: list[str] | None = None) -> list[str]:
    """Which channels this export will contain.

    A channel can appear in the samples without being in the recording's
    declared list (older rows, or the "Recording started" event), so the data
    is trusted alongside the declaration rather than silently dropped.
    """
    recorded = [c for c in recording.get("channels") or [] if c in CHANNEL_FILES]
    for kind in present:
        if kind in CHANNEL_FILES and kind not in recorded:
            recorded.append(kind)
    return [c for c in recorded if channels is None or c in channels]


def build_zip(recording: dict[str, Any], samples: list[dict[str, Any]],
              channels: list[str] | None = None) -> tuple[bytes, str]:
    """Render a recording as a zip of per-channel CSVs, in one go.

    Kept for callers holding an in-memory sample list (tests, small exports).
    The HTTP export path uses ZipExportBuilder directly so it never has the
    whole recording in memory at once.
    """
    selected = select_channels(recording, (s["kind"] for s in samples), channels)
    stamps = [t for t in (_parse_ts(s["ts"]) for s in samples) if t is not None]
    t0 = min(stamps) if stamps else _parse_ts(recording.get("started_at"))
    with ZipExportBuilder(recording, selected, t0) as builder:
        for sample in samples:
            builder.add(sample)
        path = builder.finalize()
        with open(path, "rb") as handle:
            return handle.read(), builder.filename


# -- per-channel row writers -------------------------------------------------

def _write_pose(tables: dict[str, _Table], lead: list[str], index: int, data: dict) -> None:
    yaw = data.get("yaw")
    yaw_deg = math.degrees(yaw) if isinstance(yaw, (int, float)) and not isinstance(yaw, bool) else None
    tables["pose.csv"].write(lead, [
        _text(data.get("frame_id", "map")),
        _num(data.get("x")), _num(data.get("y")), _num(yaw),
        _round(yaw_deg, 2),
        _num(data.get("linear_velocity")), _num(data.get("angular_velocity")),
        _num(data.get("covariance_trace")), _bool(data.get("localized")),
    ])


def _write_battery(tables: dict[str, _Table], lead: list[str], index: int, data: dict) -> None:
    estimate = data.get("estimate") or {}
    tables["battery.csv"].write(lead, [
        _num(data.get("voltage")), _num(data.get("current")), _num(data.get("percentage")),
        _bool(data.get("charging")),
        _text(estimate.get("state")), _num(estimate.get("minutes_remaining")),
        _text(estimate.get("confidence")),
    ])


def _write_event(tables: dict[str, _Table], lead: list[str], index: int, data: dict) -> None:
    tables["events.csv"].write(lead, [
        _num(data.get("id")), _text(data.get("severity")),
        _text(data.get("title")), _text(data.get("message")),
    ])


def _write_base_state(tables: dict[str, _Table], lead: list[str], index: int, data: dict) -> None:
    tables["base_state.csv"].write(lead, [
        _num(data.get("session_generation")), _bool(data.get("link_connected")),
        _num(data.get("telemetry_age")), _bool(data.get("hardware_state_valid")),
        _text(data.get("charge_state")), _bool(data.get("motors_enabled")),
        _bool(data.get("estop_pressed")), _num(data.get("fault_flags")),
        _num(data.get("stall_value")),
        _bool(data.get("bumpers_front")), _bool(data.get("bumpers_rear")),
    ])


def _write_diagnostics(tables: dict[str, _Table], lead: list[str], index: int, data: dict) -> None:
    for item_index, item in enumerate(data.get("items") or []):
        if not isinstance(item, dict):
            continue
        values = item.get("values")
        tables["diagnostics.csv"].write(lead, [
            str(index), str(item_index),
            _text(item.get("name")), _text(item.get("level")), _text(item.get("message")),
            "" if not values else json.dumps(values, separators=(",", ":"), sort_keys=True),
        ])


def _write_path(tables: dict[str, _Table], lead: list[str], index: int, data: dict) -> None:
    points = [p for p in (data.get("points") or []) if isinstance(p, (list, tuple)) and len(p) >= 2]
    goal = data.get("goal") or {}
    tables["path.csv"].write(lead, [
        str(index), _text(data.get("frame_id", "map")), str(len(points)),
        _round(_path_length(points), 3),
        _num(goal.get("x")), _num(goal.get("y")), _num(goal.get("yaw")),
    ])
    for point_index, (x, y) in enumerate((p[0], p[1]) for p in points):
        tables["path_points.csv"].write(lead, [str(index), str(point_index), _num(x), _num(y)])


def _write_lidar(tables: dict[str, _Table], lead: list[str], index: int, data: dict) -> None:
    ranges = data.get("ranges") or []
    angle_min = data.get("angle_min")
    increment = data.get("angle_increment")
    valid = [r for r in ranges if isinstance(r, (int, float))]
    tables["lidar.csv"].write(lead, [
        str(index), _num(angle_min), _num(increment), str(len(ranges)), str(len(valid)),
        _round(min(valid), 3) if valid else "", _round(max(valid), 3) if valid else "",
    ])
    # The beam angle is precomputed: it is the one thing every consumer of a
    # scan needs and the one thing that is annoying to recover from a CSV.
    have_angles = isinstance(angle_min, (int, float)) and isinstance(increment, (int, float))
    for beam_index, value in enumerate(ranges):
        angle = _round(angle_min + beam_index * increment, 6) if have_angles else ""
        tables["lidar_ranges.csv"].write(lead, [str(index), str(beam_index), angle, _num(value)])


_WRITERS: dict[str, Callable[[dict[str, _Table], list[str], int, dict], None]] = {
    "pose": _write_pose,
    "battery": _write_battery,
    "event": _write_event,
    "base_state": _write_base_state,
    "diagnostics": _write_diagnostics,
    "path": _write_path,
    "lidar": _write_lidar,
}


# -- bundle metadata ---------------------------------------------------------

def _manifest(recording: dict[str, Any], selected: list[str],
              tables: dict[str, _Table], t0: datetime | None) -> dict[str, Any]:
    return {
        "recording_id": recording["id"],
        "name": recording.get("name"),
        "robot_id": recording.get("robot_id"),
        "started_at": recording.get("started_at"),
        "ended_at": recording.get("ended_at"),
        "sample_count": recording.get("sample_count"),
        "channels_recorded": recording.get("channels"),
        "channels_exported": selected,
        # Everything's elapsed_s is measured from here.
        "time_origin": t0.isoformat().replace("+00:00", "Z") if t0 else None,
        "files": {
            filename: {"rows": tables[filename].rows,
                       "columns": list(COLUMNS[filename]),
                       "description": FILE_NOTES[filename]}
            for channel in selected for filename in CHANNEL_FILES[channel]
        },
    }


def _readme(recording: dict[str, Any], selected: list[str],
            tables: dict[str, _Table], t0: datetime | None) -> str:
    lines = [
        f"PatrolBot recording #{recording['id']} — {recording.get('name', '')}",
        "=" * 72,
        "",
        f"Robot:      {recording.get('robot_id')}",
        f"Started:    {recording.get('started_at')} UTC",
        f"Ended:      {recording.get('ended_at') or '(not recorded)'} UTC",
        f"Samples:    {recording.get('sample_count')}",
        f"Channels:   {', '.join(selected) or '(none)'}",
        "",
        "Files",
        "-----",
    ]
    for channel in selected:
        for filename in CHANNEL_FILES[channel]:
            lines.append(f"  {filename:<20} {tables[filename].rows:>8} rows  "
                         f"{FILE_NOTES[filename]}")
    lines += [
        "  recording.json              the same metadata, machine-readable",
        "",
        "Reading the tables",
        "------------------",
        "Every table starts with the same three columns:",
        "",
        "  recording_id  this recording's id, so exports can be concatenated",
        "  ts            UTC timestamp, ISO 8601 (e.g. 2026-07-28T09:14:02.310Z)",
        "  elapsed_s     seconds since the first sample in this recording",
        "",
        f"elapsed_s is measured from {t0.isoformat().replace('+00:00', 'Z') if t0 else 'n/a'}.",
        "It exists because robot timestamps and the server's clock are separate;",
        "elapsed_s keeps every table in this bundle on one timeline. Join tables",
        "on ts (or elapsed_s) — samples from different channels arrive at",
        "different rates, so an as-of/nearest join is usually what you want.",
        "",
        "Boolean columns are 0 or 1. An empty cell means the robot did not report",
        "that field — it never means zero.",
        "",
        "path_points.csv and lidar_ranges.csv are long format: one row per",
        "waypoint / per beam. Join them to path.csv / lidar.csv on sample_index.",
        "diagnostics.csv is long format too: one row per diagnostic item.",
        "",
        "Example (Python)",
        "----------------",
        "  import pandas as pd",
        "  pose = pd.read_csv('pose.csv', parse_dates=['ts'])",
        "  batt = pd.read_csv('battery.csv', parse_dates=['ts'])",
        "  merged = pd.merge_asof(pose.sort_values('ts'), batt.sort_values('ts'),",
        "                         on='ts', direction='nearest')",
        "",
    ]
    return "\n".join(lines)
