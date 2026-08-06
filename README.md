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
