"""Portable harness checks: no ROS imports, network, Docker or hardware."""
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import teleop_lab as lab


def setup_readiness(monkeypatch):
    monkeypatch.setattr(lab.time, "monotonic", lambda: 10.)
    teleop = NS(state=Mock(return_value=({"link_connected": True}, 9.9, object(), 9.9)),
                idle_at=9.9, safety=Mock(return_value=None), session=NS(active=False),
                _path_identity=Mock(return_value=(b"mux", b"smoother")))
    bridge = NS(_teleop=teleop, _latest_amcl=None)
    node = NS(count_subscribers=Mock(return_value=3))
    server = dict(online_capable=True, reason=None, base_at=9.9, link_connected=True)
    state = dict(fault="healthy", sensor_max_gap_s=.04)
    lifecycle = dict(state=3, at=9.9)
    metrics = dict(tick_at=9.9, tick_max_gap_s=.025)
    return [bridge, node, server, state, lifecycle, metrics, 9., "healthy"]


def test_readiness_reports_runtime_ages(monkeypatch):
    args = setup_readiness(monkeypatch)
    result = lab.readiness_snapshot(*args)
    assert result["ready"] and result["physical_acceptance"] is False
    assert result["detail"]["ages_s"]["base"] == pytest.approx(.1)


@pytest.mark.parametrize("event", ["base", "odom", "idle", "tick", "lifecycle", "server_base"])
@pytest.mark.parametrize("at", [None, 8., 10.1])
def test_every_readiness_event_must_follow_this_intent(monkeypatch, event, at):
    args = setup_readiness(monkeypatch)
    teleop = args[0]._teleop
    if event in {"base", "odom"}:
        values = list(teleop.state())
        values[1 if event == "base" else 3] = at
        teleop.state.return_value = values
    elif event == "idle":
        teleop.idle_at = at
    elif event == "server_base":
        args[2]["base_at"] = at
    else:
        args[5 if event == "tick" else 4]["tick_at" if event == "tick" else "at"] = at
    assert not lab.readiness_snapshot(*args)["ready"]


@pytest.mark.parametrize("failure", ["runtime-stale", "server-stale", "subscriber", "lifecycle", "identity", "capability", "active"])
def test_publication_does_not_override_runtime_or_graph(monkeypatch, failure):
    args = setup_readiness(monkeypatch)
    teleop = args[0]._teleop
    if failure == "runtime-stale":
        teleop.safety.return_value = "Wait for fresh robot hardware readings."
    elif failure == "server-stale":
        args[2]["reason"] = "Manual drive requires fresh hardware readings."
    elif failure == "subscriber":
        args[1].count_subscribers.return_value = 1
    elif failure == "lifecycle":
        args[4]["state"] = 2
    elif failure == "identity":
        teleop._path_identity.return_value = None
    elif failure == "capability":
        args[2]["online_capable"] = False
    else:
        teleop.session.active = True
    assert not lab.readiness_snapshot(*args)["ready"]


def test_fault_readiness_requires_both_runtime_and_server_observation(monkeypatch):
    args = setup_readiness(monkeypatch)
    args[3]["fault"] = args[7] = "sbc_down"
    args[0]._teleop.safety.return_value = "Robot hardware data is invalid or stale."
    assert not lab.readiness_snapshot(*args)["ready"]
    args[0]._teleop.state.return_value[0]["link_connected"] = False
    assert not lab.readiness_snapshot(*args)["ready"]
    args[2]["link_connected"] = False
    assert lab.readiness_snapshot(*args)["ready"]


