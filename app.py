import os
import json
import logging
import asyncio
import zipfile
import tempfile
import io
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
admin_tasks: Dict[str, str] = {} # To track next action for owner

# --- Menu Messages ---
WELCOME_MESSAGE = "**🎬 Welcome to Malayalam Subtitle Search Bot!**\n\nYour one-stop destination for high-quality Malayalam subtitles for movies and TV shows."
ABOUT_MESSAGE = "**ℹ️ About This Bot**\n\n**🌐 Technical Details:**\n- **Hosted on:** Render.com\n- **Framework:** FastAPI\n- **Database:** PostgreSQL\n- **Developer:** [@Mxxn_Knight](tg://resolve?domain=Mxxn_Knight)\n- **Version:** 3.3"
HELP_MESSAGE = "**❓ How to Use This Bot**\n\n**🔍 Searching:**\n• Type any movie/series name\n• Use English names for better results\n• Add year for specific versions (e.g., \"Dune 2021\")"
AHELP_MESSAGE = """
**Admin Commands**

- `/stats`: Get statistics about the bot's usage and data.
- `/add <url>`: Manually add or update a subtitle from a malayalamsubtitles.org URL.
- `/broadcast`: Reply to a message with this command to broadcast it to all users.
- `/ahelp`: Show this help message.
"""

# --- Self-Contained Scraper Logic (from scraper.py) ---
def _get_soup(url: str) -> Optional[BeautifulSoup]:
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        return BeautifulSoup(response.text, 'html.parser')
    except requests.RequestException as e:
        logger.error(f"Scraper failed to fetch {url}: {e}")
        return None

def _clean_text(text: str) -> str: return re.sub(r'\s+', ' ', text.strip()) if text else ""
def _extract_imdb_id(url: str) -> Optional[str]: return match.group(0) if (match := re.search(r'tt\d+', url or "")) else None
def _extract_year(title: str) -> Optional[str]: return match.group(1) if (match := re.search(r'\((\d{4})\)', title)) else None

def _extract_season_info(title: str) -> Dict[str, Any]:
    patterns = [r'Season\s*(\d+)', r'സീസൺ\s*(\d+)', r'S0?(\d+)', r'സീസണ്‍\s*(\d+)']
    is_series = False
    season_number = None
    for pattern in patterns:
        match = re.search(pattern, title, re.IGNORECASE)
        if match:
            season_number = int(match.group(1))
            is_series = True
            break
    if not is_series and any(keyword in title.lower() for keyword in ['season', 'series', 'സീസൺ', 'സീസണ്‍']):
        is_series = True
        season_number = 1
    if not is_series:
        return {'is_series': False, 'season_number': None, 'series_name': None}
    series_name = re.split(r'\s+Season\s+\d|\s+സീസൺ\s+\d', title, 1, re.IGNORECASE)[0].strip()
    return {'is_series': True, 'season_number': season_number, 'series_name': series_name}

