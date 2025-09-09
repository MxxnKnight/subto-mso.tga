# Subtitle Search Telegram Bot (subto-mso-tga)

This is a Telegram bot that allows users to search for Malayalam subtitles for movies and series from [malayalamsubtitles.org](https://malayalamsubtitles.org).

The project is built using Python with the following key technologies:
- **FastAPI**: For the asynchronous web server that handles API requests and the Telegram webhook.
- **Uvicorn**: As the production web server.
- **aiohttp**: For asynchronous communication with the Telegram Bot API and for downloading files.
- **BeautifulSoup4/requests**: For scraping the subtitle data.

## Features

- Search for subtitles by movie or series name.
- Displays results with posters, descriptions, and direct download links.
- Handles both single subtitle files and `.zip` archives.
- A REST API endpoint (`/api/subtitles?query=...`) for programmatic searching.
- Health check endpoint (`/healthz`) for monitoring.
- Startup notification sent to the bot owner.

## Architecture on Render

This project is designed for a robust and scalable deployment on Render using a **Web Service** and a **Cron Job**.

- **Web Service**: A FastAPI application that serves the Telegram bot and a simple API. It responds to user queries by searching the `db.json` file.
- **Cron Job**: A scheduled task that runs the `scraper.py` script once a day. This job scrapes malayalamsubtitles.org and rebuilds the `db.json` and `series_db.json` files.
- **Persistent Disk**: Both the Web Service and the Cron Job are connected to a shared persistent disk. The Cron Job writes the database files to the disk, and the Web Service reads from it. This ensures the bot always has fresh data without requiring a rebuild or restart.

## Deployment on Render

This bot is designed to be deployed on the Render.

### 1. Fork the Repository

Fork this repository to your own GitHub account.

### 2. Create a New "Blueprint" on Render

- Connect your GitHub account to Render.
- In the Render Dashboard, click "New" and then "Blueprint".
- Select your forked repository. Render will automatically detect the `render.yaml` file and configure the two services (the web service and the cron job).
- Approve the creation of the services.

### 3. Set Environment Variables

After the services are created, you will need to set your secrets. Go to the "Environment" tab for the `subtitle-bot` **web service** and add the following secrets:

- **`TELEGRAM_BOT_TOKEN`**: Your bot token obtained from BotFather on Telegram.
- **`OWNER_ID`**: (Optional) Your personal Telegram User ID. If you provide this, the bot will send you a "Bot is up and running!" message every time it deploys.

You can also customize the scraper's behavior by setting environment variables for the `subtitle-scraper` **cron job**:

- **`SCRAPER_MAX_PAGES`**: (Optional) The number of pages to scrape. The default is `200`. You can increase or decrease this value to control how large the database is and how long the scraper runs.

### How it Works

- The `render.yaml` file configures the entire deployment.
- The **`subtitle-scraper` cron job** runs daily, executing `python scraper.py` to build the `db.json` and `series_db.json` on the shared persistent disk.
- The **`subtitle-bot` web service** runs the FastAPI application. On startup, it loads the database files from the persistent disk.
- The web service automatically sets its own Telegram webhook using the `RENDER_EXTERNAL_URL` provided by Render. This makes the setup process seamless.
- The free web service will "spin down" after 15 minutes of inactivity, but it will wake up automatically when it receives a new message. The first response after a cold start may take a few seconds.
