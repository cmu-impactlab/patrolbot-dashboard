"""Drive only the isolated software lab; prove real ROS subscription receipts."""
import asyncio
import json
import math
import os
from pathlib import Path
import time
from uuid import uuid4


def session_stop_id(audit, robot, command_id):
    """Resolve the sole stop after this start in the newest-first lab audit."""
    starts = [index for index, row in enumerate(audit)
              if row["command_id"] == command_id]
    assert len(starts) == 1, "Exact manual start missing from command audit"
    index = starts[0]
    start = audit[index]
    assert start["robot_id"] == robot and start["command"] == "teleop_start", start
    newer = [row for row in audit[:index] if row["robot_id"] == robot]
    # shortcut: the wire has no parent session ID for stops; require the isolated
    # lab's sole intervening command, upgrade when the protocol exposes a link.
    assert len(newer) <= 1, "Ambiguous commands after manual start"
    if not newer:
        return None
    stop = newer[0]
    assert stop["command"] == "teleop_stop", stop
    for key in ("user_id", "username", "source_ip", "session_generation"):
        assert start.get(key) is not None and stop.get(key) == start[key], (key, stop)
    assert stop["command_id"] and stop["command_id"] != command_id, stop
    assert not stop.get("rejection_reason"), stop
    return stop["command_id"]


def terminal_id(frame, robot, expected):
    """Return only an expected exact robot/command terminal; reject bad outcomes."""
    if frame.get("robot_id") != robot or frame.get("type") != "command.result":
        return None
    data = frame["data"]
    command_id = data.get("command_id")
    if command_id not in expected:
        return None
    assert data.get("outcome") in expected[command_id], frame
    return command_id


def stale_odom_detail(evidence, robot, command_id, after):
    """Validate fault-time evidence, including the two production expiry paths."""
    assert isinstance(evidence, dict), evidence
    assert evidence.get('robot_id') == robot and evidence.get('command_id') == command_id, evidence
    assert evidence.get('fault') == 'odom_stale', evidence
    for key in ('injected_mono', 'safety_started_mono', 'observed_mono', 'terminal_mono',
                'base_at', 'odom_at'):
        value = evidence.get(key)
        assert type(value) in (int, float) and math.isfinite(value), (key, evidence)
    start, end = evidence['safety_started_mono'], evidence['observed_mono']
    assert after <= evidence['injected_mono'] <= start <= end <= evidence['terminal_mono'] <= time.monotonic(), evidence
    assert evidence['injected_mono'] < evidence['base_at'] <= start, evidence
    assert end - evidence['base_at'] <= .5, evidence
    assert evidence['odom_at'] <= start, evidence
    values = evidence.get('odom_values')
    assert isinstance(values, list) and len(values) == 8, evidence
    assert all(type(value) in (int, float) and math.isfinite(value) for value in values), evidence
    for key in ('source_before_ns', 'source_after_ns', 'stamp_sec', 'stamp_nanosec'):
        assert type(evidence.get(key)) is int and evidence[key] >= 0, (key, evidence)
    assert evidence['stamp_nanosec'] < 10**9, evidence
    stamp = evidence['stamp_sec'] * 10**9 + evidence['stamp_nanosec']
    assert stamp <= evidence['source_before_ns'] <= evidence['source_after_ns'], evidence
    source_age = (evidence['source_after_ns'] - stamp) / 1e9
    receipt_before, receipt_after = start - evidence['odom_at'], end - evidence['odom_at']
    assert all(math.isfinite(age) for age in (source_age, receipt_before, receipt_after)), evidence
    reason = evidence.get('reason')
    # Bracket the delegated safety call: either clock can cross .5 s inside it.
    if reason == 'Raw odometry is invalid or stale.':
        assert source_age > .5 and 0 <= receipt_before <= .5, evidence
    else:
        assert reason == 'Raw odometry is missing or stale.' and receipt_after > .5, evidence
    terminal = evidence.get('terminal')
    assert isinstance(terminal, dict) and terminal.get('command_id') == command_id, evidence
    assert terminal.get('outcome') == 'failed' and terminal.get('detail') == reason, evidence
    return reason


async def expected_stale_odom_fault(socket, robot, command_id, evidence, after, timeout=6):
    """Only the deliberately injected odometry fault may expect a failed result."""
    deadline = time.monotonic() + timeout
    detail = stale_odom_detail(evidence, robot, command_id, after)
    while True:
        budget = deadline - time.monotonic()
        assert budget > 0, "Missing exact stale-odometry terminal"
        frame = json.loads(await asyncio.wait_for(socket.recv(), timeout=budget))
        assert time.monotonic() < deadline, "Late stale-odometry terminal"
        if terminal_id(frame, robot, {command_id: {"failed"}}) is not None:
            assert frame["data"].get("detail") == detail, frame
            return


