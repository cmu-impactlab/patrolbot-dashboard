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

## Reserved for Phase 3 — commands (NOT implemented)

`command.request`, `command.ack`, `command.progress`, `command.result`.
Each carries a unique `command_id` (UUID) for correlation, and will require:
immediate ack, progress updates, final result, timeout, audit entry, and
duplicate-command protection. Gateways currently reject `command.*` frames.

## Transport rules

- Robots authenticate with `?token=` (shared secret, env-configured).
- A second robot connection with the same `robot_id` supersedes the first.
- Browser fan-out is lossy for high-rate telemetry (drop-oldest per client);
  `telemetry.map`, snapshots, events and state changes are never dropped.
- Occupancy grid is RLE-encoded `[value, count]` pairs over row-major int8
  cells (`-1` unknown, `0` free, `100` occupied), row 0 = map origin row,
  ROS convention (+y up — renderers must flip).
