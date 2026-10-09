"""Fixed-operation host supervisor client. Never executes shell or robot commands."""
from __future__ import annotations
import json
import queue
import socket
import threading
from uuid import UUID

MAX_REPLY = 8192
TERMINAL = {'succeeded', 'failed', 'uncertain'}


def exchange(path, payload):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        sock.connect(path)
        sock.sendall(json.dumps(payload).encode() + b'\n')
        data = bytearray()
        while b'\n' not in data:
            chunk = sock.recv(min(4096, MAX_REPLY + 1 - len(data)))
            if not chunk:
                raise ValueError('Incomplete supervisor reply')
            data.extend(chunk)
            if len(data) > MAX_REPLY:
                raise ValueError('Oversized supervisor reply')
        result = json.loads(data.split(b'\n', 1)[0])
        if not isinstance(result, dict) or not isinstance(result.get('ok'), bool):
            raise ValueError('Invalid supervisor reply')
        return result


class SystemResetClient:
    def __init__(self, path, ws, *, transport=exchange):
        self.path, self.ws, self.transport = path, ws, transport
        self.available = False
        self.busy = True
        self._uncertain = False
        self._pending_id = None
        self._requests = queue.Queue(maxsize=1)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._last_status = None
        self._thread = threading.Thread(target=self._run, daemon=True, name='system-reset-status')

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=3)

    def request(self, data):
        command_id = str(data.get('command_id', ''))
        try:
            UUID(command_id)
        except (ValueError, TypeError):
            self._ack(command_id, False, 'Invalid reset request identifier.')
            return
        with self._lock:
            if data.get('operator_authorized') is not True:
                reason = 'Operator authorization is required.'
            elif not self.available or not self.path:
                reason = 'Software reset is not commissioned or the supervisor is unavailable.'
            elif self.busy:
                reason = 'A reset is pending or its outcome is unknown. Inspect it before retrying.'
            else:
                self.busy = True
                self._pending_id = command_id
                self._requests.put_nowait(command_id)
                return
        self._ack(command_id, False, reason)

    def _ack(self, command_id, accepted, reason=None):
        self.ws.send('command.ack', dict(command_id=command_id, accepted=accepted, reason=reason))

    def _submit(self, command_id):
        try:
            reply = self.transport(self.path, dict(op='request', request_id=command_id,
                                                   operation='software_reset'))
            if reply['ok'] is True and reply.get('request_id') != command_id:
                raise ValueError('Mismatched reset reply')
            accepted = reply['ok'] is True
            self._ack(command_id, accepted, reply.get('message'))
            if not accepted:
                with self._lock:
                    self._pending_id = None
                # Supervisor may be busy with another request; next status poll
                # reconciles it. Do not clear an uncertain local hold here.
                return
            self._report(reply)
        except (OSError, ValueError, TypeError):
            # Request may have reached the host. No retry, no false failure or
            # success claim, and no command release on a silent transport.
            self.available = False
            self._uncertain = True
            self.ws.send('command.progress', dict(command_id=command_id,
                stage='reset_outcome_unknown', detail='Reset outcome unknown. Inspect system status before retrying.'))

    def _report(self, reply):
        state = reply.get('state')
        if state == 'idle':
            with self._lock:
                if not self._uncertain and self._pending_id is None and self._requests.empty():
                    self.busy = False
            return
        command_id = reply.get('request_id')
        try:
            UUID(command_id)
        except (ValueError, TypeError, AttributeError):
            raise ValueError('Invalid status identifier')
        if state not in {'accepted', 'running', *TERMINAL}:
            raise ValueError('Invalid reset state')
        with self._lock:
            if self._pending_id is not None and command_id != self._pending_id:
                return
            self.busy = state not in {'succeeded', 'failed'}
            self._uncertain = state == 'uncertain'
            if not self.busy:
                self._pending_id = None
        # Re-emit after WebSocket reconnection: host status survives bridge reset.
        connection = self.ws.stats.get('last_connect_mono', 0)
        key = (command_id, state, reply.get('message'), connection)
        if key == self._last_status or not self.ws.connected.is_set():
            return
        self._last_status = key
        detail = str(reply.get('message') or 'Software reset in progress. A fresh pose will be required.')[:1024]
        if state in TERMINAL:
            self.ws.send('command.result', dict(command_id=command_id,
                outcome='timeout' if state == 'uncertain' else state, detail=detail))
        else:
            self.ws.send('command.progress', dict(command_id=command_id, stage='restarting', detail=detail))

    def poll(self):
        try:
            reply = self.transport(self.path, {'op': 'current'})
            self.available = reply['ok'] is True
            if self.available:
                self._report(reply)
        except (OSError, ValueError, TypeError):
            self.available = False

    def _run(self):
        while not self._stop.is_set():
            try:
                command_id = self._requests.get_nowait()
            except queue.Empty:
                pass
            else:
                self._submit(command_id)
            self.poll()
            self._stop.wait(1)
