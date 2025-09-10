# Subtitle Search Telegram Bot

This is a Telegram bot that allows users to search for Malayalam subtitles for movies and series from [malayalamsubtitles.org](https://malayalamsubtitles.org).

## Core Technologies
- **FastAPI**: For the asynchronous web server that handles API requests and the Telegram webhook.
- **Uvicorn**: As the production web server.
- **aiohttp**: For asynchronous communication with the Telegram Bot API and for downloading files.
- **BeautifulSoup4/requests**: For scraping the subtitle data.
- **GitHub Actions**: For automated, scheduled scraping.

## Architecture

This project uses a combination of a **Render Web Service** for the bot and a **GitHub Action** for scraping to create a cost-effective, automated solution.

- **Render Web Service**: A free-tier web service hosts the FastAPI application (`app.py`). This is the live bot that responds to users on Telegram. It serves the `db.json` file that is included in the repository.
- **GitHub Action**: A scheduled workflow (`.github/workflows/scraper.yml`) runs the `scraper.py` script once a day. It scrapes the latest subtitles, and if it finds any changes, it commits the updated `db.json` file back to the repository.
- **Automatic Updates**: When the GitHub Action pushes a new commit, it automatically triggers a new deployment on Render. This rebuilds the bot with the fresh database, ensuring the data is always up-to-date without any manual work or extra cost. The scraper is incremental, meaning it loads the existing database and only adds new entries, allowing your database to grow over time.

## Deployment

### 1. Fork the Repository

Fork this repository to your own GitHub account.

### 2. Set Up GitHub Actions Permissions

For the scraper to be able to save the updated database back to your repository, you must enable the correct permissions.

1.  In your forked repository, go to **Settings** > **Actions** > **General**.
2.  Scroll down to **Workflow permissions**.
3.  Select **"Read and write permissions"** and click **Save**.

### 3. Create the Web Service on Render

- Connect your GitHub account to Render.
- In the Render Dashboard, click "New" and then "Web Service".
- Select your forked repository and give the service a name.
- Render will detect it's a Python environment. Set the following properties:
  - **Branch**: `jules-bot-fix` (or your main working branch)
  - **Build Command**: `pip install -r requirements.txt`
  - **Start Command**: `uvicorn app:app --host 0.0.0.0 --port $PORT`
- Add the required environment variables under the "Environment" tab.

### 4. Set Environment Variables

- **`TELEGRAM_BOT_TOKEN`**: Your bot token obtained from BotFather on Telegram.
- **`OWNER_ID`**: (Optional) Your personal Telegram User ID.
- **`WEBHOOK_SECRET`**: Render will create this for you automatically if you use the `render.yaml` file. If you set up the service manually, you can leave this blank or add your own secure string.

That's it! The GitHub Action is already configured and will start running on its schedule.

### How to Manually Update the Database

If you don't want to wait for the daily automatic run, you can manually trigger the scraper at any time:
1.  Go to your forked repository on GitHub.
2.  Click on the **"Actions"** tab.
3.  In the left sidebar, click on the **"Daily Scraper"** workflow.
4.  Click the **"Run workflow"** dropdown button and then the green **"Run workflow"** button to start the process.
