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
- **`WEBHOOK_SECRET`**: Render will create this for you automatically if you use the `render.yaml` file.
- **`LOG_GROUP_ID`**: (Optional) The ID of a Telegram group where the bot will send logs of user actions (e.g., when a user starts the bot or makes a search).
- **`LOG_TOPIC_ID`**: (Optional) If the `LOG_GROUP_ID` is a group with topics enabled, you can specify the ID of a topic to send the logs to.

**How to get the `LOG_GROUP_ID` and `LOG_TOPIC_ID`:**

1.  **Create a public or private group** for your logs.
2.  **Add your bot to the group** as a member.
3.  **Send a message** to the group (or the specific topic if you have them enabled).
4.  **Forward that message** to a bot like `@userinfobot`.
5.  The bot will reply with a JSON message.
    *   The `chat.id` will be your `LOG_GROUP_ID`.
    *   If you sent the message in a topic, there will be a `message_thread_id` field. This is your `LOG_TOPIC_ID`.

That's it! The GitHub Action is already configured and will start running on its schedule.

### How to Manually Update the Database

If you don't want to wait for the daily automatic run, you can manually trigger the scraper at any time:
1.  Go to your forked repository on GitHub.
2.  Click on the **"Actions"** tab.
3.  In the left sidebar, click on the **"Daily Scraper"** workflow.
4.  Click the **"Run workflow"** dropdown button and then the green **"Run workflow"** button to start the process.

## Admin Commands

If you have set the `OWNER_ID` environment variable, you can use the following commands in a direct message with the bot:

-   **`/delete <imdb_id>`**: Deletes an entry from the bot's database. This is useful for removing broken or incorrect entries. The change is immediate but will be temporary until the next daily scraper run makes it permanent.
    -   Example: `/delete tt1234567`
-   **`/rescrape <imdb_id>`**: Manually triggers a re-scrape of a specific movie or series page. This is useful for updating an ongoing series with a new subtitle file without waiting for the automated weekly re-scrape.
    -   Example: `/rescrape tt1234567-S1`
