import os
import json
import logging
import asyncio
import zipfile
import tempfile
import ast
import unicodedata
from http import HTTPStatus
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse, urljoin
import re
import aiohttp
import aiofiles
import asyncpg
import requests
from bs4 import BeautifulSoup
from datetime import datetime

from fastapi import FastAPI, Request, Response, HTTPException, Query

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Environment Variables ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "a-random-string")
OWNER_ID = os.environ.get("OWNER_ID")
DATABASE_URL = os.environ.get("DATABASE_URL")
BASE_URL = "https://malayalamsubtitles.org"


# --- Global Variables ---
db_pool: Optional[asyncpg.Pool] = None
LOG_GROUP_ID = os.environ.get("LOG_GROUP_ID")
LOG_TOPIC_ID = os.environ.get("LOG_TOPIC_ID")

# --- Menu Messages ---
WELCOME_MESSAGE = "**🎬 Welcome to Malayalam Subtitle Search Bot!**\n\nYour one-stop destination for high-quality Malayalam subtitles for movies and TV shows."
ABOUT_MESSAGE = "**ℹ️ About This Bot**\n\n**🌐 Technical Details:**\n- **Hosted on:** Render.com\n- **Framework:** FastAPI\n- **Database:** PostgreSQL\n- **Developer:** [@Mxxn_Knight](tg://resolve?domain=Mxxn_Knight)\n- **Version:** 3.0"
HELP_MESSAGE = "**❓ How to Use This Bot**\n\n**🔍 Searching:**\n• Type any movie/series name\n• Use English names for better results\n• Add year for specific versions (e.g., \"Dune 2021\")"
TOS_MESSAGE = "**📋 Terms of Service**\n\nBy using this bot, you agree to use subtitles for legally owned content only and respect copyright laws."

# --- Self-Contained Scraper Logic for /rescrape ---
def _get_soup_for_rescrape(url: str) -> Optional[BeautifulSoup]:
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        return BeautifulSoup(response.text, 'html.parser')
    except requests.RequestException as e:
        logger.error(f"Rescrape failed to fetch {url}: {e}")
        return None

def scrape_single_page_for_rescrape(url: str) -> Optional[Dict]:
    soup = _get_soup_for_rescrape(url)
    if not soup: return None
    try:
        details = {'source_url': url}
        title_tag = soup.select_one('h1.entry-title, h1#release-title')
        details['title'] = title_tag.get_text().strip() if title_tag else "Unknown Title"
        # This is a simplified scrape, more fields can be added if needed for a full update
        return details
    except Exception as e:
        logger.error(f"Rescrape failed to parse {url}: {e}")
        return None

# --- Database Functions ---
async def init_db():
    global db_pool
    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        async with db_pool.acquire() as connection:
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY, added_at TIMESTAMPTZ DEFAULT NOW());
            """)
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS subtitles (
                    unique_id TEXT PRIMARY KEY, imdb_id TEXT, source_url TEXT, scraped_at TIMESTAMPTZ,
                    title TEXT, year INTEGER, is_series BOOLEAN, season_number INTEGER, series_name TEXT,
                    total_seasons INTEGER, srt_url TEXT, poster_url TEXT, imdb_url TEXT, description TEXT,
                    director JSONB, genre JSONB, language JSONB, translator JSONB, imdb_rating JSONB,
                    msone_release JSONB, certification JSONB, poster_maker JSONB
                );
            """)
            await connection.execute("CREATE INDEX IF NOT EXISTS idx_subtitles_imdb_id ON subtitles (imdb_id);")
            await connection.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
            await connection.execute("CREATE INDEX IF NOT EXISTS idx_subtitles_title_trgm ON subtitles USING gin (title gin_trgm_ops);")
        logger.info("Database connection pool created and tables initialized.")
    except Exception as e:
        logger.critical(f"Database initialization failed: {e}")
        db_pool = None

async def add_user(user_id: int):
    if db_pool: await db_pool.execute("INSERT INTO users (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING", user_id)

async def search_content(query: str) -> List[asyncpg.Record]:
    if not db_pool or not query: return []
    if query.lower().startswith('tt'):
        return await db_pool.fetch("SELECT * FROM subtitles WHERE imdb_id = $1 LIMIT 20", query)
    return await db_pool.fetch("SELECT *, similarity(title, $1) as relevance FROM subtitles WHERE title % $1 ORDER BY relevance DESC, year DESC LIMIT 20", query)