def scrape_page_details(url: str) -> Optional[Dict]:
    """Scrapes comprehensive details from a movie/series page."""
    soup = _get_soup(url)
    if not soup: return None
    try:
        details = {'source_url': url}
        title_tag = soup.select_one('h1.entry-title, h1#release-title')
        details['title'] = _clean_text(title_tag.get_text()) if title_tag else "Unknown Title"
        details['year'] = _extract_year(details['title'])
        details.update(_extract_season_info(details['title']))

        if srt_tag := soup.select_one('a#download-button'):
            details['srt_url'] = srt_tag.get('data-downloadurl') or srt_tag.get('href')

        if poster_tag := soup.select_one('figure#release-poster img, .entry-content figure img'):
            if src := poster_tag.get('src'): details['poster_url'] = urljoin(BASE_URL, src)

        if imdb_tag := soup.select_one('a#imdb-button, a[href*="imdb.com"]'):
            details['imdb_url'] = imdb_tag.get('href')
            details['imdb_id'] = _extract_imdb_id(details['imdb_url'])

        if desc_tag := soup.select_one('div#synopsis, .entry-content p'):
            details['description'] = _clean_text(desc_tag.get_text(separator='\n', strip=True))

        details_table = soup.select_one('#release-details-table tbody')
        table_data = {}
        if details_table:
            for row in details_table.select('tr'):
                cells = row.select('td')
                if len(cells) >= 2:
                    label = _clean_text(cells[0].get_text()).lower().strip().replace(':', '')
                    table_data[label] = cells[1]

        def get_field_data(labels):
            for label in labels:
                if label in table_data:
                    cell = table_data[label]
                    text = _clean_text(cell.get_text())
                    link_tag = cell.select_one('a')
                    url = urljoin(BASE_URL, link_tag['href']) if link_tag and link_tag.has_attr('href') else None
                    return {'name': text, 'url': url}
            return None

        field_mappings = [
            ('director', ['director', 'സംവിധായകൻ']),
            ('genre', ['genre', 'വിഭാഗം']),
            ('language', ['language', 'ഭാഷ']),
            ('translator', ['translator', 'പരിഭാഷകർ', 'പരിഭാഷകൻ']),
            ('imdb_rating', ['imdb rating', 'imdb', 'ഐ.എം.ഡി.ബി']),
            ('msone_release', ['msone release', 'msone', 'റിലീസ് നം']),
            ('certification', ['certification', 'സെർട്ടിഫിക്കേഷൻ'])
        ]
        for field_name, labels in field_mappings:
            if field_value := get_field_data(labels):
                details[field_name] = field_value

        return details
    except Exception:
        logger.exception(f"Full scraping failed for {url}")
        return None

# --- Database Functions ---
async def remove_subtitle(unique_id: str) -> bool:
    if not db_pool: return False
    try:
        result = await db_pool.execute("DELETE FROM subtitles WHERE unique_id = $1", unique_id)
        # "DELETE 1" on success, "DELETE 0" on no-op
        return result.endswith('1')
    except Exception as e:
        logger.error(f"Failed to remove subtitle {unique_id}: {e}")
        return False

async def rescrape_subtitle(unique_id: str) -> bool:
    if not db_pool: return False
    try:
        record = await db_pool.fetchrow("SELECT source_url FROM subtitles WHERE unique_id = $1", unique_id)
        if not record or not record['source_url']:
            logger.warning(f"Rescrape failed: No source_url found for {unique_id}")
            return False

        if details := scrape_page_details(record['source_url']):
            await upsert_subtitle(details)
            logger.info(f"Successfully rescraped and updated {unique_id}")
            return True
        else:
            logger.error(f"Rescrape failed: Scraping returned no details for {record['source_url']}")
            return False
    except Exception as e:
        logger.error(f"Failed to rescrape subtitle {unique_id}: {e}")
        return False

