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
WELCOME_MESSAGE = """
🎬 **Welcome to Malayalam Subtitle Search Bot!**

Your one-stop destination for high-quality Malayalam subtitles for movies and TV shows.

🎯 **What can I do?**
• Search for Malayalam subtitles
• Download subtitle files instantly
• Browse by movies or series
• Get detailed movie information

Just type any movie or series name to get started!
"""

ABOUT_MESSAGE = """
ℹ️ **About This Bot**

**Hosted on:** Render.com
**Framework:** FastAPI + Custom Telegram Bot API
**Database:** malayalamsubtitles.org
**Developer:** Custom Malayalam Subtitle Bot
**Version:** 2.0 Enhanced

**Features:**
✅ Real-time subtitle search
✅ Instant file downloads
✅ Series season management
✅ Comprehensive movie details
✅ Admin controls

**Data Source:** malayalamsubtitles.org (scraped with permission)
"""

HELP_MESSAGE = """
🆘 **How to Use This Bot**

**🔍 Searching:**
• Type any movie/series name
• Use English names for better results
• Add year for specific versions (e.g., "Dune 2021")

**📺 Series:**
• Search series name to see all seasons
• Click season buttons to view episodes
• Each season has separate download links

**🎬 Movies:**
• Direct search shows movie details
• One-click download available
• View IMDb ratings and details

**💡 Tips:**
• Try different name variations
• Check spelling for better results
• Use /stats to see database size

**⚠️ Note:** This bot provides subtitle files only, not movie content.
"""

TOS_MESSAGE = """
📋 **Terms of Service**

**By using this bot, you agree to:**

1. **Legal Use Only**
   • Use subtitles for legally owned content only
   • Respect copyright laws in your jurisdiction

2. **Data Source**
   • Content scraped from malayalamsubtitles.org
   • Bot operates under fair use principles
   • No copyright infringement intended

3. **Limitations**
   • Service provided "as-is" without warranties
   • Uptime not guaranteed
   • Database updated periodically

4. **Prohibited Actions**
   • No spam or abuse of bot services
   • No commercial redistribution of content
   • No automated scraping of this bot

5. **Privacy**
   • We don't store personal messages
   • Search queries logged for improvement
   • No data shared with third parties

**Contact:** Message the bot admin for issues.

By continuing to use this bot, you accept these terms.
"""

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
    status_message = await send_telegram_message({'method': 'sendMessage', 'chat_id': chat_id, 'text': "⏳ Preparing to download..."})
    status_message_id = status_message.get('result', {}).get('message_id') if status_message.get('ok') else None

    if not status_message_id:
        logger.error("Failed to send initial status message.")
        return

    error_occurred = False
    try:
        await send_telegram_message({'method': 'editMessageText', 'chat_id': chat_id, 'message_id': status_message_id, 'text': "📥 Downloading..."})

        with tempfile.TemporaryDirectory() as temp_dir:
            headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://malayalamsubtitles.org/'}
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(download_url, timeout=60) as resp:
                    resp.raise_for_status()
                    content = await resp.read()

                    filename = title.replace(' ', '_') + ".zip"
                    if 'content-disposition' in resp.headers:
                        cd_match = re.search(r'filename\*?=(.+)', resp.headers['content-disposition'], re.IGNORECASE)
                        if cd_match: filename = cd_match.group(1).strip('"').split("''")[-1]
                    
                    file_path = os.path.join(temp_dir, filename)
                    async with aiofiles.open(file_path, 'wb') as f:
                        await f.write(content)
                    
                    await send_telegram_message({'method': 'editMessageText', 'chat_id': chat_id, 'message_id': status_message_id, 'text': "📤 Uploading..."})
                    
                    if filename.lower().endswith('.zip'):
                        await upload_zip_contents(file_path, chat_id, source_url)
                    else:
                        await upload_single_file(file_path, chat_id, filename, source_url)

    except aiohttp.ClientError as e:
        error_occurred = True
        logger.exception(f"HTTP error during subtitle download: {e}")
        await send_telegram_message({'method': 'editMessageText', 'chat_id': chat_id, 'message_id': status_message_id, 'text': f"❌ Download failed due to a network error."})
    except Exception as e:
        error_occurred = True
        logger.exception(f"An unexpected error occurred during subtitle download: {e}")
        await send_telegram_message({'method': 'editMessageText', 'chat_id': chat_id, 'message_id': status_message_id, 'text': f"❌ An unexpected error occurred. Please try again later."})
    finally:
        if status_message_id:
            if error_occurred:
                await asyncio.sleep(5) # Give user time to read the error
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
def create_main_menu_keyboard() -> Dict:
    return {
        'inline_keyboard': [
            [{'text': 'ℹ️ About', 'callback_data': 'menu_about'}, {'text': '🆘 Help', 'callback_data': 'menu_help'}],
            [{'text': '📋 Terms of Service', 'callback_data': 'menu_tos'}]
        ]
    }

