import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- Logging Setup ---
logger = logging.getLogger(__name__)

# --- Bot Logic (Search Function) ---
# This function will be provided with the database from the main app
def search_subtitles_in_db(query: str, db: dict) -> list:
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
    # The database will be passed via the context
    db = context.bot_data.get("db", {})
    query = update.message.text
    logger.info(f"Received search query: '{query}' from user {update.effective_user.name}")

    results = search_subtitles_in_db(query, db)
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

def create_ptb_application(token: str) -> Application:
    """Creates and configures the python-telegram-bot Application."""
    application = Application.builder().token(token).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search))

    return application
