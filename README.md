# Subtitle Search Telegram Bot (subto-mso-tga)

This is a Telegram bot that allows users to search for Malayalam subtitles for movies and series from [malayalamsubtitles.org](https://malayalamsubtitles.org).

The project is built using Python with the following key technologies:
- **FastAPI**: For the asynchronous web server that handles API requests and the Telegram webhook.
- **Uvicorn/Gunicorn**: As the production web server.
- **python-telegram-bot**: The library used to interact with the Telegram Bot API.
- **BeautifulSoup4/requests**: For scraping the subtitle data.

## Features

- Search for subtitles by movie or series name.
- Displays results with posters, descriptions, and direct download links.
- A REST API endpoint (`/api/subtitles?query=...`) for programmatic searching.
- Health check endpoint (`/healthz`) for monitoring.
- Startup notification sent to the bot owner.

## Deployment on Render

This bot is designed to be deployed on the Render free tier.

### 1. Fork the Repository

Fork this repository to your own GitHub account.

### 2. Create a New Web Service on Render

- Connect your GitHub account to Render.
- Create a new "Web Service" and select your forked repository.
- Render will automatically detect the `render.yaml` file and use it for configuration.

### 3. Set Environment Variables

In the Render dashboard for your service, go to the "Environment" tab and add the following secrets:

- **`TELEGRAM_BOT_TOKEN`**: Your bot token obtained from BotFather on Telegram.
- **`OWNER_ID`**: (Optional) Your personal Telegram User ID. If you provide this, the bot will send you a "Bot is up and running!" message every time it deploys.
- **`WEBHOOK_SECRET`**: (Optional but Recommended) Render will automatically generate a secure, random string for this if you use the `render.yaml` provided. It's used to verify that incoming requests to your webhook are genuinely from Telegram.

### How it Works

- The `render.yaml` file configures the entire deployment.
- The `buildCommand` first installs dependencies and then runs `scraper.py` to build the `db.json` subtitle database. This happens every time you deploy, so the data stays fresh.
- The `startCommand` runs the FastAPI application using Gunicorn and Uvicorn workers.
- On startup, the application automatically sets its own Telegram webhook using the `RENDER_EXTERNAL_URL` provided by Render. This makes the setup process seamless.
- The free web service will "spin down" after 15 minutes of inactivity, but it will wake up automatically when it receives a new message (a webhook call from Telegram). The first response after a cold start may take a few seconds.
