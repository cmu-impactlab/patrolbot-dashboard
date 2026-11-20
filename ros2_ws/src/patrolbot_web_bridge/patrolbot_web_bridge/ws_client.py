"""Outbound WebSocket client running on its own thread + asyncio loop.

ROS callbacks hand envelopes over via a thread-safe enqueue; a sender task
drains the queue. Reconnects with jittered exponential backoff and re-runs
the hello handshake on every new session. Telemetry yields to command lifecycle,
hello, map and tuning replies. Protected-only saturation invalidates the socket
and latches a fault until supervised bridge recovery; delivery is then unknown,
never silently assumed successful. Only command lifecycle replies may survive
an ordinary reconnect for reconciliation; measurements, capability updates and
tuning RPCs never replay as fresh observations on another socket.
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

COMMAND_REPLY_TYPES = {"command.ack", "command.progress", "command.result"}
PROTECTED_TYPES = {"robot.hello", "telemetry.map", "tuning.response"} | COMMAND_REPLY_TYPES
QUEUE_LIMIT = 256

# send() owns the compact envelope prefix. Match only its top-level type, never
# a type-looking string or nested object in an otherwise disposable payload.
_PROTECTED_PREFIXES = tuple(
    f'{{"version":1,"type":"{type_}",' for type_ in PROTECTED_TYPES)
_COMMAND_REPLY_PREFIXES = tuple(
    f'{{"version":1,"type":"{type_}",' for type_ in COMMAND_REPLY_TYPES)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


BASE_CAPABILITIES = ["pose", "lidar", "path", "battery", "base_state",
                     "diagnostics", "resources", "map"]


def _json_default(obj):
    """Coerce numpy scalars/arrays that leak in from ROS messages (e.g. a
    covariance-derived bool_ in the pose payload) to native Python types.
    Duck-typed so the bridge needs no hard numpy import."""
    # Arrays also expose item(), but it raises unless they contain one value.
    # tolist() handles both arrays and NumPy scalar types without a hard import.
    tolist = getattr(obj, "tolist", None)
    if callable(tolist):
        return tolist()
    item = getattr(obj, "item", None)
    if callable(item):
        return item()
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


class WsClient:
    def __init__(self, server_url: str, token: str, robot_id: str,
                 on_map_wanted: Callable[[], None],
                 on_command: Callable[[dict], None] | None = None,
                 on_teleop: Callable[[dict], None] | None = None,
                 on_tuning: Callable[[dict], None] | None = None,
                 on_disconnect: Callable[[], None] | None = None) -> None:
        # The token travels in the Authorization header (see _connect) so it
        # never lands in reverse-proxy access logs. self.url stays clean.
        self.url = server_url
        self.server_url = server_url
        self.token = token
        self.robot_id = robot_id
        self.on_map_wanted = on_map_wanted
        self.on_command = on_command
        self.on_teleop = on_teleop
        self.on_tuning = on_tuning
        self.on_disconnect = on_disconnect
        self.connection_generation = 0
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
        self._failed = threading.Event()
        self._runner: asyncio.Task | None = None
        self._thread = threading.Thread(target=self._run_thread, name="ws-client", daemon=True)
        self.stats = {"sent": 0, "dropped": 0, "reconnects": 0, "last_connect_mono": 0.0,
                      "overflow_failures": 0}

    # -- public API (called from ROS executor threads) ------------------------

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._cancel_worker()
        if self._thread.ident is not None and threading.current_thread() is not self._thread:
            self._thread.join(timeout=5)

    def _cancel_worker(self) -> None:
        """Interrupt reads, sends and reconnect backoff, not just an idle sender."""
        loop = self._loop
        if loop is not None:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(self._cancel_running_task)

    def _cancel_running_task(self) -> None:
        if self._runner is not None:
            self._runner.cancel()

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

    def send(self, type_: str, data: dict) -> bool:
        """Queue delivery, or return False on discard/fault (not a delivery ack)."""
        overflow = False
        with self._lock:
            if self._stop.is_set() or self._failed.is_set():
                self.stats["dropped"] += 1
                return False
            if type_ not in COMMAND_REPLY_TYPES and not self.connected.is_set():
                # A measurement/RPC result must never replay on a new socket.
                # Fresh hello/map state is built by the reconnect handshake.
                self.stats["dropped"] += 1
                return False
            self.sequence += 1
            frame = json.dumps({
                "version": 1, "type": type_, "robot_id": self.robot_id,
                "sequence": self.sequence, "timestamp": utc_now(), "data": data,
            }, separators=(",", ":"), default=_json_default)
            if len(self._queue) >= QUEUE_LIMIT:
                # Telemetry must never displace an ack/progress/terminal result.
                for index, queued in enumerate(self._queue):
                    if not queued.startswith(_PROTECTED_PREFIXES):
                        del self._queue[index]
                        self.stats["dropped"] += 1
                        break
                else:
                    self.stats["dropped"] += 1
                    if type_ not in PROTECTED_TYPES:
                        return False
                    # No bounded queue can guarantee delivery indefinitely. Make
                    # loss explicit by retiring the entire transport, not one
                    # terminal reply. No automatic reconnect/replay can hide it.
                    self._failed.set()
                    self.connected.clear()
                    self.connection_generation += 1
                    self.stats["overflow_failures"] += 1
                    self.stats["dropped"] += len(self._queue)
                    self._queue.clear()
                    overflow = True
            if not overflow:
                self._queue.append(frame)
        if overflow:
            log.critical("protected outbound queue saturated; command delivery is unknown. "
                         "Transport disabled; supervised bridge recovery is required.")
            # Disconnect callbacks run on the worker, not here: send() can be
            # called while the tuning adapter holds its non-reentrant input lock.
            self._cancel_worker()
            return False
        loop, wake = self._loop, self._wake
        if loop is not None and wake is not None:
            # A send racing a shutdown must not propagate into the ROS
            # executor and take the node down with it — the frame is already
            # queued, and there is nothing left to wake.
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(wake.set)
        return True

    # -- internals -------------------------------------------------------------

    def _run_thread(self) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            asyncio.run(self._run())

    async def _run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._runner = asyncio.current_task()
        self._wake = asyncio.Event()
        retry = 0
        try:
            while not self._stop.is_set() and not self._failed.is_set():
                try:
                    await self._session()
                    retry = 0
                except (OSError, websockets.WebSocketException, RuntimeError) as exc:
                    if self._stop.is_set() or self._failed.is_set():
                        break
                    delay = min(8.0, 0.5 * (2 ** retry)) * (1.0 + random.random() * 0.3)
                    retry += 1
                    self.stats["reconnects"] += 1
                    log.warning("connection lost (%s); retrying in %.1f s", exc, delay)
                    await asyncio.sleep(delay)
        finally:
            self.connected.clear()
            self._runner = self._wake = self._loop = None

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
        with self._lock:
            self.connection_generation += 1
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
            with self._lock:
                if self._stop.is_set() or self._failed.is_set():
                    return
                self.connected.set()
            self.stats["last_connect_mono"] = time.monotonic()
            log.info("connected to %s", self.server_url)
            if ack.get("data", {}).get("want_map", True):
                self.on_map_wanted()

            reader = asyncio.create_task(self._drain_incoming(ws))
            try:
                while not self._stop.is_set() and not self._failed.is_set():
                    if reader.done():
                        await reader
                        raise ConnectionError("robot socket reader ended")
                    frame = None
                    self._wake.clear()
                    with self._lock:
                        if self._queue:
                            frame = self._queue[0]
                    if frame is None:
                        try:
                            await asyncio.wait_for(self._wake.wait(), timeout=1.0)
                        except asyncio.TimeoutError:
                            pass
                        continue
                    await ws.send(frame)
                    with self._lock:
                        # Retain protected replies until send succeeds, including
                        # across an ambiguous socket failure. A producer may have
                        # evicted an in-flight telemetry head, never a reply.
                        if self._queue and self._queue[0] == frame:
                            self._queue.popleft()
                    self.stats["sent"] += 1
            finally:
                with self._lock:
                    self.connected.clear()
                    self.connection_generation += 1
                    previous_size = len(self._queue)
                    self._queue = deque(frame for frame in self._queue
                                        if frame.startswith(_COMMAND_REPLY_PREFIXES))
                    self.stats["dropped"] += previous_size - len(self._queue)
                reader.cancel()
                try:
                    if self.on_disconnect is not None:
                        self.on_disconnect()
                finally:
                    await asyncio.gather(reader, return_exceptions=True)

    async def _drain_incoming(self, ws) -> None:
        async for message in ws:
            if self._stop.is_set() or self._failed.is_set():
                return
            try:
                frame = json.loads(message)
            except json.JSONDecodeError:
                continue
            if not isinstance(frame, dict):
                continue
            if frame.get("robot_id") not in (None, self.robot_id):
                continue
            if frame.get("type") == "teleop.velocity" and self.on_teleop is not None:
                # Bounded latest-only ingress is owned by the ROS node. Never
                # put high-rate velocity frames into the discrete command FIFO.
                if frame.get("version") == 1 and frame.get("robot_id") == self.robot_id:
                    self.on_teleop(frame)
                continue
            if frame.get("type") == "tuning.request" and self.on_tuning is not None:
                # Read-only tuning transport: its own thread-safe queue inside
                # the adapter, never the discrete command FIFO or teleop lease.
                if frame.get("version") == 1 and frame.get("robot_id") == self.robot_id:
                    self.on_tuning(frame.get("data", {}))
                continue
            if frame.get("type") == "command.cancel" and self.on_command is not None:
                self.on_command({"command": "cancel", "command_id": frame.get("data", {}).get("command_id")})
            if frame.get("type") == "command.request" and self.on_command is not None:
                # Callback runs on the WS thread — it must only enqueue.
                self.on_command(frame.get("data", {}))
