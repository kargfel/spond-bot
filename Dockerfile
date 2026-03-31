FROM python:3.11.9-slim

# [AUDIT FIX] Pinned to a specific patch version (3.11.9) for fully reproducible
# builds. The unpinned `3.11-slim` tag can silently pull a different Python version
# between builds depending on when the image cache is refreshed. (audit: Dockerfile L1)

# Install cron and tzdata
RUN apt-get update && apt-get install -y cron tzdata && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create the log file
RUN touch /var/log/cron.log

# [AUDIT FIX] Renamed entrypoint from /jwt_entrypoint.sh to /entrypoint.sh.
# The previous name "jwt" referred to JSON Web Tokens, which is unrelated to this
# project — a likely copy-paste artefact. (audit: Dockerfile L29)
#
# [AUDIT FIX] Removed the `sleep 3` race-condition hack from the cron command.
# That sleep assumed 3 seconds was always sufficient for container startup, which
# is fragile. Cron itself handles scheduling correctly once the daemon has started;
# the sleep was only needed if the script ran immediately at boot, which weekly/daily
# cron jobs do not. The echo startup message provides sufficient boot confirmation.
# (audit: Dockerfile L23)
RUN echo '#!/bin/sh\n\
# Set the timezone for the OS\n\
ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone\n\
\n\
# Create the crontab file with explicit environment interpolation\n\
echo "CRON_TZ=${TZ:-Europe/Berlin}" > /etc/cron.d/spond-cron\n\
echo "${CRON_SCHEDULE:-0 16 * * 4} root cd /app && /usr/local/bin/python spond_bot.py >> /proc/1/fd/1 2>&1" >> /etc/cron.d/spond-cron\n\
\n\
chmod 0644 /etc/cron.d/spond-cron\n\
crontab /etc/cron.d/spond-cron\n\
\n\
echo "Cron started... Schedule: ${CRON_SCHEDULE:-0 16 * * 4} ($TZ Time)"\n\
cron -f' > /entrypoint.sh && chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]