def create_back_button_keyboard() -> Dict:
    return {'inline_keyboard': [[{'text': '⬅️ Back to Main Menu', 'callback_data': 'menu_home'}]]}

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
        if data and data.get('name'):
            return f"{prefix} [{data['name']}]({data['url']})" if data.get('url') else f"{prefix} {data['name']}"
        return ""

    details_list = [s for s in [
        format_field(entry.get('director'), "🎬 Director:"),
        format_field(entry.get('genre'), "🎭 Genre:"),
        format_field(entry.get('imdb_rating'), "⭐ IMDb Rating:"),
        format_field(entry.get('certification'), "🛡️ Certification:"),
        format_field(entry.get('language'), "🗣️ Language:"),
        format_field(entry.get('msone_release'), "릴 MS-Release:"),
        format_field(entry.get('translatedBy'), "🌐 Translator:"),
        format_field(entry.get('poster_maker'), "🎨 Poster by:")
    ] if s]
    if details_list:
        message += "\n".join(details_list) + "\n\n"

    if entry.get('descriptionMalayalam'):
        message += f"📖 **Synopsis:**\n{entry['descriptionMalayalam']}\n\n"

    if entry.get('source_url'):
        message += f"🔗 [Go to Subtitle Page]({entry['source_url']})"

    return message.strip()

# --- Webhook Handlers ---
async def handle_callback_query(query: dict) -> Dict:
    callback_data, message = query['data'], query.get('message', {})
    chat_id, message_id = message.get('chat', {}).get('id'), message.get('message_id')

    payload = {'chat_id': chat_id, 'message_id': message_id, 'parse_mode': 'Markdown', 'disable_web_page_preview': True}

    if callback_data == 'menu_home':
        payload.update({'method': 'editMessageText', 'text': WELCOME_MESSAGE, 'reply_markup': create_main_menu_keyboard()})
        return payload

    if callback_data == 'menu_about':
        payload.update({'method': 'editMessageText', 'text': ABOUT_MESSAGE, 'reply_markup': create_back_button_keyboard()})
        return payload

    if callback_data == 'menu_help':
        payload.update({'method': 'editMessageText', 'text': HELP_MESSAGE, 'reply_markup': create_back_button_keyboard()})
        return payload

    if callback_data == 'menu_tos':
        payload.update({'method': 'editMessageText', 'text': TOS_MESSAGE, 'reply_markup': create_back_button_keyboard()})
        return payload

    if callback_data == 'menu_close':
        payload.update({'method': 'deleteMessage'})
        return payload

    elif callback_data.startswith('v_'):
        unique_id = callback_data.replace('v_', '')

        # Answer the callback query immediately to give feedback
        await send_telegram_message({
            'method': 'answerCallbackQuery',
            'callback_query_id': query['id']
        })

        if unique_id in db:
            entry = db[unique_id]

            # Delete the old message (the search results list)
            await send_telegram_message({'method': 'deleteMessage', 'chat_id': chat_id, 'message_id': message_id})

            # Prepare the new message payload
            new_message_payload = {
                'chat_id': chat_id,
                'reply_markup': create_detail_keyboard(unique_id),
                'parse_mode': 'Markdown'
            }

            poster_url = entry.get('posterURL')
            logger.info(f"Attempting to send poster for {unique_id}. URL: {poster_url}")

            # Use sendPhoto if poster exists and is a valid HTTPS URL
            if poster_url and poster_url.startswith('https'):
                new_message_payload.update({
                    'method': 'sendPhoto',
                    'photo': poster_url,
                    'caption': format_movie_details(entry)
                })
            else:
                if poster_url:
                    logger.warning(f"Invalid or non-HTTPS posterURL for {unique_id}: {poster_url}. Falling back to sendMessage.")
                else:
                    logger.warning(f"No posterURL for {unique_id}. Falling back to sendMessage.")

                new_message_payload.update({
                    'method': 'sendMessage',
                    'text': format_movie_details(entry),
                    'disable_web_page_preview': True
                })

            await send_telegram_message(new_message_payload)

        return None # Indicate that the action is fully handled

    elif callback_data.startswith('dl_'):
        unique_id = callback_data.replace('dl_', '')
        if unique_id in db:
            entry = db[unique_id]
            if entry.get('srtURL'):
                # Start the download task in the background
                asyncio.create_task(download_and_upload_subtitle(entry['srtURL'], chat_id, entry['title'], entry.get('source_url', '')))
                # Return an answer to the callback query
                return {'method': 'answerCallbackQuery', 'callback_query_id': query['id'], 'text': 'Download started!'}

    return None # Return None by default if no specific payload is constructed

async def handle_message_text(message: dict) -> Dict:
    text, chat_id = message['text'].strip(), message['chat']['id']

    if text == '/start':
        return {
            'chat_id': chat_id,
            'text': WELCOME_MESSAGE,
            'reply_markup': create_main_menu_keyboard(),
            'parse_mode': 'Markdown',
            'disable_web_page_preview': True
        }

    # All other text is treated as a search query
    results = search_content(text)
    if not results:
        return {'chat_id': chat_id, 'text': f"🤷‍♀️ No subtitles found for **{text}**.", 'parse_mode': 'Markdown'}

    return {
        'chat_id': chat_id,
        'text': f"🔎 Found {len(results)} results for **{text}**:",
        'reply_markup': create_search_results_keyboard(results),
        'parse_mode': 'Markdown'
    }

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
