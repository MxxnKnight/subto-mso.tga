import os
import json
import logging
import asyncio
import zipfile
import tempfile
import time
import aiohttp
import requests
import aiofiles
from http import HTTPStatus
from typing import Dict, Any, List
from urllib.parse import urlparse, urljoin
import re

from fastapi import FastAPI, Request, Response, HTTPException, Query

app = FastAPI(docs_url=None, redoc_url=None)

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Environment & Globals ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "a-random-string")
OWNER_ID = os.environ.get("OWNER_ID")
DB_FILE, SERIES_DB_FILE = "db.json", "series_db.json"
db: Dict[str, Any] = {}
series_db: Dict[str, Dict[int, str]] = {}

# --- Bot UI Text ---
WELCOME_MESSAGE = "🎬 **Welcome!**\nType a movie or series name to search."
HELP_MESSAGE = "Type any movie/series name to search for subtitles. Use the buttons to navigate."
ABOUT_MESSAGE = "This bot searches for Malayalam subtitles from malayalamsubtitles.org."

# --- Core Logic ---
def load_databases():
    global db, series_db
    try:
        with open(DB_FILE, 'r', encoding='utf-8') as f: db = json.load(f)
        logger.info(f"Loaded {len(db)} entries from {DB_FILE}")
    except (FileNotFoundError, json.JSONDecodeError): db = {}
    try:
        with open(SERIES_DB_FILE, 'r', encoding='utf-8') as f: series_db = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError): series_db = {}

def search_content(query: str) -> List[Dict]:
    if not db or not query: return []
    query_lower = query.lower().strip()
    results = [{'unique_id': uid, 'entry': e} for uid, e in db.items() if query_lower in e.get('title', '').lower()]
    results.sort(key=lambda x: x['entry'].get('year', '0'), reverse=True)
    return results[:25]

def get_series_seasons(series_name: str) -> Dict[int, str]:
    if not series_name or not series_db: return {}
    for db_series_name, seasons in series_db.items():
        if db_series_name.lower().strip() == series_name.lower().strip():
            return seasons
    return {}

# --- Telegram API Communication ---
async def send_telegram_message(payload: Dict):
    method = payload.pop('method', 'sendMessage')
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as response:
                if response.status != 200:
                    logger.error(f"Telegram API error for {method}: {response.status} - {await response.text()}")
                return await response.json()
    except Exception as e:
        logger.exception("Error sending message to Telegram")

# --- Bot Actions ---
async def download_and_upload_subtitle(download_url: str, chat_id: int, title: str, source_url: str):
    status_message = await send_telegram_message({'method': 'sendMessage', 'chat_id': chat_id, 'text': f"📥 Downloading..."})
    status_message_id = status_message['result']['message_id'] if status_message and status_message.get('ok') else None

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://malayalamsubtitles.org/'}
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(download_url, timeout=60) as resp:
                    if resp.status != 200:
                        if status_message_id: await send_telegram_message({'method': 'editMessageText', 'chat_id': chat_id, 'message_id': status_message_id, 'text': f"❌ Download failed."})
                        return

                    filename = title.replace(' ', '_') + ".zip"
                    if 'content-disposition' in resp.headers:
                        cd_match = re.search(r'filename\*?=(.+)', resp.headers['content-disposition'], re.IGNORECASE)
                        if cd_match: filename = cd_match.group(1).strip('"').split("''")[-1]
                    
                    file_path = os.path.join(temp_dir, filename)
                    async with aiofiles.open(file_path, 'wb') as f: await f.write(await resp.read())
                    
                    if status_message_id: await send_telegram_message({'method': 'editMessageText', 'chat_id': chat_id, 'message_id': status_message_id, 'text': "📤 Uploading..."})
                    
                    if filename.lower().endswith('.zip'):
                        await upload_zip_contents(file_path, chat_id, source_url)
                    else:
                        await upload_single_file(file_path, chat_id, filename, source_url)
    finally:
        if status_message_id:
            await asyncio.sleep(3)
            await send_telegram_message({'method': 'deleteMessage', 'chat_id': chat_id, 'message_id': status_message_id})

async def upload_zip_contents(zip_path: str, chat_id: int, source_url: str):
    with tempfile.TemporaryDirectory() as extract_dir, zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(extract_dir)
        for root, _, files in os.walk(extract_dir):
            for file in files:
                if file.lower().endswith(('.srt', '.ass', '.ssa', '.vtt')):
                    await upload_single_file(os.path.join(root, file), chat_id, file, source_url)
                    await asyncio.sleep(1)

async def upload_single_file(file_path: str, chat_id: int, filename: str, source_url: str):
    url = f"https://api.telegram.org/bot{TOKEN}/sendDocument"
    with open(file_path, 'rb') as f:
        data = aiohttp.FormData()
        data.add_field('chat_id', str(chat_id))
        data.add_field('document', f, filename=filename)
        data.add_field('caption', f"[{filename}]({source_url})" if source_url else filename, {'parse_mode': 'Markdown'})
        async with aiohttp.ClientSession() as session: await session.post(url, data=data)

# --- UI Generation ---
def create_menu_keyboard(context: str) -> Dict:
    if context == 'home':
        return {
            'keyboard': [[{'text': '🔎 Search Subtitles'}], [{'text': 'ℹ️ About'}, {'text': '❓ Help'}]],
            'resize_keyboard': True,
            'one_time_keyboard': False
        }
    return {}