def test_cleanup_quiesces_all_executors_before_session_and_handles(capsys, tmp_path):
    resources = lab.LabResources()
    resources.directory = tmp_path
    events = []
    resources.bridge = NS(ws=NS(stop=lambda: events.append("transport")),
                          _teleop=NS(shutdown=lambda: events.append("session")))
    for name in ("receiver", "bridge", "probe"):
        executor = NS(shutdown=lambda timeout_sec, n=name: events.append(n + "-shutdown"))
        worker = NS(ident=1, join=lambda timeout, n=name: events.append(n + "-join"), is_alive=lambda: False)
        resources.executors.append((name, executor, worker))
        resources.nodes.append((name, NS(destroy_node=lambda n=name: events.append(n + "-destroy"))))
    resources.context_shutdown = lambda: events.append("context")
    assert resources.close()
    assert events == ["transport", "probe-shutdown", "probe-join", "bridge-shutdown", "bridge-join",
                      "receiver-shutdown", "receiver-join", "session", "probe-destroy",
                      "bridge-destroy", "receiver-destroy", "context"]
    assert json.loads((tmp_path / "teardown.json").read_text())["teardown"]["passed"]


@pytest.mark.parametrize("failure", ["false", "raises", "survives"])
def test_cleanup_failure_retains_handles_but_still_stops_http(failure):
    resources = lab.LabResources()
    def shutdown(timeout_sec):
        if failure == "raises":
            raise RuntimeError("broken executor")
        return failure != "false"
    worker = NS(ident=1, join=Mock(), is_alive=lambda: failure == "survives")
    resources.executors.append(("probe", NS(shutdown=shutdown), worker))
    destroy = Mock()
    resources.nodes.append(("probe", NS(destroy_node=destroy)))
    server = NS(should_exit=False)
    resources.server = (server, NS(join=Mock(), is_alive=lambda: False))
    assert not resources.close()
    destroy.assert_not_called()
    assert server.should_exit


def test_cleanup_one_budget_even_when_stop_blocks():
    resources = lab.LabResources()
    release = threading.Event()
    resources.executors.append(("probe", NS(shutdown=lambda timeout_sec: release.wait(1)), None))
    server = NS(should_exit=False)
    resources.server = (server, NS(join=Mock(), is_alive=lambda: False))
    started = time.monotonic()
    try:
        assert not resources.close(timeout=.04)
        assert time.monotonic() - started < .5
        assert server.should_exit
    finally:
        release.set()
        for worker in threading.enumerate():
            if worker.name == "cleanup-probe-shutdown":
                worker.join(1)


@pytest.mark.parametrize("early,forced", [(True, False), (False, True), (False, False)])
def test_native_exit_and_forced_kill_are_failures(monkeypatch, early, forced):
    resources = lab.LabResources()
    signals = []
    def killpg(pid, sig):
        assert pid == 45678
        if early or sig == 0:
            raise ProcessLookupError
        signals.append(sig)
    def wait(timeout):
        if forced and signal.SIGKILL not in signals:
            raise subprocess.TimeoutExpired("native", timeout)
        return 0
    resources.processes.append(NS(pid=45678, poll=lambda: 0 if early else None, wait=wait))
    monkeypatch.setattr(lab.os, "killpg", killpg)
    assert resources.close() is (not early and not forced)
    if forced:
        assert signals == [signal.SIGINT, signal.SIGTERM, signal.SIGKILL]


def test_partial_startup_runs_cleanup_and_preserves_startup_error(monkeypatch, capsys):
    destroyed = Mock()
    def fail(resources):
        resources.nodes.append(("receiver", NS(destroy_node=destroyed)))
        raise RuntimeError("startup failed")
    monkeypatch.setattr(lab, "run_lab", fail)
    monkeypatch.setattr(lab.signal, "signal", Mock())
    with pytest.raises(RuntimeError, match="startup failed"):
        lab.main()
    destroyed.assert_called_once()
    assert json.loads(capsys.readouterr().out)["teardown"]["passed"]


