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
- **Commands are reserved, not implemented**: `command.*` envelope types are
  documented in `shared/schemas/protocol.md`; gateways reject them; the
  Navigation widget renders disabled. Phase 3 adds them server-side with
  acks, audit and duplicate protection.

## Protocol contract

`shared/schemas/protocol.md` + golden fixtures in `shared/schemas/fixtures/`.
Pydantic models (`server/app/protocol/messages.py`) are authoritative;
`frontend/src/types/protocol.ts` mirrors them; both test suites validate the
fixtures, so drift breaks CI on whichever side moved.

## Deviations from the original build spec

- SQLite (via aiosqlite) instead of PostgreSQL for Phases 1–2 — single user,
  single process; the repository layer (`server/app/database/repo.py`) is the
  only file to swap at Phase 5.
- Plain CSS custom properties + Radix primitives instead of shadcn/Tailwind —
  smaller surface, same accessibility, CMU tokens in `themes/tokens.css`.
- Redis omitted (spec allows this while one server process exists).
