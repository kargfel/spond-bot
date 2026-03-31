# Spond Auto-Accept Bot

An automated bot script for the Spond App that monitors and automatically RSVPs to specific events as soon as they become available. 

## Overview
This bot connects to the Spond API, polls for an event with a specific heading, and automatically RSVPs "Yes" (or "No") when it appears. This is especially useful for high-demand groups where events fill up extremely quickly. 

The bot is entirely configurable via local environment variables and requires **no hardcoded credentials**. 

## Features
- **Phone & Email Authentication**: Automatically detects whether your account uses an email or phone number to sign in. 
- **Dynamic Timing Constraints**: Adjustable poll timeouts and retry configurations.
- **Background Automation**: Packaged entirely via Docker with a customizable Cron Scheduler for headless, seamless deployment. 

## Setup & Deployment
To install and start deploying this script via Docker on your local laptop, Raspberry Pi, or external server, please follow the full instructions here:

[View Deployment & Docker Setup Guide](README_DOCKER.md)

## License
Provided under the MIT Open Source License. See the `LICENSE` file for more details.
