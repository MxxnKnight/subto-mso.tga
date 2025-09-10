# Agent Instructions for Subtitle Search Bot

> **CAUTION:** This file contains critical information for the operation and maintenance of this bot. **NEVER DELETE THIS FILE.**

## 1. Project Overview

This is a Telegram bot that scrapes movie and series subtitle information from `malayalamsubtitles.org`, stores it in a JSON database, and allows users to search for and download subtitle files. The project has undergone significant refactoring to arrive at its current stable architecture.

## 2. Core Architecture

The project uses a decoupled architecture to ensure stability and cost-effectiveness.

-   **Web Service (Render):** The live Telegram bot is a FastAPI application defined in `app.py`. It is hosted on Render's free web service tier. Its primary role is to respond to user interactions and serve data from the `db.json` file that is included in the repository. The build command for this service should be simple: `pip install -r requirements.txt`.

-   **Scraper (GitHub Actions):** Data collection is handled by `scraper.py`. This script is executed automatically once a day by a GitHub Actions workflow in `.github/workflows/scraper.yml`. The workflow checks out the `jules-bot-fix` branch, runs the scraper, and commits the updated `db.json` and `series_db.json` files back to the repository. This commit automatically triggers a new deployment of the web service on Render, ensuring the bot always has the latest data.

## 3. Key File Details

-   **`app.py`:** The main FastAPI application.
    -   **Webhook Handler (`telegram_webhook_handler`):** This is the entry point for all Telegram updates. It determines if an update is a text message or a button click (`callback_query`) and routes it to the appropriate handler.
    -   **Bot API Calls (`send_telegram_message`):** This is a critical helper function. It takes a payload dictionary, **pops** the `method` key to use in the URL, and sends the rest of the payload as JSON. This is the correct way to interact with the Telegram API.
    -   **Message Formatting (`format_movie_details`):** This function builds the detailed message string, including Markdown hyperlinks for fields that have associated URLs.
    -   **File Downloads (`download_and_upload_subtitle`):** This function uses a "send -> edit -> delete" flow for status messages to provide a clean UI. It sends "Downloading...", edits it to "Uploading...", and then deletes the message after the operation is complete.

-   **`scraper.py`:** The data scraper.
    -   **Incremental Logic:** The `main` function is incremental. It loads the existing `db.json` and only adds new entries it finds.
    -   **Unique ID for Series:** To handle multiple seasons of a single TV series, the scraper creates a composite unique ID for the database keys: `tt1234567-S2` for Season 2. This is a critical piece of the incremental logic.
    -   **Parsing Logic (`scrape_detail_page`):** The scraper uses a robust two-pass system. It first scrapes all `<tr>` elements from the details table into a dictionary. Then, it iterates through a `FIELD_MAPPING` dictionary to find and assign the correct data, making it resilient to the order of rows on the website.

-   `.github/workflows/scraper.yml`: The GitHub Actions configuration. **Crucially, it is configured to check out and commit to the `jules-bot-fix` branch.**

## 4. Development & Testing

### Testing the Scraper

The scraper is the most complex part of this project. Before making changes, it is crucial to test it locally.

1.  Install dependencies: `pip install -r requirements.txt`
2.  To perform a quick test run that only scrapes one page, use the command:
    ```bash
    SCRAPER_MAX_PAGES=1 python scraper.py
    ```
3.  To test the incremental logic, run the command once to create a DB, then run it again to ensure it doesn't add duplicates and stops early.

### Key Challenge: Scraper Fragility

The scraper's logic in `scraper.py` is tightly coupled to the HTML structure of `malayalamsubtitles.org`. **If the website's HTML, class names, or IDs change, the scraper will break.** Any work related to the scraper should begin by verifying that the selectors in `scrape_detail_page` are still valid.

## 5. Configuration

### GitHub Repository Settings

For the GitHub Action to work, the repository settings must be configured correctly:
1.  Go to **Settings** > **Actions** > **General**.
2.  Scroll to **Workflow permissions**.
3.  Select **"Read and write permissions"** and **Save**.

### Environment Variables

-   `TELEGRAM_BOT_TOKEN`: The secret token for your bot from BotFather.
-   `OWNER_ID`: Your personal Telegram User ID, for any admin-specific commands.
-   `WEBHOOK_SECRET`: This is generated **automatically** by Render because `generateValue: true` is set in `render.yaml`. You do not need to set this yourself.
-   `SCRAPER_MAX_PAGES`: (Optional, for the GitHub Action) Controls the maximum number of pages the scraper will process. Set to a high number like `300` to build the full database.