async def init_db():
    global db_pool
    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        async with db_pool.acquire() as conn:
            await conn.execute("CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY);")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS subtitles (
                    unique_id TEXT PRIMARY KEY,
                    imdb_id TEXT,
                    source_url TEXT,
                    scraped_at TIMESTAMPTZ,
                    title TEXT,
                    year INTEGER,
                    is_series BOOLEAN,
                    season_number INTEGER,
                    series_name TEXT,
                    total_seasons INTEGER,
                    srt_url TEXT,
                    poster_url TEXT,
                    imdb_url TEXT,
                    description TEXT,
                    director TEXT,
                    genre TEXT,
                    language TEXT,
                    translator TEXT,
                    imdb_rating TEXT,
                    msone_release TEXT,
                    certification TEXT,
                    poster_maker TEXT
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

    unique_id = f"{details['imdb_id']}-S{details['season_number']}" if details.get('is_series') else details['imdb_id']

    db_record = {
        'unique_id': unique_id,
        'imdb_id': details.get('imdb_id'),
        'source_url': details.get('source_url'),
        'scraped_at': datetime.now(),
        'title': details.get('title'),
        'year': int(details['year']) if details.get('year') else None,
        'is_series': details.get('is_series'),
        'season_number': details.get('season_number'),
        'series_name': details.get('series_name'),
        'total_seasons': None, # This will be updated by a separate process
        'srt_url': details.get('srt_url'),
        'poster_url': details.get('poster_url'),
        'imdb_url': details.get('imdb_url'),
        'description': details.get('description'),
        'director': json.dumps(details.get('director')) if details.get('director') else None,
        'genre': json.dumps(details.get('genre')) if details.get('genre') else None,
        'language': json.dumps(details.get('language')) if details.get('language') else None,
        'translator': json.dumps(details.get('translator')) if details.get('translator') else None,
        'imdb_rating': json.dumps(details.get('imdb_rating')) if details.get('imdb_rating') else None,
        'msone_release': json.dumps(details.get('msone_release')) if details.get('msone_release') else None,
        'certification': json.dumps(details.get('certification')) if details.get('certification') else None,
        'poster_maker': json.dumps(details.get('poster_maker')) if details.get('poster_maker') else None,
    }

    query = """
        INSERT INTO subtitles (
            unique_id, imdb_id, source_url, scraped_at, title, year, is_series,
            season_number, series_name, total_seasons, srt_url, poster_url, imdb_url,
            description, director, genre, language, translator, imdb_rating,
            msone_release, certification, poster_maker
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22
        )
        ON CONFLICT (unique_id) DO UPDATE SET
            source_url = EXCLUDED.source_url, scraped_at = EXCLUDED.scraped_at, title = EXCLUDED.title,
            year = EXCLUDED.year, is_series = EXCLUDED.is_series, season_number = EXCLUDED.season_number,
            series_name = EXCLUDED.series_name, srt_url = EXCLUDED.srt_url, poster_url = EXCLUDED.poster_url,
            imdb_url = EXCLUDED.imdb_url, description = EXCLUDED.description, director = EXCLUDED.director,
            genre = EXCLUDED.genre, language = EXCLUDED.language, translator = EXCLUDED.translator,
            imdb_rating = EXCLUDED.imdb_rating, msone_release = EXCLUDED.msone_release,
            certification = EXCLUDED.certification, poster_maker = EXCLUDED.poster_maker;
    """
    await db_pool.execute(query, *db_record.values())

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

    buttons = []
    if entry.get('imdb_url'): buttons.append({'text': '🎬 View on IMDb', 'url': entry['imdb_url']})
    if entry.get('source_url'): buttons.append({'text': '📄 Page Link', 'url': entry['source_url']})
    if buttons: keyboard.append(buttons)

    keyboard.append([{'text': '🔙 Back', 'callback_data': 'menu_home'}, {'text': '❌ Close', 'callback_data': 'menu_close'}])
    return {'inline_keyboard': keyboard}

def create_scraper_panel_keyboard() -> Dict:
    buttons = [
        {'text': "➕ Add", 'callback_data': 'scpr_add'},
        {'text': "➖ Remove", 'callback_data': 'scpr_remove'},
        {'text': "🔄 Rescrape", 'callback_data': 'scpr_rescrape'},
        {'text': "👁️ View", 'callback_data': 'scpr_view'},
    ]
    return {'inline_keyboard': [buttons, [{'text': '❌ Close', 'callback_data': 'menu_close'}]]}

async def run_broadcast(message: dict):
    owner_id = message['from']['id']
    replied_message = message['reply_to_message']

    if not db_pool:
        await send_telegram_message({'chat_id': owner_id, 'text': "Database not connected. Broadcast aborted."})
        return

    users = await db_pool.fetch("SELECT user_id FROM users")
    if not users:
        await send_telegram_message({'chat_id': owner_id, 'text': "No users in database to broadcast to."})
        return

    tasks = []
    for user in users:
        task = send_telegram_message({
            'method': 'copyMessage',
            'chat_id': user['user_id'],
            'from_chat_id': replied_message['chat']['id'],
            'message_id': replied_message['message_id']
        })
        tasks.append(task)
        await asyncio.sleep(0.05)  # To avoid hitting API rate limits

    results = await asyncio.gather(*tasks, return_exceptions=True)

    success_count = sum(1 for r in results if isinstance(r, dict) and r.get('ok'))
    failed_count = len(results) - success_count

    report_text = (
        f"**Broadcast Complete**\n\n"
        f"✅ **Sent to:** {success_count} users\n"
        f"❌ **Failed for:** {failed_count} users"
    )

    await send_telegram_message({
        'chat_id': owner_id,
        'text': report_text,
        'parse_mode': 'Markdown',
        'reply_markup': {'inline_keyboard': [[{'text': '❌ Close', 'callback_data': 'menu_close'}]]}
    })

async def process_download(unique_id: str, chat_id: str):
    logger.info(f"Starting download process for unique_id: {unique_id}")
    if not db_pool:
        logger.error("Download process failed: Database pool not available.")
        return

    entry = await db_pool.fetchrow("SELECT title, srt_url FROM subtitles WHERE unique_id = $1", unique_id)
    if not entry or not entry['srt_url']:
        logger.error(f"Download failed for {unique_id}: No entry or srt_url found in DB.")
        await send_telegram_message({'chat_id': chat_id, 'text': "Sorry, the download link is missing."})
        return

    logger.info(f"Found srt_url: {entry['srt_url']}")

    try:
        logger.info(f"Attempting to download file from {entry['srt_url']}")
        headers = {'User-Agent': 'Mozilla/5.0'}
        async with aiohttp.ClientSession() as session:
            async with session.get(entry['srt_url'], headers=headers) as resp:
                logger.info(f"Download response status: {resp.status}")
                if resp.status != 200:
                    await send_telegram_message({'chat_id': chat_id, 'text': "Sorry, I couldn't download the file."})
                    return
                file_content = await resp.read()
        logger.info(f"Successfully downloaded {len(file_content)} bytes.")

        # A ZIP file starts with b'PK'. This is more reliable than checking the URL.
        if file_content.startswith(b'PK'):
            logger.info("Detected .zip file by magic bytes. Starting extraction.")
            with io.BytesIO(file_content) as zip_buffer:
                with zipfile.ZipFile(zip_buffer) as zip_file:
                    count = 0
                    for file_info in zip_file.infolist():
                        if file_info.is_dir() or not file_info.filename.lower().endswith('.srt'):
                            continue

                        count += 1
                        filename = file_info.filename
                        sub_content = zip_file.read(filename)

                        logger.info(f"Uploading file {count}: {filename} ({len(sub_content)} bytes)")
                        form = aiohttp.FormData()
                        form.add_field('chat_id', chat_id)
                        form.add_field('document', sub_content, filename=filename, content_type='text/plain')
                        form.add_field('caption', filename)
                        upload_response = await send_telegram_message(form)
                        logger.info(f"Upload response for {filename}: {upload_response}")
                        await asyncio.sleep(0.5)

                    if count == 0:
                        logger.warning(f"No .srt files found in zip for {unique_id}")
                        await send_telegram_message({'chat_id': chat_id, 'text': "Sorry, I couldn't find any subtitle files in that ZIP archive."})
        else:
            logger.info("Detected single file. Preparing for upload.")
            filename = f"{entry['title']}.srt"
            form = aiohttp.FormData()
            form.add_field('chat_id', chat_id)
            form.add_field('document', file_content, filename=filename, content_type='text/plain')
            upload_response = await send_telegram_message(form)
            logger.info(f"Upload response for single file: {upload_response}")

    except Exception as e:
        logger.exception(f"Download processing failed for {unique_id}: {e}")
        await send_telegram_message({'chat_id': chat_id, 'text': "An error occurred while processing the file."})


# --- Core Handlers ---
async def handle_callback_query(callback_query: dict) -> Optional[Dict]:
    action, _, value = callback_query['data'].partition('_')
    message = callback_query['message']
    chat_id = str(message['chat']['id'])

    if action == 'menu':
        if value == 'close': return {'method': 'deleteMessage', 'chat_id': chat_id, 'message_id': message['message_id']}
        text_map = {'home': WELCOME_MESSAGE, 'about': ABOUT_MESSAGE, 'help': HELP_MESSAGE}
        if text := text_map.get(value):
            return {'method': 'editMessageText', 'text': text, 'reply_markup': create_menu_keyboard(value), 'parse_mode': 'Markdown', 'chat_id': chat_id, 'message_id': message['message_id']}

    elif action == 'view' and (entry := await db_pool.fetchrow("SELECT * FROM subtitles WHERE unique_id = $1", value)):
        await send_telegram_message({'method': 'deleteMessage', 'chat_id': chat_id, 'message_id': message['message_id']})

        def format_json_field(data: Optional[str]) -> str:
            if not data: return "N/A"
            try:
                field_data = json.loads(data)
                return field_data.get('name', 'N/A')
            except (json.JSONDecodeError, AttributeError):
                return str(data) if data else "N/A"

        caption_parts = [f"**{entry['title']}**"]

        if msone := format_json_field(entry.get('msone_release')):
             if msone != "N/A": caption_parts.append(f"**MSone Release:** `{msone}`")
        if director := format_json_field(entry.get('director')):
            if director != "N/A": caption_parts.append(f"**Director:** {director}")
        if lang := format_json_field(entry.get('language')):
            if lang != "N/A": caption_parts.append(f"**Language:** {lang}")
        if genre := format_json_field(entry.get('genre')):
            if genre != "N/A": caption_parts.append(f"**Genre:** {genre}")
        if rating := format_json_field(entry.get('imdb_rating')):
            if rating != "N/A": caption_parts.append(f"**IMDb Rating:** {rating}")
        if cert := format_json_field(entry.get('certification')):
            if cert != "N/A": caption_parts.append(f"**Certification:** {cert}")
        if translator := format_json_field(entry.get('translator')):
            if translator != "N/A": caption_parts.append(f"**Translated By:** {translator}")

        if entry.get('is_series'):
            caption_parts.append(f"**Season:** {entry.get('season_number', 'N/A')}")
            if entry.get('total_seasons'):
                caption_parts.append(f"**Total Seasons:** {entry['total_seasons']}")

        if description := entry.get('description'):
            caption_parts.append(f"\n**Synopsis:**\n{description}")

        if str(chat_id) == OWNER_ID:
            caption_parts.append(f"\n\n**Admin Info:**\n`{entry['unique_id']}`")

        caption = "\n".join(caption_parts)

        payload = {'chat_id': chat_id, 'caption': caption, 'parse_mode': 'Markdown', 'reply_markup': create_detail_keyboard(entry)}

        if entry.get('poster_url'):
            payload.update({'method': 'sendPhoto', 'photo': entry['poster_url']})
            # If sending photo fails (e.g., bad URL), fall back to text message
            if (response := await send_telegram_message(payload)) and response.get('ok'):
                return None

        # Fallback to sending a text message if photo fails or doesn't exist
        del payload['caption']
        payload.update({'method': 'sendMessage', 'text': caption})
        await send_telegram_message(payload)
        return None

    elif action == 'download':
        asyncio.create_task(process_download(value, chat_id))
        return {'method': 'answerCallbackQuery', 'callback_query_id': callback_query['id'], 'text': "Please wait, preparing your download..."}

    elif action == 'scpr' and str(chat_id) == OWNER_ID:
        task_map = {
            'add': "Please send the malayalamsubtitles.org URL to add.",
            'remove': "Please send the `unique_id` to remove.",
            'rescrape': "Please send the `unique_id` to rescrape.",
            'view': "Please send the `unique_id` to view."
        }
        if prompt_text := task_map.get(value):
            admin_tasks[str(chat_id)] = value
            return {'method': 'editMessageText', 'text': prompt_text, 'chat_id': chat_id, 'message_id': message['message_id']}

    return None

async def handle_telegram_message(message_data: dict) -> Optional[Dict]:
    user, message = None, None
    if 'callback_query' in message_data:
        user, message = message_data['callback_query']['from'], message_data['callback_query']['message']
    elif 'message' in message_data:
        user, message = message_data['message'].get('from'), message_data['message']

    if not user or not (user_id := user.get('id')): return None

    if not await check_user_membership(user_id):
        if 'callback_query' in message_data:
            await send_telegram_message({'method': 'answerCallbackQuery', 'callback_query_id': message_data['callback_query']['id'], 'text': "Please join our channel to use the bot.", 'show_alert': True})
        return {'chat_id': user_id, 'text': "You must join our channel to use this bot.", 'reply_markup': {'inline_keyboard': [[{'text': "Join Channel", 'url': FORCE_SUB_CHANNEL_LINK}]]}}

    await add_user(user_id)

    if 'callback_query' in message_data:
        if response := await handle_callback_query(message_data['callback_query']):
            await send_telegram_message(response)
        return {'method': 'answerCallbackQuery', 'callback_query_id': message_data['callback_query']['id']}

    text = message.get('text', '').strip()
    if not text: return None

    # --- Handle pending admin tasks ---
    if str(user_id) == OWNER_ID and (task := admin_tasks.pop(str(user_id), None)):
        input_value = text
        if task == 'add':
            if details := scrape_page_details(input_value):
                await upsert_subtitle(details)
                return {'chat_id': user_id, 'text': f"✅ Added/Updated: **{details['title']}**"}
            return {'chat_id': user_id, 'text': f"❌ Failed to scrape or add entry for URL: {input_value}"}
        elif task == 'remove':
            if await remove_subtitle(input_value):
                return {'chat_id': user_id, 'text': f"✅ Entry `{input_value}` has been removed."}
            return {'chat_id': user_id, 'text': f"❌ Failed to remove entry `{input_value}`."}
        elif task == 'rescrape':
            if await rescrape_subtitle(input_value):
                return {'chat_id': user_id, 'text': f"✅ Entry `{input_value}` has been rescraped."}
            return {'chat_id': user_id, 'text': f"❌ Failed to rescrape entry `{input_value}`."}
        elif task == 'view':
            # Reuse the existing view logic by crafting a fake callback query
            fake_callback = {
                'data': f'view_{input_value}',
                'message': message,
                'from': user
            }
            if response := await handle_callback_query(fake_callback):
                await send_telegram_message(response)
            return None


    if text.startswith('/'): # Commands
        command, *args = text.split()
        if command == '/start': return {'chat_id': user_id, 'text': WELCOME_MESSAGE, 'reply_markup': create_menu_keyboard('home')}

        if str(user_id) == OWNER_ID:
            if command == '/ahelp':
                return {'chat_id': user_id, 'text': AHELP_MESSAGE, 'parse_mode': 'Markdown'}

            if command == '/scpr':
                return {'chat_id': user_id, 'text': "🛠️ Admin Scraper Panel", 'reply_markup': create_scraper_panel_keyboard()}

            if command == '/stats':
                if not db_pool: return {'chat_id': user_id, 'text': "Database not connected."}

                total_users = await db_pool.fetchval("SELECT COUNT(*) FROM users")
                total_entries = await db_pool.fetchval("SELECT COUNT(*) FROM subtitles")
                movie_count = await db_pool.fetchval("SELECT COUNT(*) FROM subtitles WHERE is_series = false")
                series_count = await db_pool.fetchval("SELECT COUNT(*) FROM subtitles WHERE is_series = true")

                stats_text = (
                    f"**Bot Statistics**\n\n"
                    f"👥 **Total Users:** {total_users}\n"
                    f"🎬 **Total Entries:** {total_entries}\n"
                    f"  - **Movies:** {movie_count}\n"
                    f"  - **Series:** {series_count}"
                )
                return {'chat_id': user_id, 'text': stats_text, 'parse_mode': 'Markdown'}

            if command == '/add' and args:
                if details := scrape_page_details(args[0]):
                    await upsert_subtitle(details)
                    return {'chat_id': user_id, 'text': f"✅ Added/Updated **{details['title']}**."}
                return {'chat_id': user_id, 'text': "❌ Failed to scrape or add entry."}

            if command == '/broadcast':
                if not message.get('reply_to_message'):
                    return {'chat_id': user_id, 'text': "Please reply to a message to broadcast it."}

                asyncio.create_task(run_broadcast(message))
                return {'chat_id': user_id, 'text': "Broadcast started... I will send a report when it's complete."}

    # Search
    if len(text) > 1:
        query = """
            SELECT *, similarity(title, $1) AS score
            FROM subtitles
            WHERE similarity(title, $1) > 0.15
            ORDER BY score DESC
            LIMIT 10
        """
        if results := await db_pool.fetch(query, text):
            return {'chat_id': user_id, 'text': f"🔍 Found these for '{text}':", 'reply_markup': create_search_results_keyboard(results)}
    return {'chat_id': user_id, 'text': f'😔 No subtitles found for "{text}"'}

async def send_telegram_message(data: Any):
    if not TOKEN or not data: return {}

    method = 'sendDocument' if isinstance(data, aiohttp.FormData) else data.pop('method', 'sendMessage')
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"

    try:
        async with aiohttp.ClientSession() as session:
            post_kwargs = {'json': data} if isinstance(data, dict) else {'data': data}
            async with session.post(url, **post_kwargs) as resp:
                if resp.status != 200:
                    logger.error(f"Telegram API Error: {await resp.text()}")
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
