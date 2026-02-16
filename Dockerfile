FROM python:3.11-slim

# Install cron and tzdata
RUN apt-get update && apt-get install -y cron tzdata && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create the log file
RUN touch /var/log/cron.log

# Updated Entrypoint script
RUN echo '#!/bin/sh\n\
# Set the timezone for the OS\n\
ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone\n\
\n\
# Create the crontab file with explicit Timezone support\n\
# 00 = Minute, 16 = Hour, * = Any Day of Month, * = Any Month, 4 = Thursday\n\
echo "CRON_TZ=Europe/Berlin" > /etc/cron.d/spond-cron\n\
echo "00 16 * * 4 root sleep 3 && cd /app && /usr/local/bin/python volleyballChecker.py >> /proc/1/fd/1 2>&1" >> /etc/cron.d/spond-cron\n\
\n\
chmod 0644 /etc/cron.d/spond-cron\n\
crontab /etc/cron.d/spond-cron\n\
\n\
echo "Cron started... Schedule: 16:00 every Thursday (Berlin Time)"\n\
cron -f' > /jwt_entrypoint.sh && chmod +x /jwt_entrypoint.sh

CMD ["/jwt_entrypoint.sh"]
