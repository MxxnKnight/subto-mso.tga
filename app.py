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
ABOUT_MESSAGE = "**ℹ️ About This Bot**\n\n**🌐 Technical Details:**\n- **Hosted on:** Render.com\n- **Framework:** FastAPI\n- **Database:** PostgreSQL\n- **Developer:** [@Mxxn_Knight](tg://resolve?domain=Mxxn_Knight)\n- **Version:** 3.3"
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
def _extract_season_info(title: str) -> Dict[str, Any]:
    patterns = [r'Season\s*(\d+)', r'സീസൺ\s*(\d+)', r'S0?(\d+)', r'സീസണ്‍\s*(\d+)']
    for pattern in patterns:
        match = re.search(pattern, title, re.IGNORECASE)
        if match:
            series_name = re.split(r'\s+Season\s+\d|\s+സീസൺ\s+\d', title, 1, re.IGNORECASE)[0].strip()
            return {'is_series': True, 'season_number': int(match.group(1)), 'series_name': series_name}
    return {'is_series': False, 'season_number': None, 'series_name': None}

def scrape_page_details(url: str) -> Optional[Dict]:
    soup = _get_soup(url)
    if not soup: return None
    try:
        details = {'source_url': url}
        details['title'] = _clean_text(soup.select_one('h1.entry-title, h1#release-title').get_text())
        details.update(_extract_season_info(details['title']))
        if imdb_tag := soup.select_one('a#imdb-button, a[href*="imdb.com"]'):
            details['imdb_url'] = imdb_tag.get('href')
            details['imdb_id'] = _extract_imdb_id(details['imdb_url'])
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
        INSERT INTO subtitles (unique_id, imdb_id, title, source_url, scraped_at, srt_url, poster_url, imdb_url, description, is_series, season_number, series_name)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        ON CONFLICT (unique_id) DO UPDATE SET
            title = EXCLUDED.title, scraped_at = EXCLUDED.scraped_at, srt_url = EXCLUDED.srt_url,
            poster_url = EXCLUDED.poster_url, imdb_url = EXCLUDED.imdb_url, description = EXCLUDED.description;
    """
    await db_pool.execute(query, unique_id, details['imdb_id'], details.get('title'), details.get('source_url'), datetime.now(), details.get('srt_url'), details.get('poster_url'), details.get('imdb_url'), details.get('description'), season_info['is_series'], season_info['season_number'], season_info['series_name'])

async def add_user(user_id: int):
    if not db_pool: return
    try:
        await db_pool.execute("INSERT INTO users (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING;", user_id)
    except Exception as e:
        logger.error(f"Failed to add user {user_id}: {e}")

async def check_user_membership(user_id: int) -> bool:
    if not FORCE_SUB_CHANNEL_ID: return True
    try:
        member = await send_telegram_message({'method': 'getChatMember', 'chat_id': FORCE_SUB_CHANNEL_ID, 'user_id': user_id})
        return member.get('result', {}).get('status') not in ['left', 'kicked']
    except Exception: return False

# --- Formatting & Keyboards ---
def create_menu_keyboard(current: str) -> Dict:
    buttons = [{'text': "ℹ️ About", 'callback_data': 'menu_about'}, {'text': "❓ Help", 'callback_data': 'menu_help'}]
    if current != 'home': buttons.insert(0, {'text': "🏠 Home", 'callback_data': 'menu_home'})
    return {'inline_keyboard': [buttons, [{'text': '❌ Close', 'callback_data': 'menu_close'}]]}

def create_search_results_keyboard(results: List[asyncpg.Record]) -> Dict:
    keyboard = [[{'text': f"{r['title']} ({r['year']})" if r['year'] else r['title'], 'callback_data': f"view_{r['unique_id']}"}] for r in results]
    keyboard.append([{'text': '❌ Close', 'callback_data': 'menu_close'}])
    return {'inline_keyboard': keyboard}

def create_detail_keyboard(entry: asyncpg.Record) -> Dict:
    keyboard = []
    if entry.get('srt_url'): keyboard.append([{'text': '📥 Download Subtitle', 'callback_data': f"download_{entry['unique_id']}"}])
    if entry.get('imdb_url'): keyboard.append([{'text': '🎬 View on IMDb', 'url': entry['imdb_url']}])
    keyboard.append([{'text': '🔙 Back', 'callback_data': 'menu_home'}, {'text': '❌ Close', 'callback_data': 'menu_close'}])
    return {'inline_keyboard': keyboard}

# --- Core Handlers ---
async def handle_callback_query(callback_data: str, message: dict, chat_id: str) -> Optional[Dict]:
    action, _, value = callback_data.partition('_')
    if action == 'menu':
        if value == 'close': return {'method': 'deleteMessage', 'chat_id': chat_id, 'message_id': message['message_id']}
        text_map = {'home': WELCOME_MESSAGE, 'about': ABOUT_MESSAGE, 'help': HELP_MESSAGE}
        if text := text_map.get(value):
            return {'method': 'editMessageText', 'text': text, 'reply_markup': create_menu_keyboard(value), 'parse_mode': 'Markdown', 'chat_id': chat_id, 'message_id': message['message_id']}
    elif action == 'view' and (entry := await db_pool.fetchrow("SELECT * FROM subtitles WHERE unique_id = $1", value)):
        return {'method': 'editMessageText', 'chat_id': chat_id, 'message_id': message['message_id'], 'text': f"**{entry['title']}**", 'reply_markup': create_detail_keyboard(entry)}
    return None

async def handle_telegram_message(message_data: dict) -> Optional[Dict]:
    user, message = None, None
    if 'callback_query' in message_data:
        cb = message_data['callback_query']
        user, message = cb['from'], cb['message']
    elif 'message' in message_data:
        message = message_data['message']
        user = message.get('from')

    if not user or not (user_id := user.get('id')): return None

    if not await check_user_membership(user_id):
        if 'callback_query' in message_data: await send_telegram_message({'method': 'answerCallbackQuery', 'callback_query_id': message_data['callback_query']['id'], 'text': "Please join our channel to use the bot.", 'show_alert': True})
        return {'chat_id': user_id, 'text': "You must join our channel to use this bot.", 'reply_markup': {'inline_keyboard': [[{'text': "Join Channel", 'url': FORCE_SUB_CHANNEL_LINK}]]}}

    await add_user(user_id)

    if 'callback_query' in message_data:
        if response := await handle_callback_query(message_data['callback_query']['data'], message, str(message['chat']['id'])):
            await send_telegram_message(response)
        return {'method': 'answerCallbackQuery', 'callback_query_id': message_data['callback_query']['id']}

    text = message.get('text', '').strip()
    if not text: return None

    if text.startswith('/'): # Commands
        command, *args = text.split()
        if command == '/start': return {'chat_id': user_id, 'text': WELCOME_MESSAGE, 'reply_markup': create_menu_keyboard('home')}
        if str(user_id) == OWNER_ID and command == '/add' and args:
            if details := scrape_page_details(args[0]):
                await upsert_subtitle(details)
                return {'chat_id': user_id, 'text': f"✅ Added/Updated **{details['title']}**."}
            return {'chat_id': user_id, 'text': "❌ Failed to scrape or add entry."}

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
app = FastAPI(title="Subtitle Search Bot API", version="3.3", redoc_url=None, docs_url=None)
@app.on_event("startup")
async def startup_event(): await init_db()
@app.on_event("shutdown")
async def shutdown_event():
    if db_pool: await db_pool.close()

@app.post("/telegram")
async def telegram_webhook(request: Request):
    if WEBHOOK_SECRET != request.headers.get("X-Telegram-Bot-Api-Secret-Token"): return Response(status_code=403)
    try:
        if response_data := await handle_telegram_message(await request.json()):
            await send_telegram_message(response_data)
        return Response(status_code=200)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return Response(status_code=500)
