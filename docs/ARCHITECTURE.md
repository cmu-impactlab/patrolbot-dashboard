# PatrolBot Dashboard — Architecture (Phases 1–2)

```
┌──────────────────────────────────────────────────────────┐
│                     Browser (React)                      │
│   widgets ── Zustand stores ── WS client ── TanStack     │
└──────────────────────────┬───────────────────────────────┘
              /ws/ui  +  /api/*  (HTTP/WS, one origin)
┌──────────────────────────▼───────────────────────────────┐
│               Dashboard server (FastAPI)                 │
│  TelemetryHub (fan-out) · status/health derivation ·     │
│  battery estimator · SQLite (users/layouts/events/       │
│  battery history) · single-user auth stub                │
└──────────────────────────▲───────────────────────────────┘
              /ws/robot?token=…   (OUTBOUND from robot)
┌──────────────────────────┴───────────────────────────────┐
│  patrolbot_web_bridge (RPi5, 4th compose container)      │
│  subscribes /map /amcl_pose /odom /scan /plan /battery   │
│  /diagnostics /patrolbot/base_state → normalize →        │
│  throttle → WS frames.  READ-ONLY: no publishers,        │
│  no service/action clients.                              │
└──────────────────────────▲───────────────────────────────┘
                    ROS 2 Jazzy graph (Pi-local, LOCALHOST discovery)
┌──────────────────────────┴───────────────────────────────┐
│  Existing robot stack: patrolbot_bridge (SBC TCP link),  │
│  Nav2, AMCL — unchanged.  SBC single-client rule intact. │
└──────────────────────────────────────────────────────────┘
```

The mock robot (`mock-robot/`) speaks the identical `/ws/robot` protocol, so
the server and frontend cannot tell it from the real bridge — that is the
whole Phase 1 development story.

## Key design decisions

- **Connection direction**: the robot dials out to the server. Nothing ever
  connects into the robot network; browsers never reach ROS or DDS.
- **SBC health without extra connections**: the SBC hardware server accepts a
  single TCP client (the existing `patrolbot_bridge`). The dashboard derives
  "Drive base" health purely from `/patrolbot/base_state.telemetry_age` /
  `hardware_state_valid`, which that bridge already computes.
- **Battery "remaining time" is always labeled Estimated**: the PatrolBot's
  lead-acid pack reports no reliable state of charge (manual: 24 V nominal,
  1–3 h runtime). The server runs a bounded linear regression on voltage
  trend (and percentage when a simulator provides one).
- **Lossy fan-out**: each browser gets a bounded queue; high-rate telemetry
  drops oldest-first for slow tabs, while snapshots, events, state changes
  and maps are never dropped.
- **Localization degradation**: with Nav2/AMCL down the bridge sends
  odom-frame poses marked `localized: false`; the UI says the position is
  approximate instead of lying.
- **Commands are goal-based only** (Phase 3): `command.request/ack/progress/
  result` flow browser → CommandBroker → robot and back. The broker validates,
  audits every request to SQLite, rejects duplicates, and closes out missing
  acks (5 s) and results (120 s) with synthesized timeouts. There is no
  velocity teleop path anywhere in the stack, and the bridge executes
  commands only when `WEB_BRIDGE_ENABLE_COMMANDS=1` — otherwise it declines
  each request with a plain-language reason. "Return to Dock" stays disabled
  because the robot has no autonomous dock-in (only a guarded `Undock`).

## Protocol contract

`shared/schemas/protocol.md` + golden fixtures in `shared/schemas/fixtures/`.
Pydantic models (`server/app/protocol/messages.py`) are authoritative;
`frontend/src/types/protocol.ts` mirrors them; both test suites validate the
fixtures, so drift breaks CI on whichever side moved.

## Phase 4 — recording & playback

Entirely server-side (`server/app/recordings/recorder.py`): while a
recording is active the hub feeds selected channels (pose, laser scan,
path, battery, drive-base state, system reports, alerts) through per-channel
decimation into SQLite/PostgreSQL. REST under `/api/recordings` covers
start (with a channel list), stop, list, detail, CSV export, and delete;
an interrupted recording is closed out at startup. The Recordings widget
replays a session on the Live Map as a ghost robot with a time slider.
Camera video is a reserved channel — advertised in the UI, not implemented.
The robot is asked for nothing extra (no rosbag).

## Phase 5 — production pieces

- `infrastructure/docker-compose.production.yml`: nginx TLS front
  (`infrastructure/nginx/`, self-signed dev certs via `generate-certs.sh`,
  certbot-ready) + the server image.
- PostgreSQL: `PATROLBOT_DATABASE_URL=postgresql://…` selects
  `database/pg.py` (asyncpg); SQLite stays the default. Same interface,
  verified by `tests/test_postgres.py` against a real Postgres.
- Auth: `PATROLBOT_AUTH_MODE=oidc` turns on an OpenID Connect
  code+PKCE flow (`authentication/oidc.py`) — point the issuer at CMU's
  IdP, register `/auth/callback`, list admins in
  `PATROLBOT_ADMIN_USERNAMES`. Sessions are HMAC-signed cookies; in oidc
  mode all `/api/*` (except health) and `/ws/ui` require one. Local
  single-user mode remains the default.

## Deviations from the original build spec

- SQLite (via aiosqlite) instead of PostgreSQL for Phases 1–2 — single user,
  single process; the repository layer (`server/app/database/repo.py`) is the
  only file to swap at Phase 5.
- Plain CSS custom properties + Radix primitives instead of shadcn/Tailwind —
  smaller surface, same accessibility, CMU tokens in `themes/tokens.css`.
- Redis omitted (spec allows this while one server process exists).
