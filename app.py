import os
import json
import logging
from http import HTTPStatus

from fastapi import FastAPI, Request, Response
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Environment and Database ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN env var")

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "a-random-string")
OWNER_ID = os.environ.get("OWNER_ID")
DB_FILE = 'db.json'

# --- Load Database ---
db = {}
try:
    with open(DB_FILE, 'r', encoding='utf-8') as f:
        db = json.load(f)
    logger.info(f"Successfully loaded db.json with {len(db)} entries.")
except (FileNotFoundError, json.JSONDecodeError) as e:
    logger.error(f"Could not load or parse db.json: {e}")

# --- Bot and FastAPI App Setup ---
app = FastAPI()
ptb_app = Application.builder().token(TOKEN).build()

# --- Bot Logic ---
def search_subtitles(query: str) -> list:
    if not db:
        return []
    cleaned_query = query.lower().replace('.', ' ').strip()
    results = [
        (imdb_id, entry) for imdb_id, entry in db.items()
        if cleaned_query in entry.get('title', '').lower().replace('.', ' ').strip()
    ]
    return results

# --- Bot Handlers ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hi! I am alive. Send me a movie name to search for subtitles.")

async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text
    logger.info(f"Received search query: '{query}' from user {update.effective_user.name}")

    results = search_subtitles(query)
    if not results:
        await update.message.reply_text(f'Sorry, no results found for "{query}".')
        return

    await update.message.reply_text(f'Found {len(results)} result(s). Showing top 5:')
    for imdb_id, result in results[:5]:
        title = result.get('title', 'N/A').strip()
        poster = result.get('posterMalayalam')
        imdb_url = result.get('imdbURL')
        download_url = result.get('srtURL')
        caption = f"*{title}*\n\n[IMDb]({imdb_url})"

        keyboard = [[InlineKeyboardButton("Download Subtitle", url=download_url)]] if download_url else []
        reply_markup = InlineKeyboardMarkup(keyboard)
        try:
            if poster:
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id, photo=poster, caption=caption,
                    parse_mode='Markdown', reply_markup=reply_markup)
            else:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id, text=caption,
                    parse_mode='Markdown', reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Failed to send message for '{title}'. Error: {e}")
            await update.message.reply_text(f"Could not send result for {title} due to an error.")

# --- FastAPI Routes ---
@app.get("/")
async def root():
    return Response(content="ok", status_code=HTTPStatus.OK)

@app.get("/healthz")
async def healthz():
    return Response(content="healthy", status_code=HTTPStatus.OK)

@app.get("/api/subtitles")
async def api_search(query: str):
    results = search_subtitles(query)
    if not results:
        return {"error": "No results found"}
    return {"count": len(results), "results": [dict(r[1], **{'imdb_id': r[0]}) for r in results]}

@app.post("/webhook")
async def webhook(request: Request):
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        logger.warning("Webhook secret mismatch!")
        return Response(status_code=HTTPStatus.FORBIDDEN)

    data = await request.json()
    update = Update.de_json(data, ptb_app.bot)
    await ptb_app.process_update(update)
    return Response(content="ok", status_code=HTTPStatus.OK)

# --- FastAPI Lifecycle Events ---
@app.on_event("startup")
async def on_startup():
    await ptb_app.initialize()

    ptb_app.add_handler(CommandHandler("start", start_command))
    ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search))

    base_url = os.environ.get("RENDER_EXTERNAL_URL")
    if not base_url:
        logger.error("RENDER_EXTERNAL_URL not set! Cannot set webhook.")
        return

    url = f"{base_url}/webhook"
    await ptb_app.bot.set_webhook(url=url, secret_token=WEBHOOK_SECRET, drop_pending_updates=True)
    logger.info(f"Webhook set to {url}")

    if OWNER_ID:
        await ptb_app.bot.send_message(chat_id=OWNER_ID, text="Bot is up and running!")
        logger.info(f"Sent startup notification to OWNER_ID {OWNER_ID}")

    # Note: ptb_app.start() is for polling and not needed for webhooks.
    # The application processes updates via the /webhook endpoint.

@app.on_event("shutdown")
async def on_shutdown():
    await ptb_app.shutdown()
