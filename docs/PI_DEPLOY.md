# Deploying patrolbot_web_bridge to the RPi5 (Phase 2)

The bridge runs as a **4th compose service** next to the existing three
(`bringup`, `bridge`, `navigation`) in `patrolbot-repo/docker/docker-compose.yml`.
That edit lives in **patrolbot-repo** and should be its own reviewed change —
this repo only ships the package and this runbook.

## 0. Prerequisites

- Dashboard server reachable from the Pi (dev laptop on the same LAN):
  `make server` on the laptop, note its IP.
- `ssh robot-pi` works (see patrolbot-repo SKILLS/SSH.md).

## 1. Sync the package to the Pi

```bash
rsync -av --delete \
  ros2_ws/src/patrolbot_web_bridge/ \
  robot-pi:/home/ubuntu/patrolbot-dashboard/ros2_ws/src/patrolbot_web_bridge/
```

## 2. Image dependencies

The bridge needs `websockets` and `psutil` inside the container. If
`PATROLBOT_IMAGE` lacks them, build a derived image on the Pi:

```bash
ssh robot-pi 'docker build -t patrolbot-web-bridge:latest -f- . <<EOF
ARG BASE
FROM ${PATROLBOT_IMAGE}
RUN pip install --break-system-packages websockets psutil
EOF'
```

(or add the two pip packages to the main image build in patrolbot-repo.)

## 3. Compose service (add to patrolbot-repo/docker/docker-compose.yml)

> **Status 2026-07-17:** this service is now merged into
> `patrolbot-repo/docker/docker-compose.yml` behind the `web-bridge`
> profile (uncommitted there; see that repo's TODO.md). Start it with
> `docker compose --profile web-bridge up -d web-bridge`. The snippet below
> is kept for reference.
>
> **Stable address:** `WEB_BRIDGE_SERVER_URL` currently points at the
> operator laptop's VPN IP, which changes between sessions. Before other
> people depend on the dashboard, give the server a stable address
> (reserved VPN IP or a DNS name) and set it once in `docker/.env`.

```yaml
  web-bridge:
    <<: *common
    container_name: patrolbot-web-bridge
    environment:
      ROS_DOMAIN_ID: "0"
      RMW_IMPLEMENTATION: rmw_fastrtps_cpp
      ROS_AUTOMATIC_DISCOVERY_RANGE: LOCALHOST
      PATROLBOT_ROLE: web-bridge
      WEB_BRIDGE_SERVER_URL: "${WEB_BRIDGE_SERVER_URL:?set in docker/.env}"
      WEB_BRIDGE_TOKEN: "${WEB_BRIDGE_TOKEN:?set in docker/.env}"
    command: ["bash", "-lc",
      "source /opt/ros/jazzy/setup.bash && source /opt/patrolbot_ws/install/setup.bash 2>/dev/null; export PYTHONPATH=/opt/web_bridge:$PYTHONPATH && python3 -m patrolbot_web_bridge.bridge_node --ros-args --params-file /opt/web_bridge/patrolbot_web_bridge/../config/web_bridge.yaml"]
    volumes:
      - /home/ubuntu/patrolbot-dashboard/ros2_ws/src/patrolbot_web_bridge:/opt/web_bridge:ro
```

Notes:
- `<<: *common` inherits host network / LOCALHOST discovery — required to see
  the Pi-local ROS graph; the outbound WebSocket to the laptop still works
  because the container shares the host network.
- Running via `PYTHONPATH` avoids a colcon build on the Pi; the package is
  also colcon-buildable (`ament_python`) if you prefer installing it into the
  workspace image. `patrolbot_interfaces` must be sourced for BaseState
  telemetry — without it the bridge runs but skips base_state (and the
  dashboard shows "Drive base: no data").
- Add to `docker/.env`:
  `WEB_BRIDGE_SERVER_URL=ws://<laptop-ip>:8000/ws/robot`
  `WEB_BRIDGE_TOKEN=<same PATROLBOT_ROBOT_TOKEN as the server>`

```bash
ssh robot-pi 'cd patrolbot-repo/docker && docker compose up -d web-bridge'
```

## 4. Acceptance checklist (with the robot powered)

1. Dashboard shows the real `cmuq_1st_floor` map.
2. Pushing/joystick-driving the robot moves the pose marker; heading matches.
3. `ros2 topic echo /battery --once` voltage matches the Battery widget.
4. Stop the `patrolbot-bridge` container → dashboard shows a Drive-base fault
   in plain language; restart → recovers.
5. `docker stop patrolbot-web-bridge` → dashboard Offline within 10 s;
   `docker start` → reconnects and re-sends the map automatically.
6. `docker stats patrolbot-web-bridge` CPU stays under ~5 %.
7. Confirm no new connections to the SBC: `ss -tn dst 10.0.0.1:7272` on the
   Pi still shows exactly one ESTAB pair (the existing hardware bridge).

## Safety posture

The bridge is read-only by default: without `WEB_BRIDGE_ENABLE_COMMANDS=1`
it has no publishers, no service or action clients, and declines every
inbound `command.request` with a plain-language reason. Setting the flag
enables the goal-based command path only — Nav2 `NavigateToPose`,
`/initialpose`, and goal cancel — with pre-checks that fail closed on
e-stop pressed, motors off, or missing/invalid base_state. There is no
`/cmd_vel` path. Enable the flag only with someone physically present at
the robot; the physical E-stop remains the only emergency stop.
