# SpondBot — Multi-Node Deployment Guide (Traefik JumpHost)

Deployment target: **`/home/felix/stacks/spondApp`** on your `mainApps` VM.
JumpHost target: Your frontend Traefik instance managing Let's Encrypt certificates.
Public URL: **`https://spond.felixkarg.de`** via Traefik.

---

## 1. Zero-Trust Networking Principles

Because SpondBot will be running on a dedicated `mainApps` VM while Traefik runs on a `jumpHost`, the connection between the two traverses your internal network. 

**Security Checklist:**
- The Docker container on `mainApps` will expose port 8080 to the VM's network interface (not `127.0.0.1`).
- You **must** configure a firewall (e.g., `ufw`) on the `mainApps` VM to allow TCP inbound on port 8080 **only** from the `jumpHost`'s internal IP address.
- The `Dockerfile` has been configured with `--proxy-headers --forwarded-allow-ips='*'` so that the internal rate limiter correctly reads the client IP passed down by Traefik. Keep this restricted on the firewall to prevent IP spoofing from other machines on your LAN.

---

## 2. Server Setup (`mainApps` VM)

SSH into your `mainApps` VM and create the app directory:

```bash
mkdir -p /home/felix/stacks/spondApp
cd /home/felix/stacks/spondApp
```

Clone the repository:

```bash
git clone https://github.com/YOUR_USER/Spond.git .
```

Create the `.env` file:

```bash
cp .env.example .env
nano .env
```

Generate the required secrets **directly on the server**:

```bash
# Fernet key
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# API key
openssl rand -hex 32
```

Fill in `.env` with your actual values:

| Variable | Value |
|---|---|
| `DB_PASSWORD` | A strong unique password |
| `DATABASE_URL` | `postgresql+asyncpg://spond:<DB_PASSWORD>@db:5432/spond_bot` |
| `FERNET_KEY` | Output of the python command above |
| `API_KEY` | Output of openssl command above |
| `SITE_DOMAIN` | `spond.felixkarg.de` |
| `APP_PORT` | `8080` |
| `ADMIN_USERNAME` | Your desired admin username |
| `ADMIN_PASSWORD` | A strong unique password |

Build and start the stack:

```bash
docker compose up -d --build
docker compose logs -f app
```

---

## 3. Traefik Setup (`jumpHost`)

On your `jumpHost`, Traefik needs a routing rule to send traffic to the `mainApps` VM.
Since they are on different servers, you'll use a **Traefik Dynamic Configuration file**.

Create a new file (e.g., `spondbot.yml`) in your Traefik dynamic configurations directory (wherever `providers.file.directory` points to in your `traefik.yml`):

```yaml
http:
  routers:
    spondbot-router:
      rule: "Host(`spond.felixkarg.de`)"
      service: spondbot-service
      entryPoints:
        - websecure
      tls:
        certResolver: your_cert_resolver  # e.g., letsencrypt

  services:
    spondbot-service:
      loadBalancer:
        servers:
          - url: "http://<MAIN_APPS_VM_IP>:8080" # Replace with mainApps internal IP
```

*Traefik will detect the new configuration file and instantly apply the rules.*

---

## 4. Verification

From the internet (or another network entirely):

```bash
# Test path traversal is fixed (should return index.html, not your .env)
curl -s https://spond.felixkarg.de/..%2F.env | head -c 50
# Expected: <!DOCTYPE html> ...

# Test rate limiting (6th attempt within a minute should return 429)
for i in $(seq 1 6); do curl -s -o /dev/null -w "%{http_code}\n" \
  -X POST https://spond.felixkarg.de/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"x","password":"x"}'; done
# Expected: 401 401 401 401 401 429
```

---

## 5. Ongoing Operations

### Update the app on `mainApps`
```bash
cd /home/felix/stacks/spondApp
git pull
docker compose up -d --build
```

### View logs
```bash
docker compose logs -f app
```

### Backup the database
```bash
docker exec spond-db pg_dump -U spond spond_bot > backup_$(date +%Y%m%d).sql
```
