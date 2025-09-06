import os
import json
import logging
import asyncio
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Environment and App Setup ---
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
if not TOKEN:
    raise ValueError("No TELEGRAM_BOT_TOKEN found in environment variables")

# Use a single event loop for all async operations. This is the standard "bridge" pattern.
try:
    loop = asyncio.get_running_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

app = Flask(__name__)

# --- Load Database ---
try:
    with open('db.json', 'r', encoding='utf-8') as f:
        db = json.load(f)
    logger.info(f"Successfully loaded db.json with {len(db)} entries.")
except (FileNotFoundError, json.JSONDecodeError) as e:
    db = {}
    logger.error(f"Could not load or parse db.json: {e}")

# --- Bot Handler Functions ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message."""
    await update.message.reply_text('Welcome! Send me a movie or series name to search for subtitles.')

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Searches for subtitles and sends results."""
    # Fix: query must be converted to lower case for case-insensitive search
    query = update.message.text.lower()
    logger.info(f"Received search query: '{query}' from user {update.effective_user.name}")

    # Fix: Use 'in' operator for substring check
    results = [
        (imdb_id, entry) for imdb_id, entry in db.items()
        if query in entry.get('title', '').lower().replace('.', ' ').strip()
    ]

    if not results:
        await update.message.reply_text(f'Sorry, no results found for "{update.message.text}".')
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
            # Fix: Use context.bot, which is available in all handlers
            if poster:
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id, photo=poster, caption=caption,
                    parse_mode='Markdown', reply_markup=reply_markup
                )
            else:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id, text=caption,
                    parse_mode='Markdown', reply_markup=reply_markup
                )
        except Exception as e:
            logger.error(f"Failed to send message for '{title}'. Error: {e}")
            await update.message.reply_text(f"Could not send result for {title} due to an error.")

# --- Application Setup ---
# Build the application and add handlers
application = Application.builder().token(TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search))

# Initialize the application once at the module level
logger.info("Initializing the application...")
loop.run_until_complete(application.initialize())
logger.info("Application initialized.")


# --- Flask API Routes ---
@app.route('/')
def index():
    return "API and Bot Server is running."

@app.route('/api/<imdb_id>')
def get_movie_by_id(imdb_id):
    """Serves subtitle data for a given IMDb ID."""
    movie_data = db.get(imdb_id)
    return jsonify(movie_data) if movie_data else (jsonify({"error": "Movie not found"}), 404)

@app.route('/telegram', methods=['POST'])
def webhook():
    """Webhook endpoint. It receives updates from Telegram."""
    update = Update.de_json(request.get_json(force=True), application.bot)
    loop.run_until_complete(application.process_update(update))
    return 'ok'

@app.route('/set_webhook', methods=['GET'])
def set_webhook():
    """Manually sets the webhook and sends a startup notification."""
    webhook_base_url = os.environ.get("WEBHOOK_URL")
    if not webhook_base_url:
        return "WEBHOOK_URL environment variable not set!", 500

    webhook_url = f'{webhook_base_url}/telegram'

    async def set_hook():
        # No need to initialize here, it's already done at startup
        await application.bot.set_webhook(webhook_url)
        owner_id = os.environ.get("OWNER_ID")
        if owner_id:
            # Fix: Use application.bot, which is now guaranteed to be initialized
            await application.bot.send_message(chat_id=owner_id, text="Bot is up and running!")
            logger.info(f"Sent startup notification to OWNER_ID {owner_id}")

    loop.run_until_complete(set_hook())
    return f"Webhook set to {webhook_url}"

if __name__ == '__main__':
    app.run(debug=True, port=5000)
