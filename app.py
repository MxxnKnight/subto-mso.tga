import os
import json
import logging
import asyncio
import zipfile
import tempfile
from typing import Dict, Any, List, Optional
from urllib.parse import urljoin
import re
import aiohttp
import asyncpg
import requests
from bs4 import BeautifulSoup
from datetime import datetime

from fastapi import FastAPI, Request, Response

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Environment Variables ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "a-random-string")
OWNER_ID = os.environ.get("OWNER_ID")
DATABASE_URL = os.environ.get("DATABASE_URL")
LOG_GROUP_ID = os.environ.get("LOG_GROUP_ID")
FORCE_SUB_CHANNEL_ID = os.environ.get("FORCE_SUB_CHANNEL_ID")
FORCE_SUB_CHANNEL_LINK = os.environ.get("FORCE_SUB_CHANNEL_LINK")
BASE_URL = "https://malayalamsubtitles.org"

# --- Global Variables ---
db_pool: Optional[asyncpg.Pool] = None

# --- Menu Messages ---
WELCOME_MESSAGE = "**🎬 Welcome to Malayalam Subtitle Search Bot!**\n\nYour one-stop destination for high-quality Malayalam subtitles for movies and TV shows."
ABOUT_MESSAGE = "**ℹ️ About This Bot**\n\n**🌐 Technical Details:**\n- **Hosted on:** Render.com\n- **Framework:** FastAPI\n- **Database:** PostgreSQL\n- **Developer:** [@Mxxn_Knight](tg://resolve?domain=Mxxn_Knight)\n- **Version:** 3.2"
HELP_MESSAGE = "**❓ How to Use This Bot**\n\n**🔍 Searching:**\n• Type any movie/series name\n• Use English names for better results\n• Add year for specific versions (e.g., \"Dune 2021\")"

# --- Self-Contained Scraper Logic ---
def _get_soup(url: str) -> Optional[BeautifulSoup]:
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        return BeautifulSoup(response.text, 'html.parser')
    except requests.RequestException as e:
        logger.error(f"Scraper failed to fetch {url}: {e}")
        return None

def _clean_text(text: str) -> str: return re.sub(r'\s+', ' ', text.strip()) if text else ""
def _extract_imdb_id(url: str) -> Optional[str]: return match.group(0) if (match := re.search(r'tt\d+', url or "")) else None

def scrape_page_details(url: str) -> Optional[Dict]:
    soup = _get_soup(url)
    if not soup: return None
    try:
        details = {'source_url': url}
        details['title'] = _clean_text(soup.select_one('h1.entry-title, h1#release-title').get_text())
        if imdb_tag := soup.select_one('a#imdb-button, a[href*="imdb.com"]'):
            details['imdb_url'] = imdb_tag.get('href')
            details['imdb_id'] = _extract_imdb_id(details['imdb_url'])
        # This is a full scraper, add all fields needed
        if srt_tag := soup.select_one('a#download-button'):
            details['srt_url'] = srt_tag.get('data-downloadurl') or srt_tag.get('href')
        if poster_tag := soup.select_one('figure#release-poster img, .entry-content figure img'):
            details['poster_url'] = urljoin(BASE_URL, poster_tag['src'])
        if desc_tag := soup.select_one('div#synopsis, .entry-content p'):
            details['description'] = _clean_text(desc_tag.get_text(separator='\n', strip=True))
        return details
    except Exception as e:
        logger.error(f"Full scraping failed for {url}: {e}")
        return None

# --- Database Functions ---
async def init_db():
    global db_pool
    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        async with db_pool.acquire() as conn:
            await conn.execute("CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY);")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS subtitles (
                    unique_id TEXT PRIMARY KEY, imdb_id TEXT, source_url TEXT, scraped_at TIMESTAMPTZ,
                    title TEXT, year INTEGER, is_series BOOLEAN, season_number INTEGER, series_name TEXT,
                    srt_url TEXT, poster_url TEXT, imdb_url TEXT, description TEXT
                );
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_subtitles_imdb_id ON subtitles (imdb_id);")
            await conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_subtitles_title_trgm ON subtitles USING gin (title gin_trgm_ops);")
        logger.info("Database connection pool initialized.")
    except Exception as e:
        logger.critical(f"Database initialization failed: {e}")
        db_pool = None

async def upsert_subtitle(details: dict):
    if not db_pool or not details.get('imdb_id'): return
    season_info = _extract_season_info(details.get('title', ''))
    unique_id = f"{details['imdb_id']}-S{season_info['season_number']}" if season_info.get('is_series') else details['imdb_id']
    query = """
        INSERT INTO subtitles (unique_id, imdb_id, title, source_url, scraped_at, srt_url, poster_url, imdb_url, description)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT (unique_id) DO UPDATE SET
            title = EXCLUDED.title, scraped_at = EXCLUDED.scraped_at, srt_url = EXCLUDED.srt_url,
            poster_url = EXCLUDED.poster_url, imdb_url = EXCLUDED.imdb_url, description = EXCLUDED.description;
    """
    await db_pool.execute(query, unique_id, details['imdb_id'], details.get('title'), details.get('source_url'), datetime.now(), details.get('srt_url'), details.get('poster_url'), details.get('imdb_url'), details.get('description'))

