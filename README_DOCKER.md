# Spond Bot - Deployment Guide

This repository relies on a robust Docker and Cron structure to reliably execute your automation. 

## Prerequisites

- [Docker](https://www.docker.com/products/docker-desktop/) installed on your server/machine.
- A basic understanding of standard `CRON` syntax.

## Setup Instructions

### 1. Configuration (`.env`)
You must create a `.env` file in the root directory before booting up the docker environment. An example template is provided in `.env.example`.

Create the file:
```bash
cp .env.example .env
nano .env
```

Define your required parameters inside `.env`:
- `SPOND_USERNAME`: The email or phone number you use to sign in to Spond.
- `SPOND_PASSWORD`: Your Spond password.
- `SPOND_TARGET_EVENT_HEADING`: The *exact* text heading of the event you wish to RSVP to.
- `CRON_SCHEDULE`: When the bot should wake up. (Default: `00 16 * * 4` = Every Thursday at 16:00).

> **Important**: Never commit your `.env` file to source control. It is ignored by `.gitignore` by default.

### 2. Execution

To build and start the bot as a background daemon, run:
```bash
docker-compose up -d --build
```
This boots the `spond-bot` service. Upon booting:
1. It verifies your Timezone (`TZ`).
2. It constructs an internal `crontab` evaluating your specific `$CRON_SCHEDULE`.
3. It quietly waits in the background using almost no system memory until that explicit time is matched. 

### 3. Verification

Check if the container is running and confirm its schedule:
```bash
docker ps
docker-compose logs -f
```

## Advanced Customizations

If your situation requires finer adjustments, the `.env` file natively supports advanced controls:

- `SPOND_POLL_TIMEOUT_MINUTES`: How long the bot actively searches before timing out. (Default: 5)
- `SPOND_ATTEND_ANSWER`: Allows configuring the bot to decline (`false`) over accepting (`true`). (Default: true)
- `SPOND_MIN_COOLDOWN_SECONDS` / `SPOND_MAX_COOLDOWN_SECONDS`: API rate limiting sleep boundaries.
- `SPOND_ID`: Manually skip the automatic Spond ID detection by defining your Member ID here.
