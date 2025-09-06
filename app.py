import os
import json
import logging
import requests
import io
import zipfile
from flask import Flask, request, jsonify
import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

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
        if query in entry.get('title', '').lower().replace('.', ' ').strip()
    ]

    if not results:
        await update.message.reply_text(f'Sorry, no results found for "{update.message.text}".')
        return

    await update.message.reply_text(f'Found {len(results)} result(s). Showing top 5:')

    for imdb_id, result in results[:5]:
        title = result.get('title', 'N/A').replace('\n', ' ').replace('\t', '').strip()
        poster = result.get('posterMalayalam')
        imdb_url = result.get('imdbURL')

        caption = f"*{title}*\n\n[IMDb]({imdb_url})"
        
        keyboard = []
        if result.get('isSeries') and result.get('seasons'):
            for season in result['seasons']:
                # Use imdb_id and season name as callback data to fetch the correct entry
                callback_data = f"season:{imdb_id}:{season['season_name']}"
                keyboard.append([InlineKeyboardButton(season['season_name'], callback_data=callback_data)])
        else:
            download_url = result.get('srtURL')
            if download_url:
                callback_data = f"download:{imdb_id}"
                keyboard.append([InlineKeyboardButton("Download Subtitle", callback_data=callback_data)])
        
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
            await bot.send_message(
                chat_id=update.effective_chat.id,
                text=caption,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Parses the CallbackQuery and sends the subtitle file."""
    query = update.callback_query
    await query.answer()

    data_parts = query.data.split(':')
    action = data_parts[0]
    imdb_id = data_parts[1]

    entry = db.get(imdb_id)
    if not entry:
        await query.edit_message_text(text="Sorry, I couldn't find that entry.")
        return

    if action == 'download':
        download_url = entry.get('srtURL')
        if download_url:
            try:
                await query.edit_message_text(text="Downloading subtitle...")
                response = requests.get(download_url)
                response.raise_for_status()

                content_type = response.headers.get('content-type')

                if 'zip' in content_type:
                    with io.BytesIO(response.content) as zip_stream:
                        with zipfile.ZipFile(zip_stream) as zip_file:
                            for file_info in zip_file.infolist():
                                if file_info.filename.endswith('.srt'):
                                    with zip_file.open(file_info) as srt_file:
                                        await context.bot.send_document(
                                            chat_id=query.message.chat_id,
                                            document=srt_file.read(),
                                            filename=file_info.filename
                                        )
                    await query.delete_message()
                else:
                    file_name = f"{entry.get('title', 'subtitle')}.srt"
                    file_stream = io.BytesIO(response.content)

                    await context.bot.send_document(
                        chat_id=query.message.chat_id,
                        document=file_stream,
                        filename=file_name
                    )
                    await query.delete_message()
            except requests.RequestException as e:
                logger.error(f"Failed to download file: {e}")
                await query.edit_message_text(text="Sorry, I could not download the subtitle file.")

    elif action == 'season':
        season_name = data_parts[2]
        # This part will be enhanced later to handle season-specific downloads
        download_url = entry.get('srtURL')
        if download_url:
            callback_data = f"download:{imdb_id}"
            keyboard = [[InlineKeyboardButton("Download Subtitle", callback_data=callback_data)]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_caption(caption=f"*{season_name}*", reply_markup=reply_markup, parse_mode='Markdown')



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
application.add_handler(CallbackQueryHandler(button))

if __name__ == '__main__':
    # This part is for local development and won't be used by Gunicorn on Render
    logger.info("Starting Flask app for local development...")
    app.run(debug=True, port=5000)
