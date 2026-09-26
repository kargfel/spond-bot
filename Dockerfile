FROM python:3.11-slim

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
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8080 --proxy-headers --forwarded-allow-ips='*' --log-level info"]
