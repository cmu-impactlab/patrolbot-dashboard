# PatrolBot Dashboard

Custom web dashboard for the PatrolBot: a widget-based UI that hides ROS
terminology from normal users while giving researchers and administrators the
diagnostics they need.

```
Browser ──HTTP/WS──▶ Dashboard server (FastAPI) ◀──outbound WS── RPi5 web bridge ──ROS 2──▶ robot
                                 ▲
                                 └──outbound WS── mock robot (ROS-free, for development)
```

The browser only ever talks to the dashboard server. Robots (real bridge or
mock) dial **out** to the server at `/ws/robot`; browsers connect to `/ws/ui`.

## Layout

| Path | Purpose |
|------|---------|
| `frontend/` | React 19 + TypeScript + Vite dashboard UI |
| `server/` | FastAPI server: telemetry hub, command broker, recorder, REST API, SQLite/PostgreSQL persistence, OIDC auth |
| `mock-robot/` | ROS-free simulated robot speaking the same WebSocket protocol |
| `ros2_ws/src/patrolbot_web_bridge/` | ROS 2 node that runs on the RPi5 |
| `shared/schemas/` | Protocol contract: `protocol.md` + golden JSON fixtures |
| `infrastructure/` | Docker Compose (local + production/nginx TLS) |
| `docs/` | Architecture and Pi deployment notes |
| `scripts/` | Dev helpers (stand-in OIDC IdP for local auth testing) |

## Quick start (development)

```bash
make setup          # create server venv + npm install
make server         # dashboard server on :8000
make mock           # mock robot (separate terminal)
make frontend       # Vite dev server on :5173 (separate terminal)
```

Open http://localhost:5173.

## Docker

The whole stack ships as containers — the server image bundles the built
frontend and a local copy of the map.

```bash
cd infrastructure

# Local: containerized server on :8000 (host network)
docker compose -f docker-compose.local.yml up -d --build server

# Add the mock robot for development
docker compose -f docker-compose.local.yml --profile mock up -d

# Production: nginx TLS terminator + server (self-signed certs, certbot-ready)
./nginx/generate-certs.sh
docker compose -f docker-compose.production.yml up -d --build
```

Runtime configuration lives in `infrastructure/.env` (gitignored; see
`.env.example`) — auth mode, OIDC client, allowlist, database URL, and the
static map path.

## Tests

```bash
make test           # server pytest + mock pytest + frontend vitest
make smoke          # end-to-end: server + mock + WS assertion + vite build
```

## Features

All five build phases are implemented:

- **Phase 1 — Dashboard + mock.** Widget grid (react-grid-layout) with a live
  canvas map, robot status, battery estimation, system health, alerts, Pi
  stats, and diagnostics. Custom dashboards persist per user; alerts move to a
  *Seen* section when read; widgets resize from both bottom corners.
- **Phase 2 — Live telemetry.** The `patrolbot_web_bridge` ROS 2 node streams
  pose, path, LiDAR, battery, base state, diagnostics, and resources from the
  real RPi5. The map is served locally (never streamed off the robot — that
  starves `/scan`).
- **Phase 3 — Motion commands.** Goal-based only (navigate-to, set location,
  stop/resume) over a versioned `command.*` protocol with an audit log,
  duplicate protection, and ack/result timeouts. **No `/cmd_vel` anywhere.**
  The bridge executes commands only with `WEB_BRIDGE_ENABLE_COMMANDS=1`, which
  should be set only with someone physically present holding the e-stop.
  Map pose picks use RViz-style orientation drag (press to place, drag to aim).
- **Phase 4 — Recording & playback.** Server-side recorder with per-channel
  selection (pose/LiDAR/path/battery/base state/diagnostics/events; camera
  video reserved), start/stop, CSV export, and ghost-robot replay on the map
  with a time slider.
- **Phase 5 — Production hardening.** nginx TLS compose, PostgreSQL support
  (connection-string swap), and OIDC (code + PKCE) auth with a login-first
  gate. Access is restricted to an Andrew ID allowlist.

See `shared/schemas/protocol.md` for the WebSocket contract and `docs/` for
architecture and Pi deployment notes.

## Safety

- Motion is **goal-based only**; there is no direct velocity control path.
- The bridge is fail-closed: it refuses commands unless explicitly enabled at
  the robot, and never enables command execution by default.
- The robot never streams its ~7 MB map; the server serves a local copy.
- Never run the mock and the real bridge at once — both claim the same
  `robot_id`.
