FROM python:3.11.9-slim

# System dependencies: cron for scheduling, tzdata for timezone support.
RUN apt-get update && apt-get install -y cron tzdata && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN touch /var/log/cron.log

# Build the container entrypoint script at image build time.
#
# The script does three things at container startup:
#   1. Configures the OS timezone from the TZ environment variable.
#   2. Snapshots all Docker-injected environment variables to /etc/environment.
#      Cron spawns each job in a clean shell that does not inherit the container's
#      environment, so this snapshot is sourced explicitly by the cron command.
#   3. Registers the scheduled job and starts the cron daemon in the foreground.
RUN echo '#!/bin/sh\n\
ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone\n\
\n\
printenv > /etc/environment\n\
\n\
echo "CRON_TZ=${TZ:-Europe/Berlin}" > /etc/cron.d/spond-cron\n\
echo "${CRON_SCHEDULE:-0 16 * * 4} root . /etc/environment; cd /app && /usr/local/bin/python spond_bot.py >> /proc/1/fd/1 2>&1" >> /etc/cron.d/spond-cron\n\
\n\
chmod 0644 /etc/cron.d/spond-cron\n\
\n\
echo "Cron started... Schedule: ${CRON_SCHEDULE:-0 16 * * 4} ($TZ Time)"\n\
cron -f' > /entrypoint.sh && chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]
