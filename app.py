import os
import json
import logging
import asyncio
import zipfile
import tempfile
import time
from http import HTTPStatus
from typing import Dict, Any, List
from urllib.parse import urlparse, urljoin
import re

from fastapi import FastAPI, Request, Response, HTTPException, Query

app = FastAPI(docs_url=None, redoc_url=None)

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
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

def load_databases():
    """Load both main and series databases."""
    global db, series_db
    
    try:
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            db = json.load(f)
            logger.info(f"Loaded main database: {len(db)} entries")
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning(f"Could not load {DB_FILE}. Starting with an empty database.")
        db = {}
    
    try:
        with open(SERIES_DB_FILE, 'r', encoding='utf-8') as f:
            series_db = json.load(f)
            logger.info(f"Loaded series database: {len(series_db)} series")
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning(f"Could not load {SERIES_DB_FILE}.")
        series_db = {}

def search_content(query: str) -> List[Dict]:
    """Enhanced search with series support."""
    if not db or not query:
        return []
    
    query_lower = query.lower().strip()
    results = []
    
    for unique_id, entry in db.items():
        title = entry.get('title', '').lower()
        series_name = entry.get('series_name', '').lower() if entry.get('series_name') else ''
        
        if (query_lower in title or
            (series_name and query_lower in series_name) or
            any(word in title for word in query_lower.split()) or
            (series_name and any(word in series_name for word in query_lower.split()))):
            
            results.append({
                'type': 'match',
                'unique_id': unique_id,
                'entry': entry,
                'relevance': calculate_relevance(query_lower, title, series_name)
            })
    
    results.sort(key=lambda x: x.get('relevance', 0), reverse=True)
    return results[:20]

def calculate_relevance(query: str, title: str, series_name: str) -> int:
    """Calculate search relevance score."""
    score = 0
    if query in title: score += 100
    if series_name and query in series_name: score += 100
    for word in query.split():
        if word in title: score += 10
        if series_name and word in series_name: score += 10
    return score

def get_series_seasons(series_name: str) -> Dict[int, str]:
    """Get all seasons for a series."""
    if not series_name or not series_db:
        return {}
    
    normalized_series_name = series_name.lower().strip()
    for db_series_name, seasons in series_db.items():
        if db_series_name.lower().strip() == normalized_series_name:
            return seasons
    return {}

async def download_and_upload_subtitle(download_url: str, chat_id: str, title: str, source_url: str) -> bool:
    """Download subtitle file and upload to Telegram."""
    import aiohttp
    import aiofiles
    
    logger.info(f"[ChatID: {chat_id}] Starting download for '{title}'")

    status_message_id = None
    try:
        status_message = await send_telegram_message({
            'method': 'sendMessage', 'chat_id': chat_id,
            'text': f"📥 Downloading subtitle for **{title}**...", 'parse_mode': 'Markdown'
        })
        if status_message and status_message.get('ok'):
            status_message_id = status_message['result']['message_id']

        with tempfile.TemporaryDirectory() as temp_dir:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Referer': 'https://malayalamsubtitles.org/'
            }
            
            async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as session:
                async with session.get(download_url) as resp:
                    if resp.status != 200:
                        if status_message_id:
                            await send_telegram_message({'method': 'editMessageText', 'chat_id': chat_id, 'message_id': status_message_id, 'text': f"❌ Failed to download (HTTP {resp.status})."})
                        return False

                    filename = f"{title.replace(' ', '_')}.zip"
                    if 'content-disposition' in resp.headers:
                        cd_match = re.search(r'filename\*?=(.+)', resp.headers['content-disposition'], re.IGNORECASE)
                        if cd_match:
                            raw_filename = cd_match.group(1).strip('"')
                            filename = raw_filename.split("''")[-1] if "''" in raw_filename else raw_filename
                    
                    file_path = os.path.join(temp_dir, filename)
                    
                    async with aiofiles.open(file_path, 'wb') as f:
                        async for chunk in resp.content.iter_chunked(8192):
                            await f.write(chunk)
                    
                    if status_message_id:
                        await send_telegram_message({'method': 'editMessageText', 'chat_id': chat_id, 'message_id': status_message_id, 'text': "📤 Uploading..."})
                    
                    if filename.lower().endswith('.zip'):
                        await upload_zip_contents(file_path, chat_id, source_url)
                    else:
                        await upload_single_file(file_path, chat_id, filename, source_url)
    except Exception as e:
        logger.exception(f"Error in download_and_upload_subtitle for chat {chat_id}")
        await send_telegram_message({'chat_id': chat_id, 'text': "❌ An unexpected error occurred during download."})
    finally:
        if status_message_id:
            await asyncio.sleep(3)
            await send_telegram_message({'method': 'deleteMessage', 'chat_id': chat_id, 'message_id': status_message_id})

