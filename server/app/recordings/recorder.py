"""Telemetry session recorder (Phase 4).

Captures a decimated stream of the selected telemetry channels plus events
into SQLite while a recording is active. Entirely server-side: it works
identically against the mock robot and the real bridge, and asks nothing
extra of the robot (no rosbag, no additional topics).

Camera video capture is planned but deliberately not implemented yet — the
channel name "video" is reserved so the UI can advertise it as coming later.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..database.repo import Database

log = logging.getLogger("recorder")

# Channel -> minimum seconds between stored samples. Rates chosen so a long
# recording stays small: lidar dominates (~3 KB/sample) at 1 Hz.
CHANNEL_INTERVALS: dict[str, float] = {
    "pose": 0.5,
    "lidar": 1.0,
    "path": 1.0,
    "base_state": 1.0,
    "diagnostics": 5.0,
    "battery": 5.0,
    "event": 0.0,
}
KNOWN_CHANNELS = set(CHANNEL_INTERVALS)
DEFAULT_CHANNELS = ["pose", "battery", "event"]
RESERVED_CHANNELS = {"video"}  # advertised in the UI, not recordable yet
MAX_SAMPLES = 100_000  # hard stop; prevents runaway database growth


class Recorder:
    def __init__(self, db: "Database | None") -> None:
        self.db = db
        self.active: dict[str, Any] | None = None
        self._channels: set[str] = set()
        self._last_mono: dict[str, float] = {}
        self._samples = 0

    @property
    def recording(self) -> bool:
        return self.active is not None

    async def start(self, robot_id: str, name: str,
                    channels: list[str] | None = None) -> dict[str, Any] | None:
        if self.db is None or self.active is not None:
            return None
        wanted = [c for c in (channels or DEFAULT_CHANNELS) if c in KNOWN_CHANNELS]
        if not wanted:
            wanted = list(DEFAULT_CHANNELS)
        row = await self.db.start_recording(robot_id, name, wanted)
        if row is None:  # a 'recording' row survived a server restart
            return None
        self.active = row
        self._channels = set(wanted)
        self._last_mono = {}
        self._samples = 0
        log.info("recording %s started: %r channels=%s", row["id"], name, wanted)
        return row

    async def stop(self) -> dict[str, Any] | None:
        if self.db is None or self.active is None:
            return None
        recording_id = self.active["id"]
        self.active = None
        await self.db.stop_recording(recording_id)
        row = await self.db.get_recording(recording_id)
        log.info("recording %s stopped (%s samples)", recording_id, row and row["sample_count"])
        return row

    async def recover(self) -> None:
        """Close out a recording left open by a crash/restart."""
        if self.db is None:
            return
        for row in await self.db.list_recordings():
            if row["status"] == "recording":
                await self.db.stop_recording(row["id"])
                log.warning("closed recording %s left open by a previous run", row["id"])

    def offer(self, kind: str, ts: str, data: dict[str, Any]) -> None:
        """Called from the hub's hot path — must not block."""
        if self.db is None or self.active is None or kind not in self._channels:
            return
        interval = CHANNEL_INTERVALS.get(kind, 1.0)
        now = time.monotonic()
        if interval > 0 and now - self._last_mono.get(kind, 0.0) < interval:
            return
        self._last_mono[kind] = now
        if self._samples >= MAX_SAMPLES:
            return
        self._samples += 1
        recording_id = self.active["id"]
        asyncio.get_running_loop().create_task(
            self.db.add_recording_sample(recording_id, ts, kind,
                                         json.dumps(data, separators=(",", ":"))))
