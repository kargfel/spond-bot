# Setup & Deployment

## Prerequisites

- Docker and Docker Compose v2
- A Spond account (one per user you want to automate)
- A domain or server if exposing publicly

## Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/kargfel/Spond.git
cd Spond
cp .env.example .env   # or create .env from scratch (see below)
```

### 2. Create `.env`

```env
# PostgreSQL connection (matches docker-compose.yml)
DATABASE_URL=postgresql+asyncpg://spond:yourdbpassword@db:5432/spond_bot
DB_PASSWORD=yourdbpassword

# Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
FERNET_KEY=your-fernet-key-here

# Random secret used for internal API access
API_KEY=your-random-api-key-here

# Public domain (used for Secure cookie; leave as localhost for local dev)
SITE_DOMAIN=localhost

# Admin dashboard credentials — change these before first run
ADMIN_USERNAME=admin
ADMIN_PASSWORD=changeme

# Optional tuning (see the reference below)
DISCOVERY_INTERVAL_MINUTES=60
TZ=Europe/Berlin
```

#### Environment variable reference

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | required | SQLAlchemy async URL, e.g. `postgresql+asyncpg://spond:…@db:5432/spond_bot` |
| `DB_PASSWORD` | required (compose) | Password for the `db` service; must match `DATABASE_URL`. Also used by the `backup` service |
| `FERNET_KEY` | required | Encrypts stored Spond passwords and tokens. Back it up separately from the database |
| `API_KEY` | required | Bearer token for internal API access |
| `SITE_DOMAIN` | `localhost` | Public domain. Anything other than `localhost` makes the session cookie `Secure` (HTTPS only) |
| `APP_PORT` | `8080` | Host port for the app (compose) |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / `changeme` | Admin login seeded on first start if no admin exists |
| `DISCOVERY_INTERVAL_MINUTES` | `60` | How often events are synced from Spond |
| `EXECUTIONER_INTERVAL_SECONDS` | `60` | How often the fallback executioner looks for due answers |
| `RSVP_LEAD_TIME_MS` | `0` | Fire this many milliseconds *before* registration opens, to offset network latency. Spond may reject answers that arrive before opening, so raise it carefully and watch the answer log |
| `TZ` | `Europe/Berlin` | Scheduler and log timezone |
| `BACKUP_DIR` | `./backups` | Host folder for database dumps (compose `backup` service) |
| `BACKUP_INTERVAL_HOURS` | `24` | Time between backups |
| `BACKUP_KEEP_DAYS` | `14` | Dumps older than this are deleted after a successful backup |

### 3. Start

```bash
docker compose up -d
```

The app starts at `http://localhost:8080` (or the port set by `APP_PORT`).

### 4. First login

Navigate to `http://localhost:8080` and log in with the `ADMIN_USERNAME` / `ADMIN_PASSWORD` you configured. The admin account is seeded automatically on the first startup if none exists.

**Change the admin password immediately** — either update `ADMIN_PASSWORD` in `.env` before first run, or use the password-change endpoint after logging in.

---

## Adding Members

### Invite links (recommended)

Members create their own login and connect their own Spond account, so you never handle their passwords.

1. Admin panel → **Users** → **Invite member**
2. Enter who it is for (shown to you and pre-filled as their display name) and how long the link is valid (1–30 days, default 7)
3. Copy the link and send it to that one person. It is shown only once and works for a single signup.
4. The member opens `/join#…`, picks a username and password, and enters their Spond login. SpondBot checks the Spond credentials before creating anything, then signs them straight into their dashboard.

The **Invites** table shows each invite as pending, used or expired. Revoke a pending invite to stop its link working.
Only a SHA-256 hash of each token is stored, and the token stays in the URL fragment, which browsers never send to the server.

### Existing logins without a Spond account

A signed-in login with no linked Spond account sees a **Connect your Spond account** form on its dashboard and can connect one itself.

### Adding accounts as an admin

You can still do it yourself: **Connect Spond account** (the Spond credentials) and **Add login** (a dashboard login linked to that account).

---

## Ports and Networking

| Service | Default port | Override |
|---------|-------------|---------|
| App | `8080` | `APP_PORT=9000` in `.env` |
| Database | not exposed | — |

The database port is intentionally not exposed outside the Docker network. Connect via `docker exec` if you need direct DB access:

```bash
docker exec -it spond-db psql -U spond -d spond_bot
```

---

## Reverse Proxy (recommended for production)

Run the app behind nginx or Caddy for TLS termination. Set `SITE_DOMAIN` to your actual domain so the session cookie is marked `Secure`.

Minimal nginx config:

