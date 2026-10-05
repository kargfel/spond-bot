FROM python:3.14-slim

# No cron needed — APScheduler handles scheduling inside the app process.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user to run the application
RUN useradd --create-home --shell /bin/bash spond

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Ensure the non-root user owns the application files
RUN chown -R spond:spond /app

USER spond

# Docker marks the container unhealthy when /api/v1/health stops answering 200
# (database unreachable or scheduler stopped). See scripts/healthcheck.py.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "/app/scripts/healthcheck.py"]

# Run database migrations then start the API server.
# Using shell form so environment variable substitution works.
# TRUSTED_PROXIES is set through the environment, not here: write your reverse proxy's IP (or
# network, e.g. 172.18.0.0/16; several separated by commas) into .env as
#     TRUSTED_PROXIES=<proxy IP>
# docker compose hands .env to the container. Only those peers' X-Forwarded-For is believed; it
# decides the client IP in the audit log and the per-IP login limit.
# The fallback "*" trusts everyone, so a visitor can fake their IP. It stays only so that existing
# setups keep working until the proxy's IP is entered (behind a proxy on another host, 127.0.0.1
# would make every visitor look like the proxy and share one login limit). The app logs a warning
# at startup while TRUSTED_PROXIES is unset on a public domain. See docs/setup.md and DEPLOY.md.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8080 --proxy-headers --forwarded-allow-ips \"${TRUSTED_PROXIES:-*}\" --log-level info"]
