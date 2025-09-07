import logging
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import TelegramError

# --- Logging Setup ---
logger = logging.getLogger(__name__)

# --- Rate Limiting ---
user_last_request: Dict[int, datetime] = defaultdict(lambda: datetime.min)
RATE_LIMIT_SECONDS = 2

def is_rate_limited(user_id: int) -> bool:
    """Check if user is rate limited."""
    now = datetime.now()
    last_request = user_last_request[user_id]
    
    if now - last_request < timedelta(seconds=RATE_LIMIT_SECONDS):
        return True
    
    user_last_request[user_id] = now
    return False

# --- Search Function ---
def search_subtitles_in_db(query: str, db: Dict[str, Any]) -> list:
    """Search subtitles in database with improved matching."""
    if not db or not query:
        return []
    
    cleaned_query = query.lower().replace('.', ' ').replace('-', ' ').strip()
    query_words = cleaned_query.split()
    
    results = []
    for imdb_id, entry in db.items():
        title = entry.get('title', '').lower().replace('.', ' ').replace('-', ' ').strip()
        
        # Check if all query words are in title
        if all(word in title for word in query_words):
            results.append((imdb_id, entry))
        # Fallback to original matching
        elif cleaned_query in title:
            results.append((imdb_id, entry))
    
    # Sort by title length (shorter titles first - more relevant)
    results.sort(key=lambda x: len(x[1].get('title', '')))
    
    return results

# --- Bot Handlers ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    user = update.effective_user
    logger.info(f"Start command from user: {user.username or user.first_name} ({user.id})")
    
    welcome_text = (
        "🎬 **Welcome to Malayalam Subtitle Search Bot!**\n\n"
        "I can help you find Malayalam subtitles for movies and TV shows.\n\n"
        "📝 **How to use:**\n"
        "• Just send me a movie or show name\n"
        "• I'll search and show you available subtitles\n"
        "• Click the download button to get the subtitle file\n\n"
        "🔍 **Example:** Send me \"Dune\" or \"Breaking Bad\"\n\n"
        "⚡ *Powered by malayalamsubtitles.org*"
    )
    
    try:
        await update.message.reply_text(
            welcome_text, 
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
    except TelegramError as e:
        logger.error(f"Failed to send start message: {e}")
        await update.message.reply_text("Hi! Send me a movie name to search for subtitles.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    help_text = (
        "🆘 **Help - How to use this bot:**\n\n"
        "1️⃣ Send me any movie or TV show name\n"
        "2️⃣ I'll search for Malayalam subtitles\n"
        "3️⃣ Browse the results with posters and details\n"
        "4️⃣ Click 'Download Subtitle' to get the file\n\n"
        "💡 **Tips:**\n"
        "• Try different name variations if no results\n"
        "• Use English movie names for better results\n"
        "• Wait a few seconds between searches\n\n"
        "🔧 **Commands:**\n"
        "• /start - Welcome message\n"
        "• /help - This help message\n"
        "• /stats - Bot statistics"
    )
    
    try:
        await update.message.reply_text(
            help_text,
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
    except TelegramError as e:
        logger.error(f"Failed to send help message: {e}")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stats command."""
    db = context.bot_data.get("db", {})
    stats_text = f"📊 **Bot Statistics:**\n\n🎬 Total Movies/Shows: {len(db)}\n🤖 Bot Status: Online"
    
    try:
        await update.message.reply_text(stats_text, parse_mode='Markdown')
    except TelegramError as e:
        logger.error(f"Failed to send stats message: {e}")

async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle search queries."""
    user = update.effective_user
    query = update.message.text.strip()
    
    # Rate limiting
    if is_rate_limited(user.id):
        await update.message.reply_text("⏳ Please wait a moment before searching again.")
        return
    
    # Validate query
    if len(query) < 2:
        await update.message.reply_text("🔍 Please send a movie name with at least 2 characters.")
        return
    
    if len(query) > 100:
        await update.message.reply_text("🔍 Movie name is too long. Please use a shorter search term.")
        return
    
    logger.info(f"Search query: '{query}' from user {user.username or user.first_name} ({user.id})")
    
    # Get database
    db = context.bot_data.get("db", {})
    if not db:
        await update.message.reply_text("❌ Database not available. Please try again later.")
        return
    
    # Search
    try:
        results = search_subtitles_in_db(query, db)
        
        if not results:
            await update.message.reply_text(
                f'😔 Sorry, no subtitles found for "{query}".\n\n'
                '💡 Try different keywords or check spelling.'
            )
            return
        
        # Show results count
        total_results = len(results)
        showing = min(5, total_results)
        
        await update.message.reply_text(
            f'🎯 Found {total_results} result(s) for "{query}". Showing top {showing}:'
        )
        
        # Send results
        for i, (imdb_id, result) in enumerate(results[:5], 1):
            try:
                await send_result(update, context, result, imdb_id, i)
                # Small delay between messages to avoid flooding
                if i < showing:
                    await asyncio.sleep(0.5)
                    
            except Exception as e:
                logger.error(f"Failed to send result {i} for '{query}': {e}")
                await update.message.reply_text(f"❌ Could not send result #{i} due to an error.")
        
        # Show more results info
        if total_results > 5:
            await update.message.reply_text(
                f"📝 Showing 5 of {total_results} results. Try a more specific search for better results."
            )
                
    except Exception as e:
        logger.error(f"Search error for query '{query}': {e}")
        await update.message.reply_text("❌ Something went wrong. Please try again later.")

async def send_result(update: Update, context: ContextTypes.DEFAULT_TYPE, result: dict, imdb_id: str, index: int):
    """Send a single search result."""
    title = result.get('title', 'Unknown Title').strip()
    poster = result.get('posterMalayalam')
    imdb_url = result.get('imdbURL')
    download_url = result.get('srtURL')
    
    # Create caption
    caption_parts = [f"🎬 **{title}**"]
    
    if imdb_url:
        caption_parts.append(f"📝 [View on IMDb]({imdb_url})")
    
    caption_parts.append(f"🆔 ID: `{imdb_id}`")
    caption = "\n".join(caption_parts)
    
    # Create keyboard
    keyboard = []
    if download_url:
        keyboard.append([InlineKeyboardButton("📥 Download Subtitle", url=download_url)])
    
    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    
    # Send message
    try:
        if poster and poster.startswith(('http://', 'https://')):
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=poster,
                caption=caption,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=caption,
                parse_mode='Markdown',
                reply_markup=reply_markup,
                disable_web_page_preview=True
            )
    except TelegramError as e:
        logger.error(f"Failed to send result message: {e}")
        # Fallback to simple text
        simple_text = f"{index}. {title}"
        if download_url:
            simple_text += f"\nDownload: {download_url}"
        await update.message.reply_text(simple_text)

async def handle_unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle unknown commands."""
    await update.message.reply_text(
        "❓ Unknown command. Send /help to see available commands or just send a movie name to search."
    )

def create_handlers(application: Application):
    """Add handlers to the application."""
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(MessageHandler(filters.COMMAND, handle_unknown_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search))
    
    logger.info("Bot handlers added successfully")

# Keep the old function for compatibility
def create_ptb_application(token: str) -> Application:
    """Create and configure the python-telegram-bot Application."""
    application = Application.builder().token(token).build()
    create_handlers(application)
    return application
