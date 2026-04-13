# ⚡ SpondBot — Auto-Accept RSVP & Multi-User Dashboard

<div align="center">

[![License](https://img.shields.io/github/license/kargfel/spond-bot?style=for-the-badge&color=8A2BE2)](LICENSE)
[![Stars](https://img.shields.io/github/stars/kargfel/spond-bot?style=for-the-badge&color=9370DB)](https://github.com/kargfel/spond-bot/stargazers)
[![Forks](https://img.shields.io/github/forks/kargfel/spond-bot?style=for-the-badge&color=BA55D3)](https://github.com/kargfel/spond-bot/network/members)
[![Issues](https://img.shields.io/github/issues/kargfel/spond-bot?style=for-the-badge&color=EE82EE)](https://github.com/kargfel/spond-bot/issues)
[![Docker](https://img.shields.io/badge/Docker-Enabled-2496ED?style=for-the-badge&logo=docker&logoColor=white)](#)
[![FastAPI](https://img.shields.io/badge/FastAPI-Backend-009688?style=for-the-badge&logo=fastapi&logoColor=white)](#)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Database-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)](#)

*An automated, self-hosted Bot & Dashboard for the Spond app.*
</div>

---

## 🌟 Overview

Tired of missing out on high-demand group events because they fill up in seconds? **SpondBot** connects to your Spond account and automatically RSVPs "Yes" to specific events the moment they are created. 

Originally a simple cron-script, SpondBot is now a fully-fledged platform featuring:
- **👮 Multi-User Support**: Host your own instance and let multiple users configure their own RSVP settings.
- **🎨 Modern Web Dashboard**: A sleek, dark-themed "Midnight Violet" UI to manage your RSVPs, monitor status, and link your Spond account.
- **⚡ Background Automation**: Fully containerized schedule-driven worker that strictly adheres to your configured limits.
- **🔒 Secure & Private**: Hosted entirely on your own hardware. Your Spond credentials are systematically encrypted.

---

## ✨ Key Features

- **Phone & Email Authentication**: Automatically detects whether an account uses an email or phone number.
- **Zero-Trust Hardened**: All stored credentials are encrypted with a Fernet symmetric key.
- **Admin Management Portal**: Built-in Admin dashboard to manage users, reset passwords, and oversee system health.
- **Automated Deployments**: Quick-start configured with Docker & `docker-compose`.

---

## 🚀 Quick Setup & Deployment

SpondBot is incredibly easy to deploy using Docker. You can host this on your local PC, a Raspberry Pi, or a VPS server.

### 1. Prerequisites
- **[Docker & Docker Compose](https://docs.docker.com/get-docker/)** installed on your machine.
- Git (optional, but recommended).

### 2. Download and Configure
Clone this repository (or download it as a ZIP):
```bash
git clone https://github.com/kargfel/spond-bot.git
cd spond-bot
```

Create your environment configuration:
```bash
cp .env.example .env
```

Open `.env` in your favorite text editor to configure the secrets:
- **`DB_PASSWORD`**: Set a strong database password.
- **`FERNET_KEY`**: Generate this securely (run `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
- **`API_KEY`**: Set a strong random token.
- **`ADMIN_PASSWORD`**: Set your initial administrator password to access the Web UI.

### 3. Start SpondBot!
```bash
docker compose up -d --build
```
*That's it!* SpondBot is now running in the background.

### 4. Access the Dashboard
Open your web browser and navigate to:
👉 **`http://localhost:8080`** *(or your server's IP address)*

Log in with:
- **Username**: `admin`
- **Password**: *(What you set for `ADMIN_PASSWORD`)*

From there, you can link your Spond account and start automating your RSVPs!

---

## 📖 Advanced Usage & Multi-Node Deployment

If you want to expose SpondBot to the open internet (e.g., using a Reverse Proxy like Traefik on a dedicated JumpHost), please read our comprehensive **[Advanced Deployment Guide](DEPLOY.md)**. 

---

## 🤝 Contributing & Support

If you run into issues, have feature requests, or want to contribute:
1. Open an Issue in the [Issue Tracker](https://github.com/kargfel/spond-bot/issues).
2. Fork the repository and open a Pull Request.

---

## 📝 License

This project is generously provided under the [MIT Open Source License](LICENSE).
