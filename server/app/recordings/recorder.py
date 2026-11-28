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
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from ..database.writer import BackgroundWriter

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
    def __init__(self, db: "Database | None",
                 on_finished: Callable[[dict[str, Any]], Awaitable[None]] | None = None) -> None:
        self.db = db
        self._on_finished = on_finished
        self._lock = asyncio.Lock()
        self._start_task: asyncio.Task | None = None
        self._finish_task: asyncio.Task | None = None
        self._stopping = False
        self._stop_failed = False
        self._reason = "manual"
        self._closed = False
        self.active: dict[str, Any] | None = None
        self._channels: set[str] = set()
        self._last_mono: dict[str, float] = {}
        self._samples = 0
        self.writer = BackgroundWriter("recording")
        self._dropped_at_start = 0
        self._failed_at_start = 0

    @property
    def recording(self) -> bool:
        return self.active is not None and not self._stopping

    async def start(self, robot_id: str, name: str,
                    channels: list[str] | None = None) -> dict[str, Any] | None:
        if self._start_task is not None and not self._start_task.done():
            return None
        self._start_task = asyncio.create_task(self._start(robot_id, name, channels),
                                              name="recording:start")
        self._start_task.add_done_callback(self._finished)
        # Once submitted, the DB write and active ownership outlive the requester.
        return await asyncio.shield(self._start_task)

    async def _start(self, robot_id: str, name: str,
                     channels: list[str] | None) -> dict[str, Any] | None:
        async with self._lock:
            if self.db is None or self.active is not None or self._closed:
                return None
            wanted = [c for c in (channels or DEFAULT_CHANNELS) if c in KNOWN_CHANNELS]
            if not wanted:
                wanted = list(DEFAULT_CHANNELS)
            row = await self.db.start_recording(robot_id, name, wanted)
            if row is None:
                return None
            self.active = row
            self._channels = set(wanted)
            self._last_mono = {}
            self._samples = 0
            self._dropped_at_start = self.writer.dropped
            self._failed_at_start = self.writer.failed
            self._finish_task = None
            self._stopping = self._stop_failed = False
            self._reason = "manual"
            log.info("recording %s started: %r channels=%s", row["id"], name, wanted)
            return row

    def describe(self, row: dict[str, Any]) -> dict[str, Any]:
        """Expose finalization state without claiming an uncommitted save."""
        if self.active and row["id"] == self.active["id"] and self._stopping:
            return {**row, "status": "stop_failed" if self._stop_failed else "stopping",
                    "stop_reason": self._reason}
        return row

    def _request_stop(self, reason: str) -> asyncio.Task:
        if not self._stopping:
            self._reason = reason
        self._stopping = True  # close intake synchronously on the last accepted sample
        if self._finish_task is None or self._finish_task.done():
            self._stop_failed = False
            self._finish_task = asyncio.create_task(self._finish(), name="recording:finish")
            # Automatic stops have no HTTP waiter to retrieve their exception.
            self._finish_task.add_done_callback(self._finished)
        return self._finish_task

    @staticmethod
    def _finished(task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception() is not None:
            log.error("%s failed", task.get_name(), exc_info=task.exception())

    async def stop(self, reason: str = "manual") -> dict[str, Any] | None:
        if self._start_task is not None and not self._start_task.done():
            await asyncio.shield(self._start_task)
        async with self._lock:
            if self._finish_task is not None and not self._finish_task.done():
                task = self._finish_task
            elif self.db is None or self.active is None:
                return None
            else:
                task = self._request_stop(reason)
        # A disconnected HTTP caller must not cancel the shared finalizer.
        return await asyncio.shield(task)

    async def _notify(self, row: dict[str, Any]) -> None:
        if self._on_finished is not None:
            try:
                await self._on_finished(row)
            except Exception:
                log.exception("could not publish recording result")

    async def _finish(self) -> dict[str, Any]:
        assert self.db is not None and self.active is not None
        recording_id = self.active["id"]
        try:
            await asyncio.wait_for(self.writer.flush(), timeout=10.0)
            # Preserve lossy intake; the saved outcome must disclose this run's loss.
            reason = self._reason
            if self.writer.failed > self._failed_at_start:
                reason = "sample_limit_with_loss" if reason == "sample_limit" else "write_error"
            elif self.writer.dropped > self._dropped_at_start:
                reason = "sample_limit_with_loss" if reason == "sample_limit" else "queue_full"
            await self.db.stop_recording(recording_id, reason)
            row = await self.db.get_recording(recording_id)
            if row is None:
                raise RuntimeError("recording disappeared during finalization")
        except Exception:
            self._stop_failed = True
            await self._notify(self.describe(self.active))
            raise
        await self._notify(row)
        self.active = None
        log.info("recording %s stopped (%s samples, %s)",
                 recording_id, row["sample_count"], row["stop_reason"])
        return row

    async def shutdown(self) -> None:
        self._closed = True
        try:
            await self.stop("shutdown")
        except Exception:
            # Preserve the unfinished row for recovery; shutdown must still close the DB.
            log.exception("recording will need recovery on restart")
        finally:
            await self.writer.stop()

    async def recover(self) -> None:
        """Close out a recording left open by a crash/restart."""
        if self.db is None:
            return
        for row in await self.db.list_recordings(limit=-1):
            if row["status"] == "recording":
                await self.db.stop_recording(row["id"], "restart")
                log.warning("closed recording %s left open by a previous run", row["id"])

    def offer(self, kind: str, ts: str, data: dict[str, Any]) -> None:
        """Called from the hub's hot path — must not block."""
        if self.db is None or not self.recording or kind not in self._channels:
            return
        interval = CHANNEL_INTERVALS.get(kind, 1.0)
        now = time.monotonic()
        if interval > 0 and now - self._last_mono.get(kind, 0.0) < interval:
            return
        self._last_mono[kind] = now
        recording_id = self.active["id"]
        if self.writer.submit(self.db.add_recording_sample, recording_id, ts, kind,
                              json.dumps(data, separators=(",", ":"))):
            self._samples += 1
            if self._samples >= MAX_SAMPLES:
                self._request_stop("sample_limit")
