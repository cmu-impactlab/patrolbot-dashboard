"""A bounded, owned queue for database writes issued from the telemetry path.

Recording samples and battery history are written from `handle_robot_message`,
which must not block on the database — a slow disk cannot be allowed to stall
the robot socket. That was implemented as one `create_task` per sample, which
has three problems and had all three:

- Nothing held a reference to the tasks, so the event loop was free to garbage
  collect one mid-flight and the sample vanished with no trace.
- Nothing retrieved their exceptions, so a failing write surfaced (at best) as
  "Task exception was never retrieved" on shutdown, attributed to nothing.
- Nothing waited for them. `Recorder.stop()` returned a sample_count that the
  outstanding writes had not caught up to yet, and application shutdown closed
  the database out from under tasks that were still running.

Nor was there any bound: the queue of pending writes was however many samples
the robot had sent since the disk last kept up.

This replaces all of that with a single worker per writer. Submissions are
non-blocking and are *dropped* — counted and logged — when the queue is full,
because the alternative is back-pressuring the robot path. Writes are
serialized, which also suits the SQLite backend, and both `flush()` and
`stop()` are deterministic.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

log = logging.getLogger("dbwriter")

# Roughly 30 s of the busiest recording configuration (pose at 2 Hz plus
# lidar, path, base_state at 1 Hz, battery and diagnostics slower). Deep
# enough to ride out a disk stall, shallow enough that "the database is not
# keeping up" is noticed rather than absorbed.
DEFAULT_MAXSIZE = 256


class BackgroundWriter:
    """Owns one worker task draining a bounded queue of pending writes."""

    def __init__(self, name: str, maxsize: int = DEFAULT_MAXSIZE) -> None:
        self.name = name
        self._queue: asyncio.Queue[tuple[Callable[..., Awaitable[Any]], tuple]] = (
            asyncio.Queue(maxsize=maxsize))
        self._task: asyncio.Task | None = None
        self.dropped = 0  # submissions refused because the queue was full
        self.failed = 0   # writes that raised

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    @property
    def healthy(self) -> bool:
        return self.dropped == 0 and self.failed == 0

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name=f"dbwriter:{self.name}")

    def submit(self, write: Callable[..., Awaitable[Any]], *args: Any) -> bool:
        """Queue a write. Never blocks, never raises; False when it was dropped.

        The callable and its arguments are stored rather than a coroutine
        object, so a dropped submission cannot leave a never-awaited coroutine
        behind.
        """
        if self._task is None:
            self.start()
        try:
            self._queue.put_nowait((write, args))
            return True
        except asyncio.QueueFull:
            self.dropped += 1
            if self.dropped == 1 or self.dropped % 100 == 0:
                log.error("%s writer is not keeping up: %d write(s) dropped",
                          self.name, self.dropped)
            return False

    async def flush(self) -> None:
        """Return once every queued write has completed."""
        if self._task is None:
            return
        await self._queue.join()

    async def stop(self, drain: bool = True) -> None:
        """Finish outstanding writes (or abandon them) and stop the worker."""
        task = self._task
        self._task = None
        if task is None:
            return
        if drain:
            try:
                await asyncio.wait_for(self._queue.join(), timeout=10.0)
            except TimeoutError:
                log.error("%s writer did not drain in 10s; abandoning %d write(s)",
                          self.name, self._queue.qsize())
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        while True:
            write, args = await self._queue.get()
            try:
                await write(*args)
            except asyncio.CancelledError:
                # Put the item back on the books before unwinding, so a
                # cancelled worker cannot leave join() waiting forever.
                self._queue.task_done()
                raise
            except Exception:
                self.failed += 1
                log.exception("%s write failed (%d total)", self.name, self.failed)
                self._queue.task_done()
            else:
                self._queue.task_done()