# --- Formatting & Keyboards ---
def format_movie_details(entry: asyncpg.Record) -> (str, str):
    def get_display_value(value):
        if not value: return None
        return value.get('name', value) if isinstance(value, dict) else value

    title = f"**{entry.get('title', 'Unknown')}** ({entry.get('year')})" if entry.get('year') else f"**{entry.get('title', 'Unknown')}**"
    details = [f"🎬 {title}"]
    fields = [("language", "🗣️"), ("director", "🎬"), ("genre", "🎭"), ("imdb_rating", "⭐"), ("translator", "🌐")]
    for key, icon in fields:
        if val := get_display_value(entry.get(key)): details.append(f"{icon} **{key.replace('_', ' ').title()}:** {val}")
    
    core_details = "\n".join(details)
    synopsis = f"\n\n📖 **Synopsis:**\n{entry['description']}" if entry.get('description') else ""
    return core_details, synopsis

def create_menu_keyboard(current: str) -> Dict:
    buttons = [{'text': "ℹ️ About", 'callback_data': 'menu_about'}, {'text': "❓ Help", 'callback_data': 'menu_help'}, {'text': "📜 TOS", 'callback_data': 'menu_tos'}]
    return {'inline_keyboard': [[b for b in buttons if current not in b['callback_data']], [{'text': '❌ Close', 'callback_data': 'menu_close'}]]}

def create_search_results_keyboard(results: List[asyncpg.Record]) -> Dict:
    keyboard = [[{'text': f"{r['title']} ({r['year']})" if r['year'] else r['title'], 'callback_data': f"view_{r['unique_id']}"}] for r in results]
    keyboard.append([{'text': '❌ Close', 'callback_data': 'menu_close'}])
    return {'inline_keyboard': keyboard}

def create_detail_keyboard(entry: asyncpg.Record) -> Dict:
    keyboard = []
    if entry.get('srt_url'): keyboard.append([{'text': '📥 Download Subtitle', 'callback_data': f"download_{entry['unique_id']}"}])
    if entry.get('imdb_url'): keyboard.append([{'text': '🎬 View on IMDb', 'url': entry['imdb_url']}])
    return {'inline_keyboard': keyboard}

# --- Core Handlers ---
async def handle_callback_query(callback_data: str, message: dict, chat_id: str) -> Optional[Dict]:
    if not db_pool: return None
    action, _, value = callback_data.partition('_')

    if action == 'menu':
        if value == 'close': return {'method': 'deleteMessage', 'chat_id': chat_id, 'message_id': message['message_id']}
        text_map = {'about': ABOUT_MESSAGE, 'help': HELP_MESSAGE, 'tos': TOS_MESSAGE}
        return {'method': 'editMessageText', 'text': text_map.get(value), 'reply_markup': create_menu_keyboard(value), 'parse_mode': 'Markdown', 'chat_id': chat_id, 'message_id': message['message_id']}

    elif action == 'view':
        if entry := await db_pool.fetchrow("SELECT * FROM subtitles WHERE unique_id = $1", value):
            core, synopsis = format_movie_details(entry)
            return {'method': 'editMessageText', 'chat_id': chat_id, 'message_id': message['message_id'], 'text': f"{core}{synopsis}", 'reply_markup': create_detail_keyboard(entry), 'parse_mode': 'Markdown'}

    elif action == 'download':
        if entry := await db_pool.fetchrow("SELECT title, srt_url FROM subtitles WHERE unique_id = $1", value):
            if entry['srt_url']:
                # Simplified download flow for this refactoring
                return {'chat_id': chat_id, 'text': f"⬇️ [Download Subtitle]({entry['srt_url']}) for **{entry['title']}**", 'parse_mode': 'Markdown'}
    return None

