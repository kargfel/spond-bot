FROM python:3.11-slim

# No cron needed — APScheduler handles scheduling inside the app process.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run database migrations then start the API server.
# Using shell form so environment variable substitution works.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8080 --log-level info"]
