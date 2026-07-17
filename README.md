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
| `server/` | FastAPI server: telemetry hub, REST API, SQLite persistence |
| `mock-robot/` | ROS-free simulated robot speaking the same WebSocket protocol |
| `ros2_ws/src/patrolbot_web_bridge/` | ROS 2 node that runs on the RPi5 (Phase 2) |
| `shared/schemas/` | Protocol contract: `protocol.md` + golden JSON fixtures |
| `infrastructure/` | Docker Compose files |
| `docs/` | Architecture and Pi deployment notes |

## Quick start (development)

```bash
make setup          # create server venv + npm install
make server         # dashboard server on :8000
make mock           # mock robot (separate terminal)
make frontend       # Vite dev server on :5173 (separate terminal)
```

Open http://localhost:5173.

## Tests

```bash
make test           # server pytest + mock pytest + frontend vitest
make smoke          # end-to-end: server + mock + WS assertion + vite build
```

## Current scope

Phases 1–2 of the build spec: full widget UI with mock robot, plus live
**read-only** telemetry from the real RPi5. Motion commands (`command.*`) are
reserved in the protocol but not implemented — see `shared/schemas/protocol.md`.
