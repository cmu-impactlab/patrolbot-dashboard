"""Outbound WebSocket client running on its own thread + asyncio loop.

ROS callbacks hand envelopes over via a thread-safe enqueue; a sender task
drains the queue. Reconnects with jittered exponential backoff and re-runs
the hello handshake on every new session. Drop-oldest under back-pressure,
except hello/map frames which must always land.
"""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
import random
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Callable

import websockets

log = logging.getLogger("web_bridge.ws")

PROTECTED_TYPES = {"robot.hello", "telemetry.map"}
QUEUE_LIMIT = 256


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


BASE_CAPABILITIES = ["pose", "lidar", "path", "battery", "base_state",
                     "diagnostics", "resources", "map"]


def _json_default(obj):
    """Coerce numpy scalars/arrays that leak in from ROS messages (e.g. a
    covariance-derived bool_ in the pose payload) to native Python types.
    Duck-typed so the bridge needs no hard numpy import."""
    item = getattr(obj, "item", None)
    if callable(item):
        return obj.item()
    tolist = getattr(obj, "tolist", None)
    if callable(tolist):
        return obj.tolist()
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


class WsClient:
    def __init__(self, server_url: str, token: str, robot_id: str,
                 on_map_wanted: Callable[[], None],
                 on_command: Callable[[dict], None] | None = None) -> None:
        # The token travels in the Authorization header (see _connect) so it
        # never lands in reverse-proxy access logs. self.url stays clean.
        self.url = server_url
        self.server_url = server_url
        self.token = token
        self.robot_id = robot_id
        self.on_map_wanted = on_map_wanted
        self.on_command = on_command
        self.sequence = 0
        self.map_version = 0
        # Dock operations whose servers are currently reachable. Empty until
        # the node says otherwise, so a robot without a dock manager never
        # offers the controls.
        self._dock_capabilities: list[str] = []
        # Set when the capability list changed but could not be announced yet
        # (socket down). Without this the list latches: the change is recorded,
        # the announce is skipped, and no later call sees a change to retry.
        self._capabilities_dirty = False
        self.connected = threading.Event()
        self._queue: deque[str] = deque()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run_thread, name="ws-client", daemon=True)
        self.stats = {"sent": 0, "dropped": 0, "reconnects": 0, "last_connect_mono": 0.0}

    # -- public API (called from ROS executor threads) ------------------------

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._loop is not None:
            # The loop may already be closed if the thread exited on its own;
            # raising here would bury whatever actually killed it.
            with contextlib.suppress(RuntimeError):
                self._loop.call_soon_threadsafe(lambda: None)
        self._thread.join(timeout=5)

    def capabilities(self) -> list[str]:
        return BASE_CAPABILITIES + list(self._dock_capabilities)

    def set_dock_capabilities(self, capabilities: list[str]) -> None:
        """Update what the robot claims it can do, mid-session.

        The dock manager can start after this bridge does, so the capability
        set is not fixed at connect time. On a change we re-announce with a
        fresh robot.hello rather than forcing a reconnect — the dashboard
        server treats a later hello as an update and re-broadcasts it, so an
        open dashboard un-greys the control without anyone reloading.
        """
        if list(capabilities) != self._dock_capabilities:
            self._dock_capabilities = list(capabilities)
            self._capabilities_dirty = True
            log.info("dock capabilities now: %s", self._dock_capabilities or "none")
        if not self._capabilities_dirty or not self.connected.is_set():
            return
        self.send("robot.hello", {
            "protocol_version": 1,
            "capabilities": self.capabilities(),
            "map_version": self.map_version,
            "software_version": "web-bridge-0.1.0",
        })
        self._capabilities_dirty = False

    def send(self, type_: str, data: dict) -> None:
        with self._lock:
            self.sequence += 1
            frame = json.dumps({
                "version": 1, "type": type_, "robot_id": self.robot_id,
                "sequence": self.sequence, "timestamp": utc_now(), "data": data,
            }, separators=(",", ":"), default=_json_default)
            if len(self._queue) >= QUEUE_LIMIT:
                # Drop the oldest unprotected frame; give up only if the queue
                # is somehow all-protected.
                for index, queued in enumerate(self._queue):
                    if '"telemetry.map"' not in queued and '"robot.hello"' not in queued:
                        del self._queue[index]
                        self.stats["dropped"] += 1
                        break
                else:
                    if type_ not in PROTECTED_TYPES:
                        self.stats["dropped"] += 1
                        return
            self._queue.append(frame)
        loop, wake = self._loop, self._wake
        if loop is not None and wake is not None:
            # A send racing a shutdown must not propagate into the ROS
            # executor and take the node down with it — the frame is already
            # queued, and there is nothing left to wake.
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(wake.set)

    # -- internals -------------------------------------------------------------

    def _run_thread(self) -> None:
        asyncio.run(self._run())

    async def _run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        retry = 0
        while not self._stop.is_set():
            try:
                await self._session()
                retry = 0
            except (OSError, websockets.WebSocketException, RuntimeError) as exc:
                self.connected.clear()
                delay = min(8.0, 0.5 * (2 ** retry)) * (1.0 + random.random() * 0.3)
                retry += 1
                self.stats["reconnects"] += 1
                log.warning("connection lost (%s); retrying in %.1f s", exc, delay)
                await asyncio.sleep(delay)

    def _connect(self):
        """Open the socket with the token in the Authorization header.

        websockets renamed the kwarg extra_headers -> additional_headers in
        v14. Pick by introspection, NOT by try/except: the legacy client's
        `connect(...)` returns an awaitable object without validating kwargs,
        so the TypeError only surfaces later inside `await`, where a fallback
        can no longer choose the other name. The Pi ships the legacy client
        (Ubuntu's python3-websockets), which is how that was found.
        """
        headers = {"Authorization": f"Bearer {self.token}"}
        kwargs = dict(max_size=16 * 1024 * 1024, ping_interval=20, ping_timeout=10)
        kwargs[self._header_kwarg()] = headers
        return websockets.connect(self.url, **kwargs)

    @staticmethod
    def _header_kwarg() -> str:
        try:
            parameters = inspect.signature(websockets.connect).parameters
        except (TypeError, ValueError):  # C-implemented or unintrospectable
            return "extra_headers"
        if "additional_headers" in parameters:
            return "additional_headers"
        if "extra_headers" in parameters:
            return "extra_headers"
        # Neither name is declared (only **kwargs): modern releases accept
        # additional_headers, so prefer it.
        return "additional_headers"

    async def _session(self) -> None:
        async with self._connect() as ws:
            hello = json.dumps({
                "version": 1, "type": "robot.hello", "robot_id": self.robot_id,
                "sequence": 0, "timestamp": utc_now(),
                "data": {
                    "protocol_version": 1,
                    "capabilities": self.capabilities(),
                    "map_version": self.map_version,
                    "software_version": "web-bridge-0.1.0",
                },
            }, separators=(",", ":"))
            await ws.send(hello)
            ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if ack.get("type") != "server.hello_ack":
                raise RuntimeError(f"unexpected hello reply: {ack.get('type')}")
            # The hello just sent carries the current capability list, so any
            # pending announce is now satisfied.
            self._capabilities_dirty = False
            self.connected.set()
            self.stats["last_connect_mono"] = time.monotonic()
            log.info("connected to %s", self.server_url)
            if ack.get("data", {}).get("want_map", True):
                self.on_map_wanted()

            reader = asyncio.create_task(self._drain_incoming(ws))
            try:
                while not self._stop.is_set():
                    frame = None
                    with self._lock:
                        if self._queue:
                            frame = self._queue.popleft()
                    if frame is None:
                        self._wake.clear()
                        try:
                            await asyncio.wait_for(self._wake.wait(), timeout=1.0)
                        except asyncio.TimeoutError:
                            pass
                        continue
                    await ws.send(frame)
                    self.stats["sent"] += 1
            finally:
                self.connected.clear()
                reader.cancel()

    async def _drain_incoming(self, ws) -> None:
        async for message in ws:
            try:
                frame = json.loads(message)
            except json.JSONDecodeError:
                continue
            if frame.get("type") == "command.request" and self.on_command is not None:
                # Callback runs on the WS thread — it must only enqueue.
                self.on_command(frame.get("data", {}))
