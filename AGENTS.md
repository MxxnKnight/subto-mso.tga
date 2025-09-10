# Agent Instructions for Subtitle Search Bot

This document provides essential information for AI agents working on this project.

## Project Overview

This is a Telegram bot that scrapes movie and series subtitle information from `malayalamsubtitles.org`, stores it in a JSON database, and allows users to search for and download subtitle files.

## Core Architecture

The project uses a decoupled architecture to ensure stability and cost-effectiveness.

1.  **Web Service (Render):**
    *   The live Telegram bot is a FastAPI application defined in `app.py`.
    *   It is hosted on Render's free web service tier.
    *   Its primary role is to respond to user interactions and serve data from the database. It does **not** perform any scraping itself.

2.  **Scraper (GitHub Actions):**
    *   The data collection is handled by `scraper.py`.
    *   This script is executed automatically once a day by a **GitHub Actions workflow** defined in `.github/workflows/scraper.yml`.
    *   The workflow checks out the `jules-bot-fix` branch, runs the scraper, and commits the updated `db.json` and `series_db.json` files back to the repository.
    *   This commit automatically triggers a new deployment of the web service on Render, ensuring the bot always has the latest data.

## Key Files

-   `app.py`: The main FastAPI application. Contains all bot logic, including command handlers, callback handlers for buttons, and the Telegram webhook endpoint.
-   `scraper.py`: The data scraper. This script is **incremental**. It loads the existing `db.json`, scrapes the website for new entries, and saves the combined result.
-   `.github/workflows/scraper.yml`: The GitHub Actions configuration file. It defines the schedule and steps for the automated scraping task.
-   `render.yaml`: The configuration for deploying the web service on Render.
-   `db.json` & `series_db.json`: The database files. These are committed to the repository and should be treated as data, not as artifacts to be generated during a build.

## Development & Testing

### Testing the Scraper

The scraper is the most complex part of this project. Before making changes, it is crucial to test it locally.

1.  Install dependencies: `pip install -r requirements.txt`
2.  To perform a quick test run that only scrapes one page, use the following command:
    ```bash
    SCRAPER_MAX_PAGES=1 python scraper.py
    ```
3.  After the run, inspect `db.json` to verify that the data is structured correctly.

### Key Challenge: Scraper Fragility

The scraper's logic in `scraper.py` is tightly coupled to the HTML structure of `malayalamsubtitles.org`. **If the website's HTML, class names, or IDs change, the scraper will break.** Any work related to the scraper should begin by verifying that the selectors in `scrape_detail_page` are still valid.

### Unique ID for Series

To handle multiple seasons of a single TV series (which often share one IMDb ID), the scraper creates a composite unique ID for the database keys:
-   **Movies:** `tt1234567`
-   **Series:** `tt1234567-S2` (for Season 2)

This is a critical piece of the incremental scraping logic.
