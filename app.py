import os
import json
import logging
from flask import Flask, request, jsonify
import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Bot and Flask Setup ---
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
if not TOKEN:
    raise ValueError("No TELEGRAM_BOT_TOKEN found in environment variables")

bot = telegram.Bot(token=TOKEN)
app = Flask(__name__)

# --- Load Database ---
try:
    with open('db.json', 'r', encoding='utf-8') as f:
        db = json.load(f)
    logger.info(f"Successfully loaded db.json with {len(db)} entries.")
except FileNotFoundError:
    db = {}
    logger.warning("db.json not found. The bot will not have data to search.")
except json.JSONDecodeError:
    db = {}
    logger.error("Could not decode db.json. File might be corrupt.")


# --- Bot Handler Functions ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message when the /start command is issued."""
    await update.message.reply_text('Welcome! Send me a movie or series name to search for subtitles.')

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Searches for subtitles based on user's message."""
    query = update.message.text.lower()
    logger.info(f"Received search query: '{query}' from user {update.effective_user.name}")
    
    results = [
        (imdb_id, entry) for imdb_id, entry in db.items() 
        if entry.get('title', '').lower().replace('.', ' ').strip().includes(query)
    ]

    if not results:
        await update.message.reply_text(f'Sorry, no results found for "{update.message.text}".')
        return

    await update.message.reply_text(f'Found {len(results)} result(s). Showing top 5:')

    for imdb_id, result in results[:5]:
        title = result.get('title', 'N/A').replace('\n', ' ').replace('\t', '').strip()
        poster = result.get('posterMalayalam')
        imdb_url = result.get('imdbURL')
        download_url = result.get('srtURL')

        caption = f"*{title}*\n\n[IMDb]({imdb_url})"
        
        keyboard = []
        if download_url:
            keyboard.append([InlineKeyboardButton("Download Subtitle", url=download_url)])
        
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

        try:
            if poster:
                await bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=poster,
                    caption=caption,
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
            else: # Fallback to text message if no poster
                await bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=caption,
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
        except Exception as e:
            logger.error(f"Failed to send message for '{title}'. Error: {e}")
            # Fallback for failed photo sending
            await bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"{caption}\nDownload: {download_url}",
                parse_mode='Markdown'
            )


# --- Flask API Routes ---
@app.route('/')
def index():
    return "API and Bot Server is running."

@app.route('/api/<imdb_id>')
def get_movie_by_id(imdb_id):
    """Serves subtitle data for a given IMDb ID."""
    movie_data = db.get(imdb_id)
    if movie_data:
        return jsonify(movie_data)
    return jsonify({"error": "Movie not found"}), 404

@app.route('/telegram', methods=['POST'])
async def webhook():
    """Webhook endpoint for the Telegram bot."""
    update = Update.de_json(request.get_json(force=True), bot)
    await application.process_update(update)
    return 'ok'

@app.route('/set_webhook', methods=['GET'])
def set_webhook():
    """A route to manually set the webhook (for development)."""
    # Note: On Render, the WEBHOOK_URL should be set as an environment variable.
    # Example: https://your-app-name.onrender.com
    webhook_base_url = os.environ.get("WEBHOOK_URL")
    if not webhook_base_url:
        return "WEBHOOK_URL environment variable not set!", 500

    webhook_url = f'{webhook_base_url}/telegram'
    success = bot.set_webhook(webhook_url)
    if success:
        return f"Webhook set to {webhook_url}"
    else:
        return "Webhook setup failed!", 500

# --- Application Setup ---
application = Application.builder().token(TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search))

if __name__ == '__main__':
    # This part is for local development and won't be used by Gunicorn on Render
    logger.info("Starting Flask app for local development...")
    app.run(debug=True, port=5000)
