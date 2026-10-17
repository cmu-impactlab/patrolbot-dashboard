"""Drive only the isolated software lab; prove real ROS subscription receipts."""
import asyncio
import json
import os
from pathlib import Path
import time
from uuid import uuid4

import httpx
import websockets


async def main():
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
                    frame = json.loads(await asyncio.wait_for(socket.recv(), timeout=5))
                    if frame["type"] == "command.ack" and frame["data"]["command_id"] == command_id:
                        assert frame["data"]["accepted"] is accepted, frame
                        return frame
                raise AssertionError("No exact-session acknowledgement")

            async def finish(command_id, expect_stop):
                # A browser release or producer-loss revoke makes the server
                # issue its own audited teleop_stop: ownership closes only after
                # BOTH the session result and that stop's result. The F710 path
                # self-closes with the session result alone.
                session_done = False
                stop_done = not expect_stop
                deadline = time.monotonic() + 6
                while not (session_done and stop_done):
                    if time.monotonic() > deadline:
                        raise AssertionError("Manual session did not close with expected terminals")
                    frame = json.loads(await asyncio.wait_for(socket.recv(), timeout=6))
                    if frame["type"] == "command.result":
                        if frame["data"]["command_id"] == command_id:
                            session_done = True
                        else:
                            stop_done = True

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
                await finish(command_id, expect_stop=True)

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
            await finish(command_id, expect_stop=True)
            results.append("producer_loss_zero_received")

            command_id = await start()
            await stream(command_id, .1, 0., duration=.15)
            begin = time.monotonic()
            await client.post("/lab/fault", json={"fault": "f710"})
            await wait_receipt("/patrolbot/web_teleop_active", lambda item: item["active"] is False, begin)
            await wait_receipt("/cmd_vel_out", lambda item: item["linear"] == -.12, begin)
            await finish(command_id, expect_stop=False)
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
