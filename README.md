# MSone Malayalam Subtitles Bot & API

This project provides a comprehensive solution for accessing Malayalam subtitles from `malayalamsubtitles.org` via a Telegram bot and a JSON API. The bot is feature-rich, offering an interactive UI, direct file downloads, and detailed information for movies and series.

## Features

- **Advanced Scraper:** A robust scraper (`scrapper.py`) that builds a local database of all subtitles and their detailed metadata.
- **Interactive Telegram Bot:**
    - **Menu System:** Easy navigation with `/start`, `Help`, `About`, and `TOS` pages using inline buttons.
    - **Smart Search:** Search for movies or series. The bot provides a list of choices for multiple matches or a detailed view for a single match.
    - **Detailed View:** Displays rich information including a poster, title, IMDb rating, certification, genre, director, synopsis, and poster designer credits.
    - **Direct File Downloads:** Instead of links, the bot sends the `.srt` file directly to you.
    - **ZIP File Handling:** Automatically extracts `.srt` files from ZIP archives.
    - **Series Navigation:** Displays different seasons as inline buttons for easy selection.
- **JSON API:** A simple Flask-based API (`app.py`) to serve the scraped data.

## Workflow

The system is designed in two main parts to be fast and efficient:

1.  **The Scraper (`scrapper.py`):** This script acts as the data-gathering engine. It's designed to be run on a schedule (e.g., daily using a cron job). It scrapes `malayalamsubtitles.org`, collects details for every entry, and saves it all into the `db.json` file. This file acts as a fast, local database.

2.  **The API & Bot (`app.py`):** This is the user-facing part of the application. It reads from the pre-populated `db.json` file. This means user interactions are nearly instant and don't require slow, on-demand scraping of the website. This architecture also makes the bot more reliable and respectful of the source website's servers.

## Example Bot Interaction

**1. User searches for "Shōgun"**
> **Bot:** I found multiple results for "Shōgun". Please select one:
> `[ Shōgun Season 1 ഷോഗൺ സീസൺ 1 (2024) ]`
> `[ Shōgun (1980) ]`

**2. User selects "Shōgun Season 1"**
> **Bot sends a message with a poster:**
>
> **Shōgun Season 1 ഷോഗൺ സീസൺ 1 (2024)**
>
> `എംസോൺ റിലീസ് – 3400`
>
> **Rating:** 8.6/10 | **Certification:** NC-17
> **Director:** N/A
> **Genre:** അഡ്വെഞ്ചർ,ഡ്രാമ,വാർ
>
> _പതിനാറാം നൂറ്റാണ്ടിൽ പോർച്ചുഗീസ് നാവികനായ മഗല്ലൻ..._
>
> Poster by: [നിഷാദ് ജെ.എൻ](https://malayalamsubtitles.org/designers/nishad-jn/)
>
> [IMDb](https://www.imdb.com/title/tt2788316/)
>
> `[ Download Subtitle ]`

## Setup & Installation

1.  **Clone the repository:**
    ```bash
    git clone <repository-url>
    cd <repository-directory>
    ```

2.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Create the database:**
    Run the scraper to build the `db.json` file.
    ```bash
    python scrapper.py
    ```

4.  **Set Environment Variables:**
    The Telegram bot requires a bot token. Set it as an environment variable.
    ```bash
    export TELEGRAM_BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN"
    ```

5.  **Run the application:**
    ```bash
    python app.py
    ```
    The Flask server will start, and the bot will be active.

## API Usage

The API provides a simple endpoint to get movie data by IMDb ID.

- **Base URL:** `http://127.0.0.1:5000/`
- **Endpoint:** `GET /api/<imdb_id>`

**Example:**
`GET http://127.0.0.1:5000/api/tt2788316`

**Result:**
```json
{
    "title": "Shōgun Season 1 ഷോഗൺ സീസൺ 1 (2024)",
    "posterMalayalam": "https://malayalamsubtitles.org/wp-content/uploads/2024/10/SHOGUN-SE01-768x1084.jpg.webp",
    "descriptionMalayalam": "പതിനാറാം നൂറ്റാണ്ടിൽ...",
    "msoneReleaseNumber": "എംസോൺ റിലീസ് – 3400",
    "imdbURL": "https://www.imdb.com/title/tt2788316/",
    "imdbRating": "8.6/10",
    "certification": "NC-17",
    ...
}
```
