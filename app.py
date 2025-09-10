import os
import json
import logging
import asyncio
import zipfile
import tempfile
import time
import aiohttp
import requests
from http import HTTPStatus
from typing import Dict, Any, List
from urllib.parse import urlparse, urljoin
import re

from fastapi import FastAPI, Request, Response, HTTPException, Query

app = FastAPI(docs_url=None, redoc_url=None)

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Environment Variables ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "a-random-string")
OWNER_ID = os.environ.get("OWNER_ID")
DB_FILE = "db.json"
SERIES_DB_FILE = "series_db.json"

# --- Global Variables ---
db: Dict[str, Any] = {}
series_db: Dict[str, Dict[int, str]] = {}

# --- Menu Messages ---
WELCOME_MESSAGE = """
🎬 **Welcome to Malayalam Subtitle Search Bot!**
Your one-stop destination for high-quality Malayalam subtitles for movies and TV shows.
Just type any movie or series name to get started!
"""
HELP_MESSAGE = "Type any movie or series name to search for subtitles. Use the buttons to navigate."
ABOUT_MESSAGE = "This bot is developed to search for Malayalam subtitles from malayalamsubtitles.org."

def load_databases():
    """Load both main and series databases from JSON files."""
    global db, series_db
    try:
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            db = json.load(f)
        logger.info(f"Loaded {len(db)} entries from {DB_FILE}")
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning(f"Could not load {DB_FILE}. Starting with an empty database.")
        db = {}
    
    try:
        with open(SERIES_DB_FILE, 'r', encoding='utf-8') as f:
            series_db = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        series_db = {}

def search_content(query: str) -> List[Dict]:
    """Search for content in the database."""
    if not db or not query: return []
    query_lower = query.lower().strip()
    results = [
        {'unique_id': uid, 'entry': entry}
        for uid, entry in db.items()
        if query_lower in entry.get('title', '').lower()
    ]
    results.sort(key=lambda x: x['entry'].get('year', '0'), reverse=True)
    return results[:20]

async def download_and_upload_subtitle(download_url: str, chat_id: int, title: str, source_url: str):
    """Downloads, processes, and uploads subtitle files."""
    status_message = await send_telegram_message({'method': 'sendMessage', 'chat_id': chat_id, 'text': f"📥 Downloading..."})
    status_message_id = status_message['result']['message_id'] if status_message and status_message.get('ok') else None

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://malayalamsubtitles.org/'}
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(download_url, timeout=60) as resp:
                    if resp.status != 200:
                        await send_telegram_message({'method': 'editMessageText', 'chat_id': chat_id, 'message_id': status_message_id, 'text': f"❌ Download failed (HTTP {resp.status})."})
                        return

                    filename = title.replace(' ', '_') + ".zip"
                    if 'content-disposition' in resp.headers:
                        cd_match = re.search(r'filename\*?=(.+)', resp.headers['content-disposition'], re.IGNORECASE)
                        if cd_match:
                            raw_fn = cd_match.group(1).strip('"')
                            filename = raw_fn.split("''")[-1] if "''" in raw_fn else raw_fn
                    
                    file_path = os.path.join(temp_dir, filename)
                    async with aiofiles.open(file_path, 'wb') as f:
                        await f.write(await resp.read())
                    
                    if status_message_id:
                        await send_telegram_message({'method': 'editMessageText', 'chat_id': chat_id, 'message_id': status_message_id, 'text': "📤 Uploading..."})
                    
                    if filename.lower().endswith('.zip'):
                        await upload_zip_contents(file_path, chat_id, source_url)
                    else:
                        await upload_single_file(file_path, chat_id, filename, source_url)
    finally:
        if status_message_id:
            await asyncio.sleep(3)
            await send_telegram_message({'method': 'deleteMessage', 'chat_id': chat_id, 'message_id': status_message_id})

async def upload_zip_contents(zip_path: str, chat_id: int, source_url: str):
    """Extracts and uploads subtitle files from a zip archive."""
    with tempfile.TemporaryDirectory() as extract_dir, zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(extract_dir)
        subtitle_files = [os.path.join(root, file) for root, _, files in os.walk(extract_dir) if file.lower().endswith(('.srt', '.ass', '.ssa', '.vtt'))]
        for file_path in subtitle_files:
            await upload_single_file(file_path, chat_id, os.path.basename(file_path), source_url)
            await asyncio.sleep(1)

async def upload_single_file(file_path: str, chat_id: int, filename: str, source_url: str):
    """Uploads a single file to Telegram."""
    if not os.path.exists(file_path) or os.path.getsize() == 0: return
    url = f"https://api.telegram.org/bot{TOKEN}/sendDocument"
    with open(file_path, 'rb') as f:
        data = aiohttp.FormData()
        data.add_field('chat_id', str(chat_id))
        data.add_field('document', f, filename=filename)
        caption = f"[{filename}]({source_url})" if source_url else filename
        data.add_field('caption', caption, {'parse_mode': 'Markdown'})
        async with aiohttp.ClientSession() as session:
            await session.post(url, data=data)