def create_search_results_keyboard(results: List[Dict]) -> Dict:
    keyboard = []
    for res in results:
        entry = res['entry']
        year = f" ({entry['year']})" if entry.get('year') else ""
        button_text = f"{entry.get('title', 'Unknown')}{year}"
        keyboard.append([{'text': button_text, 'callback_data': f"v_{res['unique_id']}"}])
    return {'inline_keyboard': keyboard}

def create_detail_keyboard(unique_id: str) -> Dict:
    entry = db.get(unique_id, {})
    keyboard = []
    if entry.get('srtURL'): keyboard.append([{'text': '📥 Download Subtitle', 'callback_data': f"dl_{unique_id}"}])
    if entry.get('imdbURL'): keyboard.append([{'text': '🎬 View on IMDb', 'url': entry['imdbURL']}])
    keyboard.append([{'text': '❌ Close', 'callback_data': 'menu_close'}])
    return {'inline_keyboard': keyboard}

def format_movie_details(entry: Dict) -> str:
    message = f"🎬 **{entry.get('title', 'Unknown')}** ({entry.get('year', 'N/A')})\n\n"
    def format_field(data, prefix):
        if data and data.get('name'): return f"{prefix} [{data['name']}]({data['url']})" if data.get('url') else f"{prefix} {data['name']}"
        return ""
    details = [s for s in [
        format_field(entry.get('director'), "🎬 Director:"),
        format_field(entry.get('genre'), "🎭 Genre:"),
        format_field(entry.get('language'), "🗣️ Language:"),
        format_field(entry.get('translatedBy'), "🌐 Translator:"),
        format_field(entry.get('poster_maker'), "🎨 Poster by:")
    ] if s]
    if details: message += "\n".join(details) + "\n\n"
    if entry.get('descriptionMalayalam'): message += f"📖 **Synopsis:**\n{entry['descriptionMalayalam']}\n\n"
    if entry.get('source_url'): message += f"🔗 [Go to Subtitle Page]({entry['source_url']})"
    return message.strip()

# --- Webhook Handlers ---
async def handle_callback_query(query: dict) -> Dict:
    callback_data, message = query['data'], query.get('message', {})
    chat_id, message_id = message.get('chat', {}).get('id'), message.get('message_id')
    payload = {'chat_id': chat_id, 'message_id': message_id}

    if callback_data == 'menu_close':
        payload['method'] = 'deleteMessage'
    elif callback_data.startswith('v_'):
        unique_id = callback_data.replace('v_', '')
        if unique_id in db:
            entry = db[unique_id]
            payload.update({'method': 'editMessageText', 'text': format_movie_details(entry), 'reply_markup': create_detail_keyboard(unique_id), 'parse_mode': 'Markdown', 'disable_web_page_preview': True})
    elif callback_data.startswith('dl_'):
        unique_id = callback_data.replace('dl_', '')
        if unique_id in db:
            entry = db[unique_id]
            if entry.get('srtURL'):
                asyncio.create_task(download_and_upload_subtitle(entry['srtURL'], chat_id, entry['title'], entry.get('source_url', '')))
                return {'method': 'answerCallbackQuery', 'callback_query_id': query['id'], 'text': 'Download started!'}
    return payload

async def handle_message_text(message: dict) -> Dict:
    text, chat_id = message['text'].strip(), message['chat']['id']

    # --- Command Handling ---
    if text == '/start':
        return {'chat_id': chat_id, 'text': WELCOME_MESSAGE, 'reply_markup': create_menu_keyboard('home'), 'parse_mode': 'Markdown'}
    if text == '/help' or text == '❓ Help':
        return {'chat_id': chat_id, 'text': HELP_MESSAGE, 'parse_mode': 'Markdown'}
    if text == '/about' or text == 'ℹ️ About':
        return {'chat_id': chat_id, 'text': ABOUT_MESSAGE, 'parse_mode': 'Markdown'}
    if text == '🔎 Search Subtitles':
        return {'chat_id': chat_id, 'text': "Okay, send me the name of the movie or series you're looking for.", 'parse_mode': 'Markdown'}

    # --- Search Handling ---
    results = search_content(text)
    if not results:
        return {'chat_id': chat_id, 'text': f"🤷‍♀️ No subtitles found for **{text}**.", 'parse_mode': 'Markdown'}

    return {'chat_id': chat_id, 'text': f"🔎 Found {len(results)} results for **{text}**:", 'reply_markup': create_search_results_keyboard(results), 'parse_mode': 'Markdown'}

# --- FastAPI Routes & Events ---
@app.on_event("startup")
async def startup_event():
    load_databases()
    webhook_url = os.environ.get("RENDER_EXTERNAL_URL")
    if webhook_url and TOKEN:
        await send_telegram_message({'method': 'setWebhook', 'url': f"{webhook_url}/telegram", 'secret_token': WEBHOOK_SECRET})
        if OWNER_ID: await send_telegram_message({'chat_id': OWNER_ID, 'text': '✅ Bot is up and running!'})

@app.post("/telegram")
async def telegram_webhook(request: Request):
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        return Response(status_code=HTTPStatus.FORBIDDEN)
    try:
        data = await request.json()
        payload = None
        if 'callback_query' in data:
            payload = await handle_callback_query(data['callback_query'])
        elif 'message' in data and 'text' in data['message']:
            payload = await handle_message_text(data['message'])
        if payload: await send_telegram_message(payload)
    except Exception: logger.exception("Error processing webhook")
    return Response(status_code=HTTPStatus.OK)

@app.get("/", include_in_schema=False)
def root():
    return {"status": "ok"}

@app.get("/healthz", include_in_schema=False)
def health_check(): return {"status": "ok"}
