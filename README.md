# PatrolBot Dashboard

PatrolBot Dashboard is a web interface for monitoring and operating PatrolBot.
It provides live telemetry, mapping, diagnostics, goal-based navigation,
recording, playback, and role-based access without exposing ROS 2 concepts to
normal users.

```text
Browser <-- HTTP/WebSocket --> Dashboard server <-- WebSocket -- Robot bridge <-- ROS 2 --> PatrolBot
                                              ^
                                              +-- Mock robot for local development
```

The browser and robot connect to the dashboard server. The robot always opens
an outbound connection to `/ws/robot`, which makes the same architecture work
on a laptop or behind a production reverse proxy.

## Repository structure

| Path | Purpose |
| --- | --- |
| `frontend/` | React and TypeScript dashboard |
| `server/` | FastAPI API, authentication, telemetry, commands, and persistence |
| `mock-robot/` | ROS-free robot simulator |
| `ros2_ws/src/patrolbot_web_bridge/` | ROS 2 bridge deployed on the robot Pi |
| `infrastructure/` | Local and production Docker Compose configuration |
| `shared/schemas/` | Shared WebSocket protocol and fixtures |

## Environment 1: local development

### Requirements

- Python 3.11 or newer
- Node.js 22 and npm
- GNU Make

Install the Python and frontend dependencies:

```bash
git clone https://github.com/cmu-impactlab/patrolbot-dashboard.git
cd patrolbot-dashboard
make setup
```

Start the development stack in three terminals:

```bash
make server
```

```bash
make mock
```

```bash
make frontend
```

Open <http://localhost:5173>. Local development uses the simulated robot and
does not require login credentials.

To test a different simulation profile, replace `make mock` with:

```bash
server/.venv/bin/python -m mock_robot \
  --server ws://localhost:8000/ws/robot \
  --token dev-token \
  --scenario chaos
```

### Use the real robot locally

Do not run the mock robot and the real bridge at the same time. Both use the
same robot ID.

Start `make server` and `make frontend`, then configure the Pi bridge:

```env
WEB_BRIDGE_SERVER_URL=ws://<laptop-ip>:8000/ws/robot
WEB_BRIDGE_TOKEN=dev-token
WEB_BRIDGE_ENABLE_COMMANDS=0
```

The token must match the dashboard server's `PATROLBOT_ROBOT_TOKEN`. Leave
commands disabled when only telemetry is needed.

## Environment 2: production deployment

### Requirements

- A Linux server with Docker Engine and Docker Compose
- A DNS name pointing to the server
- A TLS certificate for that DNS name
- An OIDC web application with an authorized callback URL

Clone the repository on the server and create the production environment file:

```bash
git clone https://github.com/cmu-impactlab/patrolbot-dashboard.git
cd patrolbot-dashboard
cp infrastructure/.env.production.example infrastructure/.env
chmod 600 infrastructure/.env
```

Set these values in `infrastructure/.env`:

```env
PATROLBOT_ROBOT_TOKEN=<long-random-token>
PATROLBOT_SESSION_SECRET=<long-random-secret>

PATROLBOT_OIDC_ISSUER=https://accounts.google.com
PATROLBOT_OIDC_CLIENT_ID=<oidc-client-id>
PATROLBOT_OIDC_CLIENT_SECRET=<oidc-client-secret>
PATROLBOT_OIDC_REDIRECT_URL=https://patrolbot-dashboard.qatar.cmu.edu/auth/callback
PATROLBOT_OIDC_EMAIL_DOMAIN=andrew.cmu.edu

PATROLBOT_ALLOWED_ORIGINS=https://patrolbot-dashboard.qatar.cmu.edu
PATROLBOT_ALLOWED_USERNAMES=<comma-separated-usernames>
PATROLBOT_OPERATOR_USERNAMES=<comma-separated-operators>
PATROLBOT_ADMIN_USERNAMES=<comma-separated-administrators>
```

The OIDC provider's authorized redirect URI must exactly match
`PATROLBOT_OIDC_REDIRECT_URL`. Users are read-only unless they are listed as an
operator or administrator.

Create the ACME webroot and obtain the TLS certificate before starting nginx:

```bash
sudo install -d -m 0755 /srv/patrolbot-dashboard/acme
sudo certbot certonly --standalone \
  -d patrolbot-dashboard.qatar.cmu.edu
```

Validate and start the production stack:

```bash
docker compose \
  --env-file infrastructure/.env \
  -f infrastructure/docker-compose.production.yml \
  config --quiet

docker compose \
  --env-file infrastructure/.env \
  -f infrastructure/docker-compose.production.yml \
  up -d --build
```

nginx is the only service exposed on the host; the dashboard server is private
to the Compose network. The named `patrolbot-dashboard-data` volume persists
the SQLite database across container recreation.