async def finish(socket, client, robot, command_id, expect_stop, timeout=6):
    deadline = time.monotonic() + timeout

    def remaining():
        budget = deadline - time.monotonic()
        assert budget > 0, "Manual session did not close with expected terminals"
        return budget

    # None permits robot-watchdog cancellation, but still requires any audited stop.
    expected = {command_id: {"canceled"} if expect_stop is False else {"succeeded", "canceled"}}
    done = set()
    buffered = []
    stop_id = None
    settled_after = None
    final_check_at = deadline - min(.02, timeout / 10)
    while True:
        remaining()
        audit_started = time.monotonic()
        if expect_stop is not False:
            response = await asyncio.wait_for(
                client.get("/api/commands?limit=500"), timeout=remaining())
            remaining()
            response.raise_for_status()
            audit = response.json()
            resolved = session_stop_id(audit, robot, command_id)
            assert stop_id is None or resolved == stop_id, "Expected stop disappeared from audit"
            stop_id = resolved
            if stop_id is not None:
                expected[stop_id] = {"succeeded"}

        for frame in buffered:
            matched = terminal_id(frame, robot, expected)
            if matched is not None:
                done.add(matched)
        # Retain early stop results (including failures) until its audit identifies it.
        if stop_id is not None or expect_stop is False:
            buffered.clear()

        if done == expected.keys():
            if expect_stop is False or stop_id is not None:
                remaining()
                return
            if expect_stop is None:
                start = next(row for row in audit if row["command_id"] == command_id)
                if start.get("completed_at"):
                    assert start.get("outcome") in expected[command_id], start
                    # command.result precedes terminal()/_finish(). A completed audit
                    # plus settlement lets an already-running revoke finish auditing.
                    # Settle for at least .5 s (send is bounded at .3 s), then
                    # use the overall budget, reserving one poll for final audit.
                    if settled_after is None:
                        settled_after = time.monotonic() + .5
                    if audit_started >= max(settled_after, final_check_at):
                        assert start["outcome"] == "canceled", start
                        assert all(frame["data"].get("outcome") == "canceled"
                                   for frame in buffered
                                   if frame["data"].get("command_id") == command_id)
                        remaining()
                        return

        budget = remaining()
        # Poll the audit during settlement and while an issued stop is still unknown.
        polling = expect_stop is not False and stop_id is None
        if polling and time.monotonic() < final_check_at:
            budget = min(budget, final_check_at - time.monotonic())
        try:
            frame = json.loads(await asyncio.wait_for(
                socket.recv(), timeout=min(.02, budget) if polling else budget))
            remaining()
            if frame.get("type") == "command.result" and frame.get("robot_id") == robot:
                buffered.append(frame)
        except asyncio.TimeoutError:
            if not polling:
                raise


