# Production server deployment

The production stack exposes only nginx on ports 80 and 443. The FastAPI
server stays on the private Compose network at `server:8000`.

The commands below assume:

- DNS for `patrolbot-dashboard.qatar.cmu.edu` points to this server.
- Inbound TCP ports 80 and 443 are open.
- Docker Engine and the Docker Compose plugin are installed.
- Certbot is installed on the host.

## First deployment

From a clean checkout on the server:

```bash
git pull --ff-only

cp infrastructure/.env.production.example infrastructure/.env
chmod 600 infrastructure/.env
```

Edit `infrastructure/.env` and fill the robot token, session secret, Google
OIDC client ID, Google OIDC client secret, and user role lists. Generate the
two independent application secrets with:

```bash
openssl rand -hex 32
```

Register this exact Google OAuth redirect URI:

```text
https://patrolbot-dashboard.qatar.cmu.edu/auth/callback
```

The robot token must also be installed on the RPi5 as `WEB_BRIDGE_TOKEN`.
Point the bridge at:

```text
wss://patrolbot-dashboard.qatar.cmu.edu/ws/robot
```

Create the ACME webroot and obtain the first certificate before starting
nginx, because the production nginx configuration intentionally refuses to
start without the real certificate:

```bash
sudo install -d -m 0755 /srv/patrolbot-dashboard/acme
sudo certbot certonly --standalone \
  -d patrolbot-dashboard.qatar.cmu.edu
```

Validate the resolved configuration, then build and start the stack:

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

Confirm that both containers are healthy and that HTTPS responds:

```bash
docker compose \
  --env-file infrastructure/.env \
  -f infrastructure/docker-compose.production.yml \
  ps

curl --fail --show-error \
  https://patrolbot-dashboard.qatar.cmu.edu/api/health
```

After nginx is serving the ACME webroot, switch the certificate renewal
configuration from the bootstrap standalone server to that webroot and test
renewal:

```bash
sudo certbot reconfigure \
  --cert-name patrolbot-dashboard.qatar.cmu.edu \
  --webroot \
  --webroot-path /srv/patrolbot-dashboard/acme

sudo install -d -m 0755 /etc/letsencrypt/renewal-hooks/deploy
sudo ln -sfn \
  "$(realpath infrastructure/nginx/reload-after-renewal.sh)" \
  /etc/letsencrypt/renewal-hooks/deploy/patrolbot-dashboard-nginx

sudo certbot renew --dry-run
```

The deploy hook reloads nginx after a successful renewal so it immediately
serves the new certificate. The hook resolves the checkout path through its
symlink, so it does not need a hard-coded installation directory. The Docker
socket is not exposed to the nginx or Certbot containers.

## Updating

Deploy only a reviewed commit from `main`:

```bash
git pull --ff-only

docker compose \
  --env-file infrastructure/.env \
  -f infrastructure/docker-compose.production.yml \
  up -d --build

docker compose \
  --env-file infrastructure/.env \
  -f infrastructure/docker-compose.production.yml \
  ps
```

The named `patrolbot-dashboard-data` volume persists the SQLite database
across container recreation. Back it up before upgrades that change stored
data.
