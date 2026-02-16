# Spond Bot - Docker Setup

This project is set up to run automatically using Docker and Cron.

## Prerequisites

- [Docker](https://www.docker.com/products/docker-desktop/) installed on your server/machine.
- A valid `.env` file with your Spond credentials (`Spond_USERNAME`, `Spond_PASSWORD`, `Spond_ID`).

## How it Works

- The container is built using `python:3.11-slim`.
- It installs a **Cron** daemon inside the container.
- The schedule is set to **Every Thursday at 16:00:10** (Europe/Berlin time).
- The 10-second delay (`sleep 10`) ensures the event is definitely open before the script runs, avoiding the "423 Locked" error from Spond.

## Usage

1.  **Start the Bot**:
    Run the following command in this directory:
    ```bash
    docker-compose up -d --build
    ```
    - `-d`: Runs in detached mode (background).
    - `--build`: Rebuilds the image to ensure latest code is used.

2.  **Check Status**:
    Check if the container is running:
    ```bash
    docker ps
    ```
    You should see `spond-bot` listed.

3.  **View Logs**:
    To see the output of the script (when it runs) or any errors:
    ```bash
    docker-compose logs -f
    ```

4.  **Stop the Bot**:
    ```bash
    docker-compose down
    ```

## Configuration

- **Timezone**: The timezone is set to `Europe/Berlin` in `docker-compose.yml`. Change `TZ` environment variable if needed.
- **Schedule**: The schedule is defined in `Dockerfile` (currently `0 16 * * 4`).

## Deployment Workflow

### 1. On Your Laptop (Prepare)
1.  **Commit & Push**: Push all these new files (`Dockerfile`, `docker-compose.yml`, `README_DOCKER.md`, `requirements.txt`) to your GitHub repository.
    > **Note**: Do NOT commit your `.env` file containing passwords!

### 2. On Your Server (Deploy)
1.  **Connect**: SSH into your server.
2.  **Get Code**: 
    - `git pull` inside your folder (or `git clone` if it's new).
3.  **Secrets**: 
    - Create/Edit the `.env` file on the server: `nano .env`
    - Paste your `Spond_USERNAME` and `Spond_PASSWORD`.
4.  **Run**:
    ```bash
    docker-compose up -d --build
    ```
5.  **Verify**:
    - `docker ps` (Check if running)
    - `docker-compose logs -f` (View logs - waiting for Thursday!)

## Testing

To verify the script works *inside* the container without waiting for Thursday:

```bash
docker-compose exec spond-bot python volleyballChecker.py
```
This runs the script immediately.