async def main():
    import httpx
    import websockets

    assert os.environ.get("PATROLBOT_ISOLATED_TEST") == "1"
    assert {path.name for path in Path("/sys/class/net").iterdir()} == {"lo"}
    port = int(os.environ.get("TELEOP_LAB_PORT", "8027"))
    root = f"http://127.0.0.1:{port}"
    robot = "isolated-web-teleop"
    from app.protocol.envelope import encode
    results = []
    async with httpx.AsyncClient(base_url=root) as client:
        async def wait_ready(fault="healthy"):
            # Require new bridge-received sensor/idle/lifecycle events for every
            # intent. Publication counters cannot attest runtime freshness.
            after = time.monotonic()
            deadline = time.monotonic() + 20
            result = None
            while time.monotonic() < deadline:
                result = (await client.get(f"/lab/ready?after={after}&fault={fault}")).json()
                if result.get("ready"):
                    assert result["has_amcl_pose"] is False
                    print(json.dumps({"intent_readiness": result}), flush=True)
                    return
                await asyncio.sleep(.1)
            raise AssertionError(f"Isolated ROS bridge readiness deadline: {result}")

        async def wait_receipt(topic, predicate, newer_than):
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                data = (await client.get("/lab/status")).json()
                assert data["physical_acceptance"] is False
                values = data["received"][topic]
                if any(value["received_mono"] >= newer_than and predicate(value) for value in values):
                    return data
                await asyncio.sleep(.02)
            raise AssertionError("No actual ROS subscriber receipt on " + topic)

        await wait_ready()
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws/ui") as socket:
            # Ignore snapshots/telemetry when matching exact command lifecycle.
            async def reply(command_id, accepted=True):
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    frame = json.loads(await asyncio.wait_for(
                        socket.recv(), timeout=max(.001, deadline - time.monotonic())))
                    if (frame["type"] == "command.ack" and frame.get("robot_id") == robot
                            and frame["data"]["command_id"] == command_id):
                        assert frame["data"]["accepted"] is accepted, frame
                        return frame
                raise AssertionError("No exact-session acknowledgement")

            seq = 0

            async def start():
                await wait_ready()
                command_id = str(uuid4())
                await socket.send(encode("command.request", robot, 1,
                    {"command_id": command_id, "command": "teleop_start"}))
                await reply(command_id)
                return command_id

            async def stream(command_id, linear, angular, duration=.45):
                nonlocal seq
                begin = time.monotonic()
                while time.monotonic() - begin < duration:
                    seq += 1
                    await socket.send(encode("teleop.velocity", robot, seq, {
                        "command_id": command_id, "seq": seq, "linear": linear,
                        "angular": angular, "deadman": True}))
                    await asyncio.sleep(.066)
                return begin

            async def release(command_id):
                nonlocal seq
                seq += 1
                begin = time.monotonic()
                await socket.send(encode("teleop.velocity", robot, seq, {
                    "command_id": command_id, "seq": seq, "linear": 0.,
                    "angular": 0., "deadman": False}))
                await wait_receipt("/input/teleop", lambda item: item["linear"] == 0. and item["angular"] == 0., begin)
                await wait_receipt("/patrolbot/web_teleop_active", lambda item: item["active"] is False, begin)
                await finish(socket, client, robot, command_id, expect_stop=True)

            command_id = await start()
            begin = await stream(command_id, .18, 0.)
            await wait_receipt("/input/teleop", lambda item: item["linear"] == .18, begin)
            await wait_receipt("/cmd_vel", lambda item: item["linear"] > 0., begin)
            results.append("forward_input_received_and_native_mux_smoother_forwarded")
            await release(command_id)
            results.append("release_zero_and_evidence_false_received")

            command_id = await start()
            begin = await stream(command_id, 0., .35)
            await wait_receipt("/input/teleop", lambda item: item["angular"] == .35, begin)
            await release(command_id)
            results.append("keyboard_equivalent_angular_input_received")

            command_id = await start()
            begin = await stream(command_id, .12, 0., duration=.2)
            # Stop producing without sending a release. Either server or local
            # watchdog must close input; neither claims physical wheel state.
            after = time.monotonic()
            await wait_receipt("/input/teleop", lambda item: item["linear"] == 0., after)
            await wait_receipt("/patrolbot/web_teleop_active", lambda item: item["active"] is False, after)
            await finish(socket, client, robot, command_id, expect_stop=None)
            results.append("producer_loss_zero_received")

            command_id = await start()
            await stream(command_id, .1, 0., duration=.15)
            begin = time.monotonic()
            await client.post("/lab/fault", json={"fault": "f710"})
            await wait_receipt("/patrolbot/web_teleop_active", lambda item: item["active"] is False, begin)
            await wait_receipt("/cmd_vel_out", lambda item: item["linear"] == -.12, begin)
            await finish(socket, client, robot, command_id, expect_stop=False)
            results.append("physical_joystick_priority_and_web_session_revocation")
            await client.post("/lab/fault", json={"fault": "healthy"})
            await wait_ready()

            await client.post("/lab/fault", json={"fault": "sbc_down"})
            await wait_ready("sbc_down")
            command_id = str(uuid4())
            await socket.send(encode("command.request", robot, 1,
                {"command_id": command_id, "command": "teleop_start"}))
            await reply(command_id, accepted=False)
            results.append("missing_hardware_refuses_without_localization_bypass")
            await client.post("/lab/fault", json={"fault": "healthy"})

            command_id = await start()
            begin = await stream(command_id, .1, 0., duration=.15)
            await wait_receipt('/input/teleop', lambda item: item['linear'] == .1, begin)
            # Keep the producer alive so only the deliberate sensor fault ends input.
            producer = asyncio.create_task(stream(command_id, .1, 0., duration=20))
            try:
                begin = time.monotonic()
                response = await client.post('/lab/fault', json={'fault': 'odom_stale'})
                response.raise_for_status()
                assert response.json()['accepted'] is True
                deadline = time.monotonic() + 3
                observed = None
                while time.monotonic() < deadline:
                    observed = (await client.get(
                        f'/lab/ready?after={begin}&fault=odom_stale')).json()
                    if observed.get('fault_observed'):
                        break
                    await asyncio.sleep(.02)
                else:
                    raise AssertionError(f'Bridge did not observe deliberately stale odometry: {observed}')
                print(json.dumps({'stale_odom_observation': observed}), flush=True)
                await wait_receipt('/input/teleop',
                    lambda item: item['linear'] == 0. and item['angular'] == 0., begin)
                await wait_receipt('/patrolbot/web_teleop_active', lambda item: item['active'] is False, begin)
                await expected_stale_odom_fault(socket, robot, command_id,
                                                observed.get('fault_evidence'), begin)
            finally:
                producer.cancel()
                try:
                    await producer
                except asyncio.CancelledError:
                    pass
            refused_id = str(uuid4())
            await socket.send(encode('command.request', robot, 1,
                {'command_id': refused_id, 'command': 'teleop_start'}))
            refusal = await reply(refused_id, accepted=False)
            assert refusal['data'].get('reason') == 'Manual drive requires fresh base, raw odometry and LiDAR readings.', refusal
            results.append('stale_odometry_failed_zero_inactive_and_refuses_fresh_start')
            await client.post('/lab/fault', json={'fault': 'healthy'})
            await wait_ready()

        data = (await client.get("/lab/status")).json()
        print(json.dumps({"passed": True, "checks": results,
                          "physical_acceptance": False, "simulated_hardware": True,
                          "receiver_samples": {topic: len(items) for topic, items in data["received"].items()}}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