```nginx
server {
    listen 443 ssl;
    server_name spond.example.com;

    location / {
        proxy_pass http://localhost:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

## Generating a Fernet Key

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Or inside the running container:

```bash
docker exec spond-bot python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**Keep the Fernet key safe.** Losing it means all stored credentials become unreadable — you would need to re-enter every user's Spond password. There is no migration path for key rotation.

---

## Upgrading

```bash
git pull
docker compose down
docker compose up -d --build
```

Database schema changes are applied automatically on startup: the container runs `alembic upgrade head` before starting the server (see the `Dockerfile`). Migrations live in `migrations/versions/`.

---

## API Documentation

Swagger UI is available at `/docs` and ReDoc at `/redoc`. Both require an admin session — log in to the dashboard first, then navigate to `/docs` in the same browser.

---

## Health Check

```
GET /api/v1/health
```

Returns `200 {"status": "ok", "db": "ok", "scheduler": "running"}` when the database is reachable and the scheduler is running, and `503` with the failing part otherwise. No login is needed, so it also works as an uptime-monitor target.

The image's `HEALTHCHECK` polls it every 30 seconds (`scripts/healthcheck.py`). `docker ps` shows the app as `healthy` or `unhealthy`.

**Automatic restarts are opt-in.** Docker's `restart: unless-stopped` only restarts a container whose process exits; it does nothing about an unhealthy one. To restart the app when it turns unhealthy, start the optional `autoheal` service:

```bash
docker compose --profile autoheal up -d
```

It watches containers labelled `autoheal=true` (only the app). It needs the Docker socket, which gives it control over every container on the host, so enable it only if that is acceptable for your server.

---

## Backups

The `backup` service runs `pg_dump` when it starts and then every `BACKUP_INTERVAL_HOURS` (default 24), writing `spond_bot-<UTC timestamp>.dump` files to `BACKUP_DIR` (default `./backups` next to `docker-compose.yml`). Dumps older than `BACKUP_KEEP_DAYS` (default 14) are deleted, but only after a successful backup, so repeated failures never remove the last good copy. `docker ps` shows the service as unhealthy when no backup newer than two intervals exists.

```bash
ls -lh backups/                              # list backups
docker compose run --rm backup once          # take one now (uses scripts/backup.sh)
docker compose logs backup                   # see results and failures
```

**Keep `FERNET_KEY` safe separately.** The dumps contain Spond credentials encrypted with it. A backup without the key cannot be used to run the bot, and anyone with both can read the credentials. Copy `backups/` off the server regularly (for example with rsync or restic), and treat it as sensitive.

### Restoring

Restore into the running database; this replaces its current contents.

```bash
docker compose stop app backup
docker compose exec -T db pg_restore --clean --if-exists --no-owner -U spond -d spond_bot < backups/spond_bot-YYYYMMDDTHHMMSSZ.dump
docker compose start app backup
```

Use the same `FERNET_KEY` as when the backup was taken. On start the app runs any newer migrations automatically.

---

## Logs

```bash
docker logs spond-bot -f
```

Log format: `TIMESTAMP [LEVEL] logger_name: message`

Key log lines to watch:
- `=== Discovery sync started ===` — worker fired
- `Sniper scheduled for event ... at ...` — precision job registered
- `RSVP ACCEPT for 'Name' ('Event') → SUCCESS` — RSVP confirmed
- `401 on RSVP for ... — forcing token refresh` — automatic recovery triggered
- `Login failed for ...` — Spond rejected credentials (check password)

---

## Troubleshooting

**Bot stops RSVPing after a Spond app update**

Spond occasionally changes their API. Check `app/core/spond_client.py`:
- `_API_BASE` for the base URL
- `login()` for the login endpoint and response shape
- `_headers()` for the user-agent string

See `docs/codebase-review.md` for the full history of known API changes.

**"Login failed" in logs**

The stored password may be wrong (for example, the member changed it in Spond), or Spond rejected the credentials. Enter the new password: the member uses **Account menu → Update Spond password** on their dashboard, or an admin uses **Users → Spond accounts → Update password**. SpondBot checks it with Spond before saving, and events and answers are kept. Failed answers can then be retried from the dashboard.

**Events not appearing in dashboard**

Trigger a manual sync: admin panel → **Sync now**, or `POST /api/v1/admin/sync` with admin credentials. Check logs for API errors.

**RSVP fires too late**

The sniper job fires at `invite_time` from Spond's API. If the server clock is wrong, RSVPs will be late. Ensure the container timezone (`TZ` env var) matches your expected timezone, and that NTP is running on the host.
