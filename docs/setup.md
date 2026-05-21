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

# Optional tuning
DISCOVERY_INTERVAL_MINUTES=60
TZ=Europe/Berlin
```

### 3. Start

```bash
docker compose up -d
```

The app starts at `http://localhost:8080` (or the port set by `APP_PORT`).

### 4. First login

Navigate to `http://localhost:8080` and log in with the `ADMIN_USERNAME` / `ADMIN_PASSWORD` you configured. The admin account is seeded automatically on the first startup if none exists.

**Change the admin password immediately** — either update `ADMIN_PASSWORD` in `.env` before first run, or use the password-change endpoint after logging in.

---

## Adding Spond Users

Spond user accounts (the actual Spond credentials the bot will use) are managed from the admin panel at `/admin`.

1. Go to the **Users** section in the admin panel
2. Click **Add User**
3. Enter the Spond login (email or phone number), display name, and password
4. The bot will authenticate with Spond immediately and store the encrypted token

To let a dashboard user manage their own events, create a **Frontend User** and set its **Linked User** to the corresponding Spond account.

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

Database schema changes are applied automatically on startup via Alembic (if configured) or at the SQLAlchemy `create_all` level. Check the release notes for any manual migration steps before upgrading.

---

## Health Check

```
GET /health
```

Returns `{"status": "ok", "db": "ok"}` when the app and database are both reachable. Use this as your container health check or uptime monitor target.

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

The stored password may be wrong, or Spond rejected the credentials. Go to admin → Users → edit the user and re-enter the password.

**Events not appearing in dashboard**

Trigger a manual sync: admin panel → **Sync Now**, or `POST /api/v1/sync` with admin credentials. Check logs for API errors.

**RSVP fires too late**

The sniper job fires at `invite_time` from Spond's API. If the server clock is wrong, RSVPs will be late. Ensure the container timezone (`TZ` env var) matches your expected timezone, and that NTP is running on the host.
