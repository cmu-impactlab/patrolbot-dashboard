"""Drive only the isolated software lab; prove real ROS subscription receipts."""
import asyncio
import json
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


async def finish(socket, client, robot, command_id, expect_stop, timeout=6):
    deadline = time.monotonic() + timeout

    def remaining():
        budget = deadline - time.monotonic()
        assert budget > 0, "Manual session did not close with expected terminals"
        return budget

    expected = {command_id: {"succeeded", "canceled"} if expect_stop else {"canceled"}}
    if expect_stop:
        while True:
            budget = remaining()
            response = await asyncio.wait_for(
                client.get("/api/commands?limit=500"), timeout=budget)
            response.raise_for_status()
            stop_id = session_stop_id(response.json(), robot, command_id)
            if stop_id is not None:
                expected[stop_id] = {"succeeded"}
                break
            await asyncio.sleep(min(.02, remaining()))
    done = set()
    while done != expected.keys():
        budget = remaining()
        frame = json.loads(await asyncio.wait_for(socket.recv(), timeout=budget))
        matched = terminal_id(frame, robot, expected)
        if matched is not None:
            done.add(matched)


async def main():
    import httpx
    import websockets

    assert os.environ.get("PATROLBOT_ISOLATED_TEST") == "1"
    assert {path.name for path in Path("/sys/class/net").iterdir()} == {"lo"}
    root = "http://127.0.0.1:8027"
    robot = "isolated-web-teleop"
    from app.protocol.envelope import encode
    results = []
    async with httpx.AsyncClient(base_url=root) as client:
        async def wait_ready():
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                result = (await client.get("/lab/ready")).json()
                if result.get("ready"):
                    assert result["has_amcl_pose"] is False
                    return
                await asyncio.sleep(.1)
            raise AssertionError("Isolated ROS bridge did not provide fresh manual capability/data")

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
        async with websockets.connect("ws://127.0.0.1:8027/ws/ui") as socket:
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
            await finish(socket, client, robot, command_id, expect_stop=True)
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
            await asyncio.sleep(.7)

            await client.post("/lab/fault", json={"fault": "sbc_down"})
            await asyncio.sleep(.15)
            command_id = str(uuid4())
            await socket.send(encode("command.request", robot, 1,
                {"command_id": command_id, "command": "teleop_start"}))
            await reply(command_id, accepted=False)
            results.append("missing_hardware_refuses_without_localization_bypass")
            await client.post("/lab/fault", json={"fault": "healthy"})
        data = (await client.get("/lab/status")).json()
        print(json.dumps({"passed": True, "checks": results,
                          "physical_acceptance": False, "simulated_hardware": True,
                          "receiver_samples": {topic: len(items) for topic, items in data["received"].items()}}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