async def handle_telegram_message(message_data: dict) -> Optional[Dict]:
    if 'callback_query' in message_data:
        cb = message_data['callback_query']
        await add_user(cb['from']['id'])
        if response := await handle_callback_query(cb['data'], cb['message'], str(cb['message']['chat']['id'])):
            await send_telegram_message(response)
        await send_telegram_message({'method': 'answerCallbackQuery', 'callback_query_id': cb['id']})
        return None

    message = message_data.get('message', {})
    text, chat_id, user = message.get('text', '').strip(), message.get('chat', {}).get('id'), message.get('from', {})
    if not (user_id := user.get('id')) or not db_pool: return None

    # --- Feedback Handling Logic ---
    if reply := message.get('reply_to_message'):
        if reply.get('from', {}).get('is_bot') and "Send Your Feedback" in reply.get('text', ''):
            # 1. Format user details
            user_details = (
                f"📝 **New Feedback**\n\n"
                f"**From:** [{user.get('first_name', '')}](tg://user?id={user_id})\n"
                f"**Username:** @{user.get('username', 'N/A')}\n"
                f"**ID:** `{user_id}`\n"
                f"--------------------\n"
            )
            # 2. Forward the feedback to the log group/admin
            if LOG_GROUP_ID:
                if message.get('text'):
                    await send_telegram_message({'chat_id': LOG_GROUP_ID, 'text': user_details + message.get('text'), 'parse_mode': 'Markdown'})
                elif message.get('photo'):
                    await send_telegram_message({'method': 'sendPhoto', 'chat_id': LOG_GROUP_ID, 'photo': message['photo'][-1]['file_id'], 'caption': user_details, 'parse_mode': 'Markdown'})

            # 3. Send confirmation and schedule cleanup
            confirm_msg = await send_telegram_message({'chat_id': chat_id, 'text': "✅ Thank you! Your feedback has been sent."})
            if confirm_msg_id := confirm_msg.get('result', {}).get('message_id'):
                await asyncio.sleep(5)
                await send_telegram_message({'method': 'deleteMessage', 'chat_id': chat_id, 'message_id': reply.get('message_id')})
                await send_telegram_message({'method': 'deleteMessage', 'chat_id': chat_id, 'message_id': confirm_msg_id})

            return None # Stop further processing

    await add_user(user_id)

    if text.startswith('/'):
        parts = text.split()
        command, args = parts[0], parts[1:]

        if str(user_id) == OWNER_ID: # Admin commands
            if command == '/stats':
                stats = await db_pool.fetchrow("SELECT (SELECT COUNT(*) FROM subtitles) as total, (SELECT COUNT(*) FROM users) as users")
                return {'chat_id': chat_id, 'text': f"📊 **Bot Stats:** {stats['total']} subs, {stats['users']} users."}
            elif command == '/rescrape' and args:
                entry = await db_pool.fetchrow("SELECT source_url, title FROM subtitles WHERE unique_id = $1", args[0])
                if entry and entry['source_url']:
                    await send_telegram_message({'chat_id': chat_id, 'text': f"⏳ Rescraping **{entry['title']}**..."})
                    # Simplified logic: just update title and timestamp for now
                    if new_data := scrape_single_page_for_rescrape(entry['source_url']):
                        await db_pool.execute("UPDATE subtitles SET title=$1, scraped_at=$2 WHERE unique_id=$3", new_data['title'], datetime.now(), args[0])
                        return {'chat_id': chat_id, 'text': f"✅ Rescraped **{new_data['title']}**."}
                return {'chat_id': chat_id, 'text': f"❌ Failed to rescrape `{args[0]}`."}

        if command == '/start': return {'chat_id': chat_id, 'text': WELCOME_MESSAGE, 'reply_markup': create_menu_keyboard('home'), 'parse_mode': 'Markdown'}
        if command == '/feedback':
            return {
                'chat_id': chat_id,
                'text': "📝 **Send Your Feedback**\n\nPlease send your feedback now. You can send text or an image.",
                'reply_markup': {'force_reply': True, 'selective': True, 'input_field_placeholder': 'Your feedback...'}
            }

    if len(text) < 2: return {'chat_id': chat_id, 'text': "Please use at least 2 characters."}
    if results := await search_content(text):
        return {'chat_id': chat_id, 'text': f"🔍 **Found these for '{text}':**", 'reply_markup': create_search_results_keyboard(results), 'parse_mode': 'Markdown'}
    return {'chat_id': chat_id, 'text': f'😔 No subtitles found for "{text}"'}

async def send_telegram_message(data: dict):
    if not TOKEN or not data: return {}
    method = data.pop('method', 'sendMessage')
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=data) as resp:
                if resp.status != 200: logger.error(f"Telegram API Error: {await resp.text()}")
                return await resp.json()
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return {}

# --- FastAPI App ---
app = FastAPI(title="Subtitle Search Bot API", version="3.0.0", redoc_url=None, docs_url=None)

@app.on_event("startup")
async def startup_event():
    logger.info("Starting application...")
    await init_db()

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down application...")
    if db_pool: await db_pool.close()

@app.post("/telegram")
async def telegram_webhook(request: Request):
    if not TOKEN: return Response(status_code=HTTPStatus.SERVICE_UNAVAILABLE)
    if WEBHOOK_SECRET != request.headers.get("X-Telegram-Bot-Api-Secret-Token"): return Response(status_code=HTTPStatus.FORBIDDEN)
    try:
        if response_data := await handle_telegram_message(await request.json()):
            await send_telegram_message(response_data)
        return Response(status_code=HTTPStatus.OK)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return Response(status_code=HTTPStatus.INTERNAL_SERVER_ERROR)
