import os
import json
import logging
from http import HTTPStatus

from fastapi import FastAPI, Request, Response
from telegram import Update

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Environment ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN env var")

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "a-random-string")
OWNER_ID = os.environ.get("OWNER_ID")

# --- FastAPI app ---
app = FastAPI()

# --- Bot Setup ---
from bot import create_ptb_application
from api import db  # only import db, not app

ptb_app = create_ptb_application(TOKEN)
ptb_app.bot_data["db"] = db


# --- FastAPI Lifecycle Events ---
@app.on_event("startup")
async def on_startup():
    """Initialize bot and set webhook."""
    await ptb_app.initialize()

    base_url = os.environ.get("RENDER_EXTERNAL_URL", "https://subto-mso-tga.onrender.com")
    url = f"{base_url}/webhook"
    await ptb_app.bot.set_webhook(
        url=url,
        secret_token=WEBHOOK_SECRET,
        drop_pending_updates=True
    )
    logger.info(f"Webhook set to {url}")

    if OWNER_ID:
        await ptb_app.bot.send_message(chat_id=OWNER_ID, text="Bot is up and running!")
        logger.info(f"Sent startup notification to OWNER_ID {OWNER_ID}")


@app.on_event("shutdown")
async def on_shutdown():
    """Clean shutdown."""
    logger.info("Application shutting down...")
    await ptb_app.shutdown()


# --- Healthcheck Endpoint ---
@app.get("/")
async def healthcheck():
    return {"status": "ok", "message": "Bot service is running"}


# --- Webhook Endpoint ---
@app.post("/webhook")
async def webhook(request: Request):
    """Webhook endpoint that receives updates from Telegram."""
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        logger.warning("Webhook secret mismatch!")
        return Response(status_code=HTTPStatus.FORBIDDEN)

    try:
        data = await request.json()
        update = Update.de_json(data, ptb_app.bot)
        await ptb_app.process_update(update)
        return Response(status_code=HTTPStatus.OK)
    except json.JSONDecodeError:
        logger.error("Failed to decode JSON from Telegram webhook.")
        return Response(status_code=HTTPStatus.BAD_REQUEST)
