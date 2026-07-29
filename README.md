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

## Running the dashboard

The system has three moving parts: the **server** (browsers connect to it), a
**robot** (either the mock or the real Pi bridge dials *out* to the server),
and — in development — the **Vite dev server** for hot-reloading the frontend.
Pick the scenario that matches what you're doing.

First-time setup (once):

```bash
make setup          # creates server/.venv, installs server + mock, npm install
```

### Scenario A — Local dev with the mock robot (fastest inner loop)

No hardware, no auth. Three terminals:

```bash
make server         # dashboard server on :8000 (auth defaults to "local" — no login)
make mock           # ROS-free mock robot, dials ws://localhost:8000/ws/robot
make frontend       # Vite dev server on :5173, proxies API/WS to :8000
```

Open **http://localhost:5173**. You get a live simulated patrol, battery
cycle, diagnostics, and a periodic disconnect drill. Use `--scenario` to shape
the mock (`calm`, `full`, `chaos`):

```bash
server/.venv/bin/python -m mock_robot --server ws://localhost:8000/ws/robot \
  --token dev-token --scenario chaos
```

### Scenario B — Local dev with the **real Pi**

Same as A, but instead of `make mock` you run the ROS 2 bridge **on the Pi**,
pointed at this laptop. **Never run the mock and the real bridge at the same
time** — both claim `robot_id: patrolbot-01` and the newer connection evicts
the older one.

1. Start the server and (optionally) the Vite dev server on the laptop:

   ```bash
   make server         # :8000
   make frontend       # :5173  (optional; the built UI is also served from :8000)
   ```

2. Find the laptop's address the Pi can reach (on CMU VPN this is the
   `cscotun0` address and **it changes between sessions**):

   ```bash
   ip -4 addr show cscotun0 | grep -oP 'inet \K[\d.]+'
   ```

3. On the Pi, run the bridge as the 4th Docker service (see
   [`docs/PI_DEPLOY.md`](docs/PI_DEPLOY.md)), overriding the server URL to
   match step 2. The token must equal the server's `PATROLBOT_ROBOT_TOKEN`
   (default `dev-token`):

   ```bash
   WEB_BRIDGE_SERVER_URL=ws://<laptop-ip>:8000/ws/robot \
   WEB_BRIDGE_TOKEN=dev-token \
   # ... launched inside the patrolbot-repo compose stack
   ```

   The dashboard now shows the **real** map, pose, path, LiDAR, battery, and
   base state. The map is served locally by the server (`PATROLBOT_STATIC_MAP_YAML`)
   — the robot never streams its ~7 MB `/map`, which would starve `/scan`. The
   bridge's own `/map` subscription stays off unless `WEB_BRIDGE_SUBSCRIBE_MAP=1`.

4. **Motion commands are OFF by default.** The bridge only executes goal-based
   commands with `WEB_BRIDGE_ENABLE_COMMANDS=1`, and that should be set **only
   with someone physically at the robot holding the e-stop** — never remotely.
   Read-only telemetry needs no such flag.

Key bridge environment variables (override the `config/web_bridge.yaml`
defaults):

| Variable | Default | Purpose |
|----------|---------|---------|
| `WEB_BRIDGE_SERVER_URL` | `ws://192.168.1.100:8000/ws/robot` | Where to dial the server |
| `WEB_BRIDGE_TOKEN` | `dev-token` | Must match the server's `PATROLBOT_ROBOT_TOKEN` |
| `WEB_BRIDGE_ENABLE_COMMANDS` | unset (OFF) | `1` enables motion execution (e-stop present only) |
| `WEB_BRIDGE_SUBSCRIBE_MAP` | unset (OFF) | `1` re-enables the on-robot `/map` subscription |

### Scenario C — Docker (containerized server)

The server image bundles the built frontend and a local copy of the map, so
there's no separate Vite server. Runtime config comes from
`infrastructure/.env` (gitignored — copy `.env.example` and edit):

```bash
cd infrastructure
cp .env.example .env          # then edit: session secret, allowlist, auth mode, DB URL

# Containerized server on :8000 (host network so the Pi/mock can reach it)
docker compose -f docker-compose.local.yml up -d --build server

# Optionally add the mock robot (dev only)
docker compose -f docker-compose.local.yml --profile mock up -d
```

Open **http://localhost:8000**. To use the real Pi instead of the mock, just
don't start the mock profile and follow Scenario B step 3 (point the Pi bridge
at this host on :8000). The shipped `.env.example` runs **OIDC** auth against
the dev stand-in IdP (see below) — set `PATROLBOT_AUTH_MODE=local` to skip
login while developing.

### Scenario D — Testing OIDC auth locally

Real CMU OIDC credentials aren't issued yet, so a stand-in IdP is included for
end-to-end auth testing. It grants sign-in as any Andrew ID you type, and the
server's allowlist (`PATROLBOT_ALLOWED_USERNAMES=yousefh,efeoflus`) still
decides who actually gets in.

```bash
server/.venv/bin/python scripts/dev_idp.py --port 9100   # stand-in IdP (dev only — never deploy)
```

With `infrastructure/.env` set to OIDC (as in `.env.example`), the login
screen appears first; sign in, and allowed users reach the dashboard while
everyone else gets a 403.

**Production auth is Google OAuth2 restricted to `@andrew.cmu.edu`.** Google is
a standard OIDC provider, so the same code+PKCE flow is used — point
`PATROLBOT_OIDC_ISSUER=https://accounts.google.com`, set the client ID/secret
from the Google Cloud Console, register `https://<host>/auth/callback` as the
authorized redirect URI, and keep `PATROLBOT_OIDC_EMAIL_DOMAIN=andrew.cmu.edu`.
Sign-in requires a **verified** email ending in `@andrew.cmu.edu`; the
leading-`@` anchor rejects look-alike domains. See
`infrastructure/.env.example` for the full Cloud Console walkthrough.

**Roles are read-only by default.** An authenticated user is an *observer*
(telemetry only) unless listed in `PATROLBOT_OPERATOR_USERNAMES` (operator) or
`PATROLBOT_ADMIN_USERNAMES` (administrator). Only operators/administrators may
send navigation, pose, or stop commands.

### Scenario E — Production (nginx + TLS)

```bash
cd infrastructure
./nginx/generate-certs.sh                                  # self-signed (certbot-ready)
docker compose -f docker-compose.production.yml up -d --build
```

nginx terminates TLS and proxies HTTP + WSS to the server. Set a real
`PATROLBOT_SESSION_SECRET`, a PostgreSQL `PATROLBOT_DATABASE_URL` if desired,
and the real OIDC client in `.env` before exposing it.

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
  video reserved), start/stop, export as a zip of per-channel CSVs, and
  replay in its own tab with a time slider.
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
