# MSone Malayalam Subtitles Bot & API

This project provides a simple and robust foundation for a Telegram bot and JSON API to interact with subtitles from `malayalamsubtitles.org`. It is designed to be deployed on a platform like Render.

## Architecture

This project uses a **synchronous web server (Flask + Gunicorn)** to handle incoming HTTP requests. To interact with the **asynchronous `python-telegram-bot` library**, it uses a standard Python pattern: it creates a temporary, isolated `asyncio` event loop to run the async bot functions safely from within the synchronous Flask routes.

This "async bridge" provides a stable and reliable way to handle the two different programming models in a single application.

The project is composed of two main Python files:
-   `scrapper.py`: A script to scrape the website and build a local `db.json` database.
-   `app.py`: A Flask application that serves both the API and the Telegram bot's webhook endpoints.

## Setup & Deployment on Render

1.  **Fork this repository.**

2.  **Create a new Web Service on Render** and connect it to your forked repository.

3.  **Set Environment Variables:**
    In your Render service dashboard, go to the **"Environment"** tab and add the following secret files and environment variables:

    *   **`BOT_TOKEN`**: Your secret token for your Telegram bot from BotFather.
    *   **`WEBHOOK_URL`**: The public URL of your Render service (e.g., `https://my-bot-name.onrender.com`). You need to set this manually.

4.  **Build and Start Commands:**
    Render will use the `render.yaml` file to configure the service. It will automatically:
    *   Install dependencies from `requirements.txt`.
    *   Run the scraper to build `db.json`.
    *   Start the server with `gunicorn app:app`.

5.  **Set the Webhook (One-time step):**
    After your service is deployed and "live", you must visit the `/set_webhook` URL in your browser **once** to tell Telegram where to send messages.

    `https://your-service-url.onrender.com/set_webhook`

    You should see a "Webhook set successfully" message. Your bot is now live and will respond to messages.

## API Usage

The API provides a simple test endpoint.

- **Endpoint:** `GET /api/subtitles`
- **Example:** `https://your-service-url.onrender.com/api/subtitles`
- **Result:**
  ```json
  {
      "status": "ok",
      "message": "This is a test API endpoint."
  }
  ```