def test_shell_runner_propagates_supervisor_exit(tmp_path):
    # Override only setup sourcing and python; no installed ROS is consulted.
    bash_env = tmp_path / "bash-env"
    bash_env.write_text("source() { return 0; }\n")
    python = tmp_path / "python3"
    python.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\nexit 7\n')
    python.chmod(0o755)
    env = dict(os.environ, BASH_ENV=str(bash_env), PATH=str(tmp_path) + ":" + os.environ["PATH"])
    runner = Path(lab.__file__).with_name("run_teleop_lab.sh")
    result = subprocess.run(["bash", str(runner)], env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 7
    assert result.stdout.splitlines()[-1] == "--supervise"


@pytest.mark.parametrize("case,expected", [("success", 0), ("checker", 7), ("lab", 3),
    ("startup", 1), ("spawn", 1), ("forced", 1), ("teardown", 1), ("missing-teardown", 1),
    ("run-timeout", 1), ("checker-cleanup-error", 1)])
def test_supervisor_preserves_status_and_full_logs(monkeypatch, tmp_path, capsys, case, expected):
    monkeypatch.setattr(lab, "require_isolation", Mock())
    monkeypatch.setattr(lab.tempfile, "mkdtemp", lambda **_: str(tmp_path))
    monkeypatch.setattr(lab.signal, "signal", Mock())
    listener = NS(bind=Mock(), getsockname=lambda: ("127.0.0.1", 12345), fileno=lambda: 99, close=Mock())
    monkeypatch.setattr(lab.socket, "socket", lambda: listener)
    if case in {"run-timeout", "checker-cleanup-error"}:
        ticks = iter([0., 0., 0., 121.])
        monkeypatch.setattr(lab.time, "monotonic", lambda: next(ticks))
        monkeypatch.setattr(lab.threading, "Event", lambda: NS(is_set=lambda: False, wait=lambda _: False))
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: io.BytesIO(json.dumps(
        dict(robot_id=lab.ROBOT_ID, physical_acceptance=False)).encode()))
    kills = []
    def killpg(pid, sig):
        kills.append((pid, sig))
        if case == "checker-cleanup-error" and pid == 124:
            raise PermissionError("mock cleanup failure")
    monkeypatch.setattr(lab.os, "killpg", killpg)
    def spawn(argv, **kwargs):
        if case == "spawn":
            raise OSError("startup spawn failure")
        is_lab = Path(argv[-1]).name == "teleop_lab.py"
        kwargs["stdout"].write("first line\n" + "middle\n" * 100 + "last line\n")
        proc = NS(pid=123 if is_lab else 124, returncode=7 if case == "checker" and not is_lab else 0)
        proc.poll = lambda: ((0 if case == "startup" else None) if is_lab else
                            (None if case in {"run-timeout", "checker-cleanup-error"} else
                             7 if case == "checker" else 0))
        proc.send_signal = Mock()
        def wait(timeout):
            if case == "forced" and not kills:
                raise subprocess.TimeoutExpired("lab", timeout)
            proc.returncode = 3 if case == "lab" else 0
            if case != "missing-teardown":
                (tmp_path / "teardown.json").write_text(json.dumps({"teardown": {"passed": case != "teardown"}}))
            return proc.returncode
        proc.wait = wait
        return proc
    monkeypatch.setattr(lab.subprocess, "Popen", spawn)
    assert lab.supervise() == expected
    output = capsys.readouterr().out
    assert '"passed": ' + ("true" if expected == 0 else "false") in output
    assert '"evidence_dir":' in output
    if case != "spawn":
        assert "first line\n" + "middle\n" * 100 + "last line" in output
    if case == "forced":
        assert kills == [(123, signal.SIGKILL)]
    if case == "checker-cleanup-error":
        assert (tmp_path / "teardown.json").exists(), "lab cleanup must survive checker cleanup error"
    assert json.loads((tmp_path / "run.json").read_text())["lab_run"]["returncode"] == expected


def test_supervisor_checks_isolation_before_binding(monkeypatch):
    monkeypatch.setenv("ROS_DOMAIN_ID", "0")
    bind = Mock()
    monkeypatch.setattr(lab.socket, "socket", bind)
    with pytest.raises(AssertionError):
        lab.supervise()
    bind.assert_not_called()