async def upload_zip_contents(zip_path: str, chat_id: str, source_url: str):
    """Extracts and uploads all valid subtitle files from a zip archive."""
    try:
        with tempfile.TemporaryDirectory() as extract_dir, zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
            
            subtitle_files = [os.path.join(root, file) for root, _, files in os.walk(extract_dir) for file in files if file.lower().endswith(('.srt', '.ass', '.ssa', '.vtt'))]
            
            if not subtitle_files:
                await send_telegram_message({'chat_id': chat_id, 'text': "🤷 No subtitle files found in the archive."})
                return

            for file_path in subtitle_files:
                await upload_single_file(file_path, chat_id, os.path.basename(file_path), source_url)
                await asyncio.sleep(1)
    except Exception as e:
        logger.exception(f"Error processing zip file for chat {chat_id}")
        await send_telegram_message({'chat_id': chat_id, 'text': "❌ Error processing zip file."})

async def upload_single_file(file_path: str, chat_id: str, filename: str, source_url: str):
    """Uploads a single file to Telegram."""
    import aiohttp
    if not os.path.exists(file_path) or os.path.getsize(file_path) == 0: return
    
    url = f"https://api.telegram.org/bot{TOKEN}/sendDocument"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
            with open(file_path, 'rb') as f:
                data = aiohttp.FormData()
                data.add_field('chat_id', str(chat_id))
                data.add_field('document', f, filename=filename)
                caption = f"[{filename}]({source_url})" if source_url else filename
                data.add_field('caption', caption)
                data.add_field('parse_mode', 'Markdown')

                await session.post(url, data=data)
    except Exception as e:
        logger.exception(f"Error uploading file for chat {chat_id}")

def create_menu_keyboard(current_menu: str) -> Dict:
    keyboards = {'home': [[{'text': 'ℹ️ About', 'callback_data': 'menu_about'}, {'text': '🆘 Help', 'callback_data': 'menu_help'}], [{'text': '📋 Terms of Service', 'callback_data': 'menu_tos'}, {'text': '❌ Close', 'callback_data': 'menu_close'}]]}
    return {'inline_keyboard': keyboards.get(current_menu, keyboards['home'])}

def create_search_results_keyboard(results: List[Dict]) -> Dict:
    keyboard = []
    for result in results[:10]:
        entry = result['entry']
        title = entry.get('title', 'Unknown')[:45]
        if entry.get('is_series'):
            title += f" (S{entry.get('season_number', 1)})"
        keyboard.append([{'text': title, 'callback_data': f"v_{result['unique_id']}"}])
    keyboard.append([{'text': '❌ Close', 'callback_data': 'menu_close'}])
    return {'inline_keyboard': keyboard}

def format_movie_details(entry: Dict) -> str:
    """Formats movie/series details with hyperlinks."""
    title = entry.get('title', 'Unknown Title')
    year = f" ({entry['year']})" if entry.get('year') else ""
    message = f"🎬 **{title}{year}**\n\n"
    if entry.get('msone_release_number'):
        message += f"🆔 MSOne Release: `{entry['msone_release_number']}`\n\n"
    
    def format_field(data, prefix):
        if not data or not data.get('name') or data['name'] == 'Unknown': return None
        return f"{prefix} [{data['name']}]({data['url']})" if data.get('url') and data['url'].startswith('http') else f"{prefix} {data['name']}"

    details = [s for s in [
        format_field(entry.get('language'), "🗣️ **Language:**"),
        format_field(entry.get('director'), "🎬 **Director:**"),
        format_field(entry.get('genre'), "🎭 **Genre:**"),
        f"⭐ **IMDb Rating:** {entry['imdb_rating']}" if entry.get('imdb_rating') and entry['imdb_rating'] != 'N/A' else None,
        f"🏷️ **Certification:** {entry['certification']}" if entry.get('certification') and entry['certification'] != 'Not Rated' else None,
        format_field(entry.get('translatedBy'), "🌐 **Translator:**"),
        format_field(entry.get('poster_maker'), "🎨 **Poster by:**")
    ] if s]
    
    if details: message += "\n".join(details) + "\n\n"
    
    if entry.get('is_series'):
        message += f"📺 **Series Information:**\n"
        if entry.get('season_number'): message += f"• Season: {entry['season_number']}\n"
        if entry.get('total_seasons'): message += f"• Total Seasons Available: {entry['total_seasons']}\n"
        message += "\n"
    
    synopsis = entry.get('descriptionMalayalam')
    if synopsis and synopsis != 'No description available':
        message += f"📖 **Synopsis:**\n{synopsis}\n\n"
    
    if entry.get('source_url'):
        message += f"🔗 [Go to Subtitle Page]({entry['source_url']})"

    return message.strip()

def create_detail_keyboard(entry: Dict, unique_id: str) -> Dict:
    keyboard = []
    if entry.get('srtURL'):
        keyboard.append([{'text': '📥 Download Subtitle', 'callback_data': f"dl_{unique_id}"}])
    if entry.get('imdbURL'):
        keyboard.append([{'text': '🎬 View on IMDb', 'url': entry['imdbURL']}])
    keyboard.append([{'text': '❌ Close', 'callback_data': 'menu_close'}])
    return {'inline_keyboard': keyboard}