SQLite is the supported database in every environment. This is a single-host,
single-robot deployment and the live database is around 30 MB, almost all of it
battery samples — there is nothing here that needs a database server. An
unsupported PostgreSQL backend remains in the tree at
`server/app/database/pg.py` for anyone who later needs one; it is not deployed,
not covered by CI, and selected only by setting `PATROLBOT_DATABASE_URL`
explicitly.

Configure the robot Pi to use the public WebSocket endpoint:

```env
WEB_BRIDGE_SERVER_URL=wss://patrolbot-dashboard.qatar.cmu.edu/ws/robot
WEB_BRIDGE_TOKEN=<same value as PATROLBOT_ROBOT_TOKEN>
WEB_BRIDGE_ENABLE_COMMANDS=0
```

The Pi's `web-bridge` Compose service must pass these variables into the
container. Adding a value to `.env` alone does not inject it unless the service
references it.

Recreate the bridge after changing its environment:

```bash
cd /home/ubuntu/patrolbot-repo/docker
docker compose --env-file .env -f docker-compose.yml \
  --profile web-bridge up -d --no-deps --force-recreate web-bridge
```

Verify both sides:

```bash
curl --fail --show-error \
  https://patrolbot-dashboard.qatar.cmu.edu/api/health

docker compose \
  --env-file infrastructure/.env \
  -f infrastructure/docker-compose.production.yml \
  ps
```

A connected deployment reports `"robot_connected": true` from the health
endpoint. See the
[production runbook](infrastructure/nginx/README.md) for certificate renewal,
updates, and backups.

## Tests

```bash
make test
make smoke
```

`make test` runs the server, mock robot, bridge, and frontend test suites.
`make smoke` exercises the local server/mock WebSocket path and builds the
frontend.

Every Python test carries a 60-second timeout (`pytest-timeout`), so a suite
that blocks fails with a traceback instead of stalling. Supported interpreters
are Python 3.11 through 3.14, all four exercised in CI.

## Updating Python dependencies

`server/pyproject.toml` and `mock-robot/pyproject.toml` declare what each needs,
with open lower bounds. The `constraints.txt` beside each records what the image
actually installs, so two builds a month apart are the same build.

What this pins is package *versions*. It is not a bit-for-bit reproducible
build: `python:3.12-slim` is a mutable tag, wheels are not hash-pinned, and the
build backend (`setuptools`) resolves outside the constraints because pip build
isolation does not read them. Pinning those too is a bigger commitment than this
deployment needs; the version drift is what was actually biting.

The two are deliberately used in different places. The production image installs
against the constraints; the CI test matrix installs unconstrained across the
whole supported Python range, because that is what tells us early that an
upstream release has broken us. Pinning CI would silence exactly that signal.

To change a dependency:

1. Edit the relevant `pyproject.toml`.
2. Regenerate that project's `constraints.txt` with the command in its header —
   it runs in the same image the Dockerfile builds from, so the result is about
   production rather than about your laptop.
3. Read the diff. An unexplained version jump is the thing the file exists to
   make visible.
4. `./scripts/check-constraints.sh` to confirm. It checks both projects, that
   both Dockerfiles still install against their constraints, and that the server
   suite passes on the constrained set — installing a pinned version is not the
   same as it working. CI runs the same script.

Frontend dependencies are locked by `frontend/package-lock.json` and installed
with `npm ci`.

## Reserved for later phases

Two features are named here because someone will look for them. Neither is a
half-finished control you can press:

- **Camera and gimbal.** No camera stream, no gimbal control, and no video
  recording. `"video"` is reserved as a recording channel name so the
  Recordings widget can advertise it as coming later
  (`server/app/recordings/recorder.py`), and the checkbox stays disabled. A
  server-side gimbal control-authority prototype was removed in the meantime;
  it will be reintroduced with the camera work rather than kept unwired.
- **Automatic docking.** There is no `dock` command. The robot has no dock-in
  path — its ROS graph offers `/patrolbot/undock` and
  `/patrolbot/hardware_undock` and nothing to drive back onto the charger
  (verified 2026-08-08) — so the robot is driven onto its charger by hand.
  The command, its gate, its UI control and the mock's implementation of it
  were removed rather than left advertising something nothing implements;
  reinstate them alongside a commissioned dock-in action. `undock` **is**
  implemented on the real bridge and is unaffected.

## Safety

- Robot commands are disabled unless `WEB_BRIDGE_ENABLE_COMMANDS=1` reaches
  the bridge container.
- Enable commands only with an operator physically present and holding the
  e-stop.
- Motion is goal-based; the dashboard does not provide a direct velocity path.
- Do not run the mock robot and real bridge with the same robot ID.
- `WEB_BRIDGE_TOKEN` and `PATROLBOT_ROBOT_TOKEN` must match and must be kept
  secret.

The WebSocket protocol is documented in
[`shared/schemas/protocol.md`](shared/schemas/protocol.md).