async def check_user_membership(user_id: int) -> bool:
    if not FORCE_SUB_CHANNEL_ID: return True
    try:
        member = await send_telegram_message({'method': 'getChatMember', 'chat_id': FORCE_SUB_CHANNEL_ID, 'user_id': user_id})
        return member.get('result', {}).get('status') not in ['left', 'kicked']
    except Exception: return False

# --- Core Handlers & Bot Logic ---
async def handle_telegram_message(message_data: dict) -> Optional[Dict]:
    user, message, cb_data = None, None, None
    if 'callback_query' in message_data:
        cb = message_data['callback_query']
        user, message, cb_data = cb['from'], cb['message'], cb['data']
    elif 'message' in message_data:
        message = message_data['message']
        user = message.get('from')

    if not user or not (user_id := user.get('id')): return None

    if not await check_user_membership(user_id):
        if cb_data: await send_telegram_message({'method': 'answerCallbackQuery', 'callback_query_id': cb['id'], 'text': "You must join our channel to use the bot!", 'show_alert': True})
        return {'chat_id': user['id'], 'text': "You must join our channel to use this bot.", 'reply_markup': {'inline_keyboard': [[{'text': "Join Channel", 'url': FORCE_SUB_CHANNEL_LINK}]]}}

    await db_pool.execute("INSERT INTO users (user_id) VALUES ($1) ON CONFLICT DO NOTHING", user_id)

    if cb_data: # Callback Query
        action, _, value = cb_data.partition('_')
        if action == 'view' and (entry := await db_pool.fetchrow("SELECT * FROM subtitles WHERE unique_id = $1", value)):
            # Simplified logic for brevity
            return {'method': 'editMessageText', 'chat_id': message['chat']['id'], 'message_id': message['message_id'], 'text': f"**{entry['title']}**", 'reply_markup': create_detail_keyboard(entry)}
        return {'method': 'answerCallbackQuery', 'callback_query_id': cb['id']}

    text = message.get('text', '').strip()
    if not text: return None

    if text.startswith('/'): # Commands
        command, *args = text.split()
        if command == '/start': return {'chat_id': user_id, 'text': WELCOME_MESSAGE}
        if command == '/feedback': return {'chat_id': user_id, 'text': "📝 Please send your feedback now.", 'reply_markup': {'force_reply': True}}
        if str(user_id) == OWNER_ID and args:
            if command == '/add' and (details := scrape_page_details(args[0])):
                await upsert_subtitle(details)
                return {'chat_id': user_id, 'text': f"✅ Added/Updated **{details['title']}**."}

    if reply := message.get('reply_to_message'): # Feedback Reply
        if reply.get('from', {}).get('is_bot') and "Send Your Feedback" in reply.get('text', ''):
            user_details = f"📝 Feedback from [{user.get('first_name')}](tg://user?id={user_id})"
            if LOG_GROUP_ID:
                if message.get('text'): await send_telegram_message({'chat_id': LOG_GROUP_ID, 'text': f"{user_details}:\n\n{message['text']}", 'parse_mode': 'Markdown'})
                elif photo := message.get('photo'): await send_telegram_message({'method': 'sendPhoto', 'chat_id': LOG_GROUP_ID, 'photo': photo[-1]['file_id'], 'caption': user_details, 'parse_mode': 'Markdown'})
            await send_telegram_message({'chat_id': user_id, 'text': "✅ Thank you for your feedback!"})
            return None

    # Search
    if len(text) > 1 and (results := await db_pool.fetch("SELECT * FROM subtitles WHERE title ILIKE $1 LIMIT 10", f"%{text}%")):
        return {'chat_id': user_id, 'text': f"🔍 Found these for '{text}':", 'reply_markup': create_search_results_keyboard(results)}
    return {'chat_id': user_id, 'text': f'😔 No subtitles found for "{text}"'}

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
app = FastAPI(title="Subtitle Search Bot API", version="3.2", redoc_url=None, docs_url=None)
@app.on_event("startup")
async def startup_event(): await init_db()
@app.on_event("shutdown")
async def shutdown_event():
    if db_pool: await db_pool.close()

@app.post("/telegram")
async def telegram_webhook(request: Request):
    if WEBHOOK_SECRET != request.headers.get("X-Telegram-Bot-Api-Secret-Token"): return Response(status_code=HTTPStatus.FORBIDDEN)
    try:
        if response_data := await handle_telegram_message(await request.json()):
            await send_telegram_message(response_data)
        return Response(status_code=HTTPStatus.OK)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return Response(status_code=HTTPStatus.INTERNAL_SERVER_ERROR)
