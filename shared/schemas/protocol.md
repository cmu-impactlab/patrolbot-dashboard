# PatrolBot Dashboard WebSocket Protocol — version 1

Every WebSocket frame is a JSON **envelope**:

```json
{
  "version": 1,
  "type": "telemetry.pose",
  "robot_id": "patrolbot-01",
  "sequence": 4711,
  "timestamp": "2026-07-17T18:00:00.000Z",
  "data": { }
}
```

- `version` — protocol version, currently `1`. Receivers must reject other versions.
- `type` — message type (below).
- `robot_id` — stable robot identifier.
- `sequence` — per-connection monotonically increasing integer.
- `timestamp` — ISO-8601 UTC with milliseconds.
- `data` — type-specific payload.

Authoritative payload definitions: `server/app/protocol/messages.py` (Pydantic).
Frontend mirror: `frontend/src/types/protocol.ts`.
Golden examples: `shared/schemas/fixtures/*.json` — validated by both pytest
and vitest; edit fixtures together with both definitions.

## Robot → Server  (`/ws/robot?token=…`)

| Type | Rate | Payload summary |
|------|------|-----------------|
| `robot.hello` | on connect (first frame) | `protocol_version`, `capabilities[]`, `map_version`, `software_version` |
| `telemetry.heartbeat` | 1 Hz | `uptime_s` |
| `telemetry.pose` | 10 Hz | `frame_id`, `x`, `y`, `yaw`, `linear_velocity`, `angular_velocity`, `covariance_trace?`, `localized` |
| `telemetry.lidar` | 5 Hz | `angle_min`, `angle_increment`, `ranges[]` (≤360, metres, `null` = no return) |
| `telemetry.path` | on change / 2 Hz | `frame_id`, `points[[x,y],…]`, `goal?{x,y,yaw}` |
| `telemetry.battery` | 1 Hz | `voltage`, `current?`, `percentage?`, `charging` |
| `telemetry.base_state` | 1 Hz | mapped `patrolbot_interfaces/BaseState` fields (see messages.py) |
| `telemetry.diagnostics` | 1 Hz | `items[{name, level, message, values?}]`, level ∈ OK/WARN/ERROR/STALE |
| `telemetry.resources` | 1 Hz | `cpu_percent`, `memory_percent`, `cpu_temp_c?`, `disk_percent`, `wifi_signal_dbm?` |
| `telemetry.map` | on server request + on change | `map_version`, `name`, `resolution`, `width`, `height`, `origin{x,y,yaw}`, `rle[[value,count],…]` |

Server replies to `robot.hello` with `server.hello_ack` (`data.want_map: bool`).
The robot sends `telemetry.map` when `want_map` is true or the map changes.

## Server → Browser  (`/ws/ui`)

All robot telemetry types are re-broadcast unchanged, plus:

| Type | When | Payload summary |
|------|------|-----------------|
| `server.snapshot` | on browser connect | full current state (no map data — fetch `GET /api/map`) |
| `state.connection` | on change | `state` ∈ `online` / `stale` / `offline`, `last_seen` |
| `state.robot_status` | on change | `status` ∈ ready/navigating/recording/docked/charging/paused/needs_attention/offline, `detail` |
| `state.system_health` | on change | `overall`, `subsystems[{id, label, level, message, action?, updated_at}]` |
| `event.append` | as they occur | `id`, `ts`, `severity` ∈ info/warning/critical, `title`, `message` |

Connection staleness (server-side, from heartbeat age): `<3 s` online,
`3–10 s` stale, `>10 s` (or socket closed) offline.

## Commands (Phase 3)

Browser → server → robot; responses flow back along the same path. Every
frame carries a `command_id` (UUID minted by the browser) for correlation.

| type | direction | data |
|---|---|---|
| `command.request` | browser→robot | `command_id`, `command` ∈ navigate_to_pose/set_initial_pose/stop, `goal?` `{x, y, yaw?}`, `takeover?` (claim the single-operator lease from the current holder) |
| `command.ack` | robot→browser | `command_id`, `accepted`, `reason?` |
| `command.progress` | robot→browser | `command_id`, `stage`, `detail?`, `distance_remaining?` |
| `command.result` | robot→browser | `command_id`, `outcome` ∈ succeeded/failed/rejected/canceled/timeout, `detail?` |

Server-side broker rules:

- A request is **rejected by the server** (synthesized `command.ack`
  `accepted=false`) when the robot is not online, the payload is invalid,
  `goal` is missing for a command that needs one, or the `command_id` was
  already seen (duplicate protection).
- Every request is written to the `command_audit` table before forwarding.
- If no `command.ack` arrives within 5 s, or no `command.result` within 120 s,
  the server synthesizes `command.result` `outcome=timeout` and closes out the
  command. Late robot replies for a closed command are dropped.
- A new `navigate_to_pose` while one is active implicitly replaces it
  (Nav2 preemption semantics); `stop` cancels any active navigation.
- There is **no velocity teleop command** — goal-based navigation only, by
  design. The physical e-stop is the only emergency stop.
- The bridge executes commands only when `WEB_BRIDGE_ENABLE_COMMANDS=1`;
  otherwise it acks `accepted=false` with an explanatory reason.

## Transport rules

- Robots authenticate with `?token=` (shared secret, env-configured).
- A second robot connection with the same `robot_id` supersedes the first.
- Browser fan-out is lossy for high-rate telemetry (drop-oldest per client);
  `telemetry.map`, snapshots, events and state changes are never dropped.
- Occupancy grid is RLE-encoded `[value, count]` pairs over row-major int8
  cells (`-1` unknown, `0` free, `100` occupied), row 0 = map origin row,
  ROS convention (+y up — renderers must flip).