def create_search_results_keyboard(results: List[Dict]) -> Dict:
    keyboard = [[{'text': f"{r['entry'].get('title', 'Unknown')} ({r['entry'].get('year', 'N/A')})", 'callback_data': f"v_{r['unique_id']}"}] for r in results[:15]]
    keyboard.append([{'text': '❌ Close', 'callback_data': 'menu_close'}])
    return {'inline_keyboard': keyboard}

def format_movie_details(entry: Dict) -> str:
    """Formats movie/series details with hyperlinks."""
    message = f"🎬 **{entry.get('title', 'Unknown')}** ({entry.get('year', 'N/A')})\n\n"
    
    def format_field(data, prefix):
        if data and data.get('name'):
            return f"{prefix} [{data['name']}]({data['url']})" if data.get('url') else f"{prefix} {data['name']}"
        return ""

    details = [s for s in [
        format_field(entry.get('director'), "🎬 Director:"),
        format_field(entry.get('genre'), "🎭 Genre:"),
        format_field(entry.get('language'), "🗣️ Language:"),
        format_field(entry.get('translatedBy'), "🌐 Translator:"),
    ] if s]
    
    if details: message += "\n".join(details) + "\n\n"
    if entry.get('descriptionMalayalam'): message += f"📖 **Synopsis:**\n{entry['descriptionMalayalam']}\n\n"
    if entry.get('source_url'): message += f"🔗 [Go to Subtitle Page]({entry['source_url']})"
    return message.strip()

def create_detail_keyboard(entry: Dict, unique_id: str) -> Dict:
    keyboard = []
    if entry.get('srtURL'): keyboard.append([{'text': '📥 Download Subtitle', 'callback_data': f"dl_{unique_id}"}])
    if entry.get('imdbURL'): keyboard.append([{'text': '🎬 View on IMDb', 'url': entry['imdbURL']}])
    keyboard.append([{'text': '❌ Close', 'callback_data': 'menu_close'}])
    return {'inline_keyboard': keyboard}

async def handle_callback_query(query: dict) -> Dict:
    """Handles all callback queries from inline keyboards."""
    callback_data, message = query['data'], query.get('message', {})
    chat_id, message_id = message.get('chat', {}).get('id'), message.get('message_id')
    payload = {'chat_id': chat_id, 'message_id': message_id}

    if callback_data == 'menu_close':
        payload['method'] = 'deleteMessage'
    elif callback_data.startswith('v_'):
        unique_id = callback_data.replace('v_', '')
        if unique_id in db:
            entry = db[unique_id]
            payload.update({'method': 'editMessageText', 'text': format_movie_details(entry), 'reply_markup': create_detail_keyboard(entry, unique_id), 'parse_mode': 'Markdown', 'disable_web_page_preview': True})
    elif callback_data.startswith('dl_'):
        unique_id = callback_data.replace('dl_', '')
        if unique_id in db:
            entry = db[unique_id]
            if entry.get('srtURL'):
                asyncio.create_task(download_and_upload_subtitle(entry['srtURL'], chat_id, entry['title'], entry.get('source_url', '')))
                return {'method': 'answerCallbackQuery', 'callback_query_id': query['id'], 'text': 'Download started!'}
        return {'method': 'answerCallbackQuery', 'callback_query_id': query['id'], 'text': 'Download link not available.'}

    return payload

async def handle_message_text(message: dict) -> Dict:
    """Handles incoming text messages (commands and searches)."""
    text, chat_id = message['text'].strip(), message['chat']['id']
    payload = {'chat_id': chat_id, 'parse_mode': 'Markdown'}

    if text == '/start':
        payload.update({'text': WELCOME_MESSAGE, 'reply_markup': create_menu_keyboard('home')})
    else:
        results = search_content(text)
        if not results:
            payload['text'] = f"🤷‍♀️ No subtitles found for **{text}**."
        else:
            payload.update({'text': f"🔎 Found {len(results)} results for **{text}**:", 'reply_markup': create_search_results_keyboard(results)})
    return payload

async def send_telegram_message(payload: Dict):
    """Sends a message to the Telegram API."""
    method = payload.pop('method', 'sendMessage')
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(url, json=payload)
    except Exception as e:
        logger.error(f"Error sending message to Telegram: {e}")

@app.on_event("startup")
async def startup_event():
    """On startup, load DB and set webhook."""
    load_databases()
    webhook_url = os.environ.get("RENDER_EXTERNAL_URL")
    if webhook_url and TOKEN:
        webhook_url_path = f"{webhook_url}/telegram"
        await send_telegram_message({'method': 'setWebhook', 'url': webhook_url_path, 'secret_token': WEBHOOK_SECRET})
        if OWNER_ID:
            await send_telegram_message({'chat_id': OWNER_ID, 'text': '✅ Bot is up and running!'})

@app.post("/telegram")
async def telegram_webhook(request: Request):
    """Main webhook endpoint for Telegram."""
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        return Response(status_code=HTTPStatus.FORBIDDEN)

    try:
        data = await request.json()
        payload = None
        if 'callback_query' in data:
            payload = await handle_callback_query(data['callback_query'])
        elif 'message' in data and 'text' in data['message']:
            payload = await handle_message_text(data['message'])

        if payload:
            await send_telegram_message(payload)
    except Exception as e:
        logger.exception("Error processing webhook")

    return Response(status_code=HTTPStatus.OK)