async def handle_callback_query(callback_data: str, message: dict) -> Dict:
    """Handles all callback queries from inline keyboards."""
    chat_id, message_id = message['chat']['id'], message['message_id']
    method = 'editMessageText' # Default method
    payload = {'chat_id': chat_id, 'message_id': message_id}

    if callback_data.startswith('menu_'):
        action = callback_data.split('_')[1]
        if action == 'close':
            payload['method'] = 'deleteMessage'
        else: # home, about, help, tos
            payload.update({
                'text': {'home': WELCOME_MESSAGE, 'about': ABOUT_MESSAGE, 'help': HELP_MESSAGE, 'tos': TOS_MESSAGE}[action],
                'reply_markup': create_menu_keyboard(action), 'parse_mode': 'Markdown'
            })
    elif callback_data.startswith('v_'):
        unique_id = callback_data.replace('v_', '')
        if unique_id in db:
            entry = db[unique_id]
            payload.update({
                'text': format_movie_details(entry),
                'reply_markup': create_detail_keyboard(entry, unique_id),
                'parse_mode': 'Markdown', 'disable_web_page_preview': True
            })
    elif callback_data.startswith('dl_'):
        unique_id = callback_data.replace('dl_', '')
        if unique_id in db:
            entry = db[unique_id]
            if entry.get('srtURL'):
                asyncio.create_task(download_and_upload_subtitle(entry['srtURL'], chat_id, entry['title'], entry['source_url']))
                return {'method': 'answerCallbackQuery', 'callback_query_id': message['id'], 'text': 'Download started!'}
        return {'method': 'answerCallbackQuery', 'callback_query_id': message['id'], 'text': 'Download link not available.'}

    return payload

async def handle_message_text(message: dict) -> Dict:
    """Handles incoming text messages (commands and searches)."""
    text, chat_id, user_id = message['text'].strip(), message['chat']['id'], message['from']['id']
    payload = {'chat_id': chat_id, 'parse_mode': 'Markdown'}

    if text.startswith('/'):
        command = text.split(' ')[0].lower()
        if command == '/start':
            payload.update({'text': WELCOME_MESSAGE, 'reply_markup': create_menu_keyboard('home')})
        elif command == '/help':
            payload.update({'text': HELP_MESSAGE, 'reply_markup': create_menu_keyboard('help')})
        elif command == '/about':
            payload.update({'text': ABOUT_MESSAGE, 'reply_markup': create_menu_keyboard('about')})
        else:
            payload['text'] = "🤔 Unrecognized command."
    else:
        results = search_content(text)
        if not results:
            payload['text'] = f"🤷‍♀️ No subtitles found for **{text}**."
        else:
            payload.update({
                'text': f"🔎 Found {len(results)} results for **{text}**:",
                'reply_markup': create_search_results_keyboard(results)
            })
    return payload

async def telegram_webhook_handler(request: Request):
    """Main webhook endpoint to receive and handle all updates from Telegram."""
    try:
        data = await request.json()
        response_payload = None
        if 'callback_query' in data:
            response_payload = await handle_callback_query(data['callback_query']['data'], data['callback_query'])
        elif 'message' in data and 'text' in data['message']:
            response_payload = await handle_message_text(data['message'])
        
        if response_payload:
            await send_telegram_message(response_payload)
            
    except Exception as e:
        logger.exception("Error processing webhook")

    return Response(status_code=HTTPStatus.OK)

# --- Telegram API Communication ---
async def send_telegram_message(payload: Dict):
    """Sends a message to the Telegram API."""
    import aiohttp
    method = payload.pop('method', 'sendMessage')
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Telegram API error for method {method}: {response.status} - {error_text}")
    except Exception as e:
        logger.error(f"Error sending message to Telegram: {e}")

# --- FastAPI Application Events & Routes ---
@app.on_event("startup")
async def startup_event():
    """On startup, load DB, set webhook, and notify owner."""
    load_databases()
    webhook_url = os.environ.get("RENDER_EXTERNAL_URL")
    if webhook_url and TOKEN:
        webhook_url_path = f"{webhook_url}/telegram"
        payload = {'url': webhook_url_path, 'secret_token': WEBHOOK_SECRET}
        logger.info(f"Setting webhook to: {webhook_url_path}")
        async with aiohttp.ClientSession() as session:
            await session.post(f"https://api.telegram.org/bot{TOKEN}/setWebhook", data=payload)
        if OWNER_ID:
            await send_telegram_message({'chat_id': OWNER_ID, 'text': '✅ Bot is up and running!'})

@app.get("/", include_in_schema=False)
def read_root():
    return {"status": "ok", "message": "Subtitle Search Bot is running"}

@app.get("/healthz", include_in_schema=False)
def health_check():
    return {"status": "ok"}

app.add_api_route("/telegram", telegram_webhook_handler, methods=["POST"], include_in_schema=False)

@app.get("/api/subtitles")
def api_search(q: str = Query(..., min_length=1)):
    """REST API endpoint for searching subtitles."""
    results = search_content(q)
    return {"query": q, "results": results}
