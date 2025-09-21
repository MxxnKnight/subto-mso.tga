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
from urllib.parse import urlparse
import re
import aiofiles
import asyncpg
from scraper import scrape_detail_page

from fastapi import FastAPI, Request, Response, HTTPException, Query

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
DATABASE_URL = os.environ.get("DATABASE_URL")

# --- Global Variables ---
db_pool: Optional[asyncpg.Pool] = None
LOG_GROUP_ID = os.environ.get("LOG_GROUP_ID")
LOG_TOPIC_ID = os.environ.get("LOG_TOPIC_ID")

# --- Menu Messages ---
WELCOME_MESSAGE = """
**🎬 Welcome to Malayalam Subtitle Search Bot!**

Your one-stop destination for high-quality Malayalam subtitles for movies and TV shows.

**🚀 What can I do?**
• Search for Malayalam subtitles
• Download subtitle files instantly
• Browse by movies or series
• Get detailed movie information

Just type any movie or series name to get started!
"""

ABOUT_MESSAGE = """**ℹ️ About This Bot**

**🌐 Technical Details:**
- **Hosted on:** Render.com
- **Framework:** FastAPI + Custom Telegram Bot API
- **Database:** malayalamsubtitles.org
-**Developer:** [@Mxxn_Knight](tg://resolve?domain=Mxxn_Knight)
- **Version:** 2.0 Enhanced

**✨ Features:**
- Real-time subtitle search
- Instant file downloads
- Series season management
- Comprehensive movie details
- Admin controls

**📊 Data Source:** malayalamsubtitles.org"""

HELP_MESSAGE = """
**❓ How to Use This Bot**

**🔍 Searching:**
• Type any movie/series name
• Use English names for better results
• Add year for specific versions (e.g., "Dune 2021")

**📺 Series:**
• Search series name to see all seasons
• Click season buttons to view detailed message
• Each season has separate download links

**🎥 Movies:**
• Direct search shows movie details
• One-click download available
• View IMDb ratings and details

**💡 Tips:**
• Try different name variations
• Check spelling for better results

**📝 Note:**
This bot provides subtitle files only, not movie content.
"""

TOS_MESSAGE = """
**📋 Terms of Service**

By using this bot, you agree to:

**1. 📜 Legal Use Only**
• Use subtitles for legally owned content only
• Respect copyright laws in your jurisdiction

**2. 🗄️ Data Source**
• Content scraped from malayalamsubtitles.org
• Bot operates under fair use principles
• No copyright infringement intended
• All subtitles owned by malayalamsubtitles.org
• We don't have any ownership in the files provided by bot

**3. ⚠️ Limitations**
• Service provided "as-is" without warranties
• Uptime not guaranteed
• Database updated periodically

**4. 🚫 Prohibited Actions**
• No spam or abuse of bot services
• No commercial redistribution of content
• No automated scraping of this bot

**5. 🔒 Privacy**
• We don't store personal messages
• Search queries logged for improvement
• No data shared with third parties

**📞 Contact:** Message the bot admin for issues.

By continuing to use this bot, you accept these terms.
"""

async def init_db():
    """Initialize the database and create tables if they don't exist."""
    if not DATABASE_URL:
        logger.warning("DATABASE_URL not set. Skipping database initialization.")
        return

    global db_pool
    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        if db_pool is None:
             raise Exception("Database pool was not created.")

        async with db_pool.acquire() as connection:
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS subtitles (
                    unique_id TEXT PRIMARY KEY,
                    imdb_id TEXT,
                    source_url TEXT,
                    scraped_at TIMESTAMPTZ DEFAULT NOW(),
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
                    director JSONB,
                    genre JSONB,
                    language JSONB,
                    translator JSONB,
                    imdb_rating JSONB,
                    msone_release JSONB,
                    certification JSONB,
                    poster_maker JSONB
                );
            """)
            await connection.execute("CREATE INDEX IF NOT EXISTS idx_imdb_id ON subtitles (imdb_id);")
            await connection.execute("CREATE INDEX IF NOT EXISTS idx_series_name ON subtitles (series_name);")

        logger.info("Database connection pool created and tables 'users' and 'subtitles' initialized.")
    except Exception as e:
        logger.error(f"Could not connect to database or initialize tables: {e}")
        db_pool = None # Ensure pool is None if setup fails

def get_base_series_name(title: str) -> str:
    """Extracts the base name of a series from its full title."""
    if not title:
        return ""
    # This regex splits the title by "Season X" or "സീസൺ X" and takes the first part.
    # It's a reliable way to get the base name for the current database structure.
    base_name = re.split(r'\s+Season\s+\d|\s+സീസൺ\s+\d', title, 1, re.IGNORECASE)[0]
    return base_name.strip()

async def add_user(user_id: int):
    """Add a user to the database if they don't already exist."""
    if not db_pool:
        return

    try:
        # Using ON CONFLICT is efficient and safe for concurrent requests
        await db_pool.execute(
            "INSERT INTO users (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
            user_id
        )
    except Exception as e:
        logger.error(f"Error adding user {user_id} to database: {e}")

async def get_total_users_count() -> int:
    """Get the total number of users from the database."""
    if not db_pool:
        return 0

    try:
        count = await db_pool.fetchval("SELECT COUNT(*) FROM users")
        return count if count is not None else 0
    except Exception as e:
        logger.error(f"Error getting user count from database: {e}")
        return 0

async def broadcast_message(message_data: dict, admin_chat_id: int) -> Dict:
    """Broadcast a message to all tracked users from the database."""
    if not db_pool:
        return {
            'chat_id': admin_chat_id,
            'text': "❌ Database not connected. Cannot broadcast.",
            'parse_mode': 'Markdown'
        }

    try:
        user_records = await db_pool.fetch("SELECT user_id FROM users")
        all_user_ids = [record['user_id'] for record in user_records]
    except Exception as e:
        logger.error(f"Could not fetch users for broadcast: {e}")
        return {
            'chat_id': admin_chat_id,
            'text': f"❌ An error occurred while fetching users from the database: {e}",
            'parse_mode': 'Markdown'
        }

    if not all_user_ids:
        return {
            'chat_id': admin_chat_id,
            'text': "❌ No users to broadcast to.",
            'parse_mode': 'Markdown'
        }
    
    # Extract the replied message data
    original_message = message_data.get('reply_to_message', {})
    if not original_message:
        return {
            'chat_id': admin_chat_id,
            'text': "❌ Please reply to a message to broadcast it.",
            'parse_mode': 'Markdown'
        }
    
    # Prepare the broadcast message
    broadcast_payload = {
        'text': original_message.get('text', ''),
        'photo': original_message.get('photo'),
        'video': original_message.get('video'),
        'document': original_message.get('document'),
        'audio': original_message.get('audio'),
        'voice': original_message.get('voice'),
        'sticker': original_message.get('sticker'),
        'animation': original_message.get('animation'),
        'video_note': original_message.get('video_note'),
        'caption': original_message.get('caption', ''),
        'parse_mode': 'Markdown'
    }
    
    # Remove None values
    broadcast_payload = {k: v for k, v in broadcast_payload.items() if v is not None}
    
    # Determine the method based on content type
    if original_message.get('photo'):
        broadcast_payload['method'] = 'sendPhoto'
    elif original_message.get('video'):
        broadcast_payload['method'] = 'sendVideo'
    elif original_message.get('document'):
        broadcast_payload['method'] = 'sendDocument'
    elif original_message.get('audio'):
        broadcast_payload['method'] = 'sendAudio'
    elif original_message.get('voice'):
        broadcast_payload['method'] = 'sendVoice'
    elif original_message.get('sticker'):
        broadcast_payload['method'] = 'sendSticker'
    elif original_message.get('animation'):
        broadcast_payload['method'] = 'sendAnimation'
    elif original_message.get('video_note'):
        broadcast_payload['method'] = 'sendVideoNote'
    else:
        broadcast_payload['method'] = 'sendMessage'
    
    # Send to all tracked users
    successful_sends = 0
    failed_sends = 0
    
    for user_id in all_user_ids:
        try:
            user_payload = broadcast_payload.copy()
            user_payload['chat_id'] = user_id
            
            # Handle different media types
            if broadcast_payload['method'] == 'sendPhoto':
                user_payload['photo'] = original_message['photo'][-1]['file_id']  # Get highest resolution
            elif broadcast_payload['method'] == 'sendVideo':
                user_payload['video'] = original_message['video']['file_id']
            elif broadcast_payload['method'] == 'sendDocument':
                user_payload['document'] = original_message['document']['file_id']
            elif broadcast_payload['method'] == 'sendAudio':
                user_payload['audio'] = original_message['audio']['file_id']
            elif broadcast_payload['method'] == 'sendVoice':
                user_payload['voice'] = original_message['voice']['file_id']
            elif broadcast_payload['method'] == 'sendSticker':
                user_payload['sticker'] = original_message['sticker']['file_id']
            elif broadcast_payload['method'] == 'sendAnimation':
                user_payload['animation'] = original_message['animation']['file_id']
            elif broadcast_payload['method'] == 'sendVideoNote':
                user_payload['video_note'] = original_message['video_note']['file_id']
            
            await send_telegram_message(user_payload)
            successful_sends += 1
            
            # Small delay to avoid rate limiting
            await asyncio.sleep(0.05)
            
        except Exception as e:
            logger.error(f"Failed to send broadcast to user {user_id}: {e}")
            failed_sends += 1
    
    # Send confirmation to admin
    stats_text = f"""📢 **Broadcast Complete!**

✅ **Successfully sent to:** {successful_sends} users
❌ **Failed to send to:** {failed_sends} users
📊 **Total users:** {len(all_user_ids)} users

**Message type:** {broadcast_payload['method'].replace('send', '').title()}"""
    
    return {
        'chat_id': admin_chat_id,
        'text': stats_text,
        'parse_mode': 'Markdown'
    }

async def send_log(text: str):
    """Sends a log message to the configured Telegram group and topic."""
    if not LOG_GROUP_ID:
        return

    payload = {
        'chat_id': LOG_GROUP_ID,
        'text': text,
        'parse_mode': 'Markdown'
    }

    if LOG_TOPIC_ID:
        payload['message_thread_id'] = LOG_TOPIC_ID

    # This will use the default 'sendMessage' method
    await send_telegram_message(payload)

async def search_content(query: str) -> List[Dict]:
    """Search for subtitles in the database with relevance scoring."""
    if not db_pool or not query:
        return []

    query_lower = query.lower().strip()

    # Direct IMDb ID search
    if query_lower.startswith('tt') and query_lower[2:].isdigit():
        record = await db_pool.fetchrow("SELECT * FROM subtitles WHERE imdb_id = $1 LIMIT 1", query_lower)
        if record:
            return [{'type': 'direct', 'imdb_id': record['unique_id'], 'entry': dict(record)}]

    # Full-text and pattern matching search
    # Using ts_rank for relevance, with additional scoring for exact matches
    search_query = """
        SELECT *,
               ts_rank(to_tsvector('english', title), websearch_to_tsquery('english', $1)) as relevance
        FROM subtitles
        WHERE to_tsvector('english', title) @@ websearch_to_tsquery('english', $1)
           OR title ILIKE $2
           OR series_name ILIKE $2
        ORDER BY relevance DESC, title
        LIMIT 20;
    """

    # Use '%' for ILIKE pattern matching
    like_query = f"%{query_lower}%"

    records = await db_pool.fetch(search_query, query, like_query)

    results = []
    for record in records:
        results.append({
            'type': 'match',
            'imdb_id': record['unique_id'],
            'entry': dict(record),
            'relevance': record['relevance'] or 0 # Ensure relevance is not None
        })

    return results

async def get_series_seasons(series_name: str) -> Dict[int, str]:
    """Get all seasons for a given series name from the database."""
    if not db_pool or not series_name:
        return {}

    query = """
        SELECT season_number, unique_id
        FROM subtitles
        WHERE is_series = TRUE AND series_name ILIKE $1
        ORDER BY season_number;
    """
    records = await db_pool.fetch(query, series_name)

    seasons = {record['season_number']: record['unique_id'] for record in records}
    return seasons

async def download_and_upload_subtitle(download_url: str, chat_id: str, title: str, status_message_id: Optional[int]):
    """Download, upload, and provide status updates for a subtitle file."""
    if not all([download_url, TOKEN, status_message_id]):
        logger.warning("download_and_upload_subtitle called with missing arguments.")
        return

    import aiohttp
    import aiofiles

    error_occurred = False
    try:
        logger.info(f"Starting download for '{title}' from {download_url}")
        await send_telegram_message({
            'method': 'editMessageText', 'chat_id': chat_id, 'message_id': status_message_id,
            'text': f"📥 Downloading subtitle for **{title}**...", 'parse_mode': 'Markdown'
        })

        with tempfile.TemporaryDirectory() as temp_dir:
            headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://malayalamsubtitles.org/'}
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(download_url, timeout=60) as resp:
                    logger.info(f"Download request for '{title}' returned status {resp.status}")
                    resp.raise_for_status()
                    content = await resp.read()
                    logger.info(f"Successfully downloaded {len(content)} bytes for '{title}'")

                    filename = f"{title.replace(' ', '_')}.zip"
                    if 'content-disposition' in resp.headers:
                        cd = resp.headers['content-disposition']
                        filename_match = re.search(r'filename="([^"]+)"', cd)
                        if filename_match: filename = filename_match.group(1)
                    
                    file_path = os.path.join(temp_dir, filename)
                    async with aiofiles.open(file_path, 'wb') as f:
                        await f.write(content)
                    logger.info(f"Saved '{title}' to temporary file: {file_path}")

                    await send_telegram_message({
                        'method': 'editMessageText', 'chat_id': chat_id, 'message_id': status_message_id,
                        'text': f"📤 Uploading **{title}**...", 'parse_mode': 'Markdown'
                    })

                    if filename.lower().endswith('.zip'):
                        await upload_zip_contents(file_path, chat_id, title)
                    else:
                        await upload_single_file(file_path, chat_id, filename)

    except Exception as e:
        error_occurred = True
        logger.exception(f"Error in download/upload process for '{title}': {e}")
        await send_telegram_message({
            'method': 'editMessageText', 'chat_id': chat_id, 'message_id': status_message_id,
            'text': "❌ **Download Failed**\nAn error occurred while fetching the subtitle. Please try again later.",
            'parse_mode': 'Markdown'
        })
    finally:
        if status_message_id:
            if error_occurred:
                await asyncio.sleep(5)
            logger.info(f"Deleting status message {status_message_id} for '{title}'")
            await send_telegram_message({'method': 'deleteMessage', 'chat_id': chat_id, 'message_id': status_message_id})

async def upload_zip_contents(zip_path: str, chat_id: str, title: str) -> bool:
    """Extract and upload all files from zip."""
    logger.info(f"Processing zip file for '{title}': {zip_path}")
    try:
        with tempfile.TemporaryDirectory() as extract_dir:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
            logger.info(f"Extracted zip contents for '{title}' to {extract_dir}")

            for root, _, files in os.walk(extract_dir):
                for file in files:
                    if file.lower().endswith(('.srt', '.ass', '.ssa', '.vtt')):
                        file_path = os.path.join(root, file)
                        logger.info(f"Found subtitle file '{file}' for '{title}', attempting upload.")
                        await upload_single_file(file_path, chat_id, file)
            return True
    except Exception as e:
        logger.error(f"Error processing zip file for '{title}': {e}")
        return False

async def upload_single_file(file_path: str, chat_id: str, filename: str) -> bool:
    """Upload single file to Telegram."""
    import aiohttp

    logger.info(f"Uploading file '{filename}' to chat {chat_id}")
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendDocument"

        async with aiohttp.ClientSession() as session:
            with open(file_path, 'rb') as f:
                data = aiohttp.FormData()
                data.add_field('chat_id', chat_id)
                data.add_field('document', f, filename=filename)
                data.add_field('caption', f'📁 {filename}')

                async with session.post(url, data=data) as resp:
                    if resp.status == 200:
                        logger.info(f"Successfully uploaded file '{filename}' to chat {chat_id}")
                        return True
                    else:
                        logger.error(f"Failed to upload file '{filename}'. Status: {resp.status}, Response: {await resp.text()}")
                        return False
    except Exception as e:
        logger.error(f"Exception while uploading file '{filename}': {e}")
        return False

def create_menu_keyboard(current_menu: str) -> Dict:
    """Create inline keyboard for menus."""
    keyboards = {
        'home': [
    [
        {'text': ' About', 'callback_data': 'menu_about'},
        {'text': ' Help', 'callback_data': 'menu_help'}
    ],
    [
        {'text': ' Terms of Service', 'callback_data': 'menu_tos'}
    ],
    [
        {'text': ' Close', 'callback_data': 'menu_close'}
    ]
],
        'about': [
    [
        {'text': ' Home', 'callback_data': 'menu_home'},
        {'text': ' Help', 'callback_data': 'menu_help'}
    ],
    [
        {'text': ' Terms of Service', 'callback_data': 'menu_tos'}
    ],
    [
        {'text': ' Close', 'callback_data': 'menu_close'}
    ]
],
        'help':  [
    [
        {'text': ' Home', 'callback_data': 'menu_home'},
        {'text': ' About', 'callback_data': 'menu_about'}
    ],
    [
        {'text': ' Terms of Service', 'callback_data': 'menu_tos'}
    ],
    [
        {'text': ' Close', 'callback_data': 'menu_close'}
    ]
],
        'tos': [
    [
        {'text': ' Home', 'callback_data': 'menu_home'},
        {'text': ' About', 'callback_data': 'menu_about'}
    ],
    [
        {'text': ' Help', 'callback_data': 'menu_help'}
    ],
    [
        {'text': ' Close', 'callback_data': 'menu_close'}
    ]
]
    }

    return {'inline_keyboard': keyboards.get(current_menu, keyboards['home'])}

def create_search_results_keyboard(results: List[Dict]) -> Dict:
    """Create keyboard for search results."""
    keyboard = []

    for result in results[:10]:  # Limit to 10 results
        entry = result['entry']
        title = entry.get('title', 'Unknown')[:50]  # Truncate long titles

        if entry.get('is_series'):
            title += f" (S{entry.get('season_number', 1)})"

        keyboard.append([{
            'text': title,
            'callback_data': f"view_{result['imdb_id']}"
        }])

    keyboard.append([{'text': ' Close', 'callback_data': 'menu_close'}])
    return {'inline_keyboard': keyboard}

def create_series_seasons_keyboard(seasons: Dict[int, str]) -> Dict:
    """Create keyboard for series seasons."""
    keyboard = []
    
    # Ensure seasons are sorted by season number (1, 2, 3...)
    sorted_season_numbers = sorted(seasons.keys())
    
    for season_num in sorted_season_numbers:
        keyboard.append([{
            'text': f'Season {season_num}',
            'callback_data': f"view_{seasons[season_num]}"
        }])

    keyboard.append([{'text': ' Close', 'callback_data': 'menu_close'}])
    return {'inline_keyboard': keyboard}

def format_movie_details(entry: asyncpg.Record, user_id: int) -> (str, str):
    """Formats movie/series details from a database record."""

    def get_display_value(value):
        """Safely parse JSONB fields and return the 'name' value."""
        if not value:
            return None
        # asyncpg automatically decodes jsonb, but scraper stores it as a string.
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return value # Return raw string if not valid JSON

        if isinstance(value, dict) and 'name' in value:
            return value['name']
        return value

    title = entry.get('title', 'Unknown Title')
    year_val = str(entry.get('year', ''))

    if year_val and f"({year_val})" not in title:
        title_with_year = f"{title} ({year_val})"
    else:
        title_with_year = title

    core_details_message = f"🎬 **{title_with_year}**\n\n"
    details = []

    # Note: The field names here match the database columns
    fields_to_format = [
        ("msone_release", "🆔 **MSOne Release:**"),
        ("language", "🗣️ **Language:**"),
        ("director", "🎬 **Director:**"),
        ("genre", "🎭 **Genre:**"),
        ("imdb_rating", "⭐ **IMDb Rating:**"),
        ("certification", "🏷️ **Certification:**"),
        ("translator", "🌐 **Translator:**")
    ]

    ignore_values = ['Unknown', 'N/A', 'Not Rated']

    for field_key, field_label in fields_to_format:
        raw_value = entry.get(field_key)
        if raw_value:
            display_value = get_display_value(raw_value)
            if display_value and str(display_value) not in ignore_values:
                details.append(f"{field_label} {display_value}")

    if details:
        core_details_message += "\n".join(details) + "\n\n"

    if entry.get('is_series'):
        core_details_message += f"📺 **Series Information:**\n"
        if entry.get('season_number'):
            core_details_message += f"• Season: {entry['season_number']}\n"
        if entry.get('total_seasons'):
            core_details_message += f"• Total Seasons Available: {entry['total_seasons']}\n"
        core_details_message += "\n"

    synopsis_text = ""
    if entry.get('description') and entry['description'] != 'No description available':
        synopsis_text = f"📖 **Synopsis:**\n{entry['description']}"

    # Add Admin ID if the user is the owner
    if str(user_id) == OWNER_ID:
        admin_info = f"\n\n🔧 **Admin ID:** `{entry['unique_id']}`"
        # Append to synopsis if it exists, otherwise append to core details
        if synopsis_text:
            synopsis_text += admin_info
        else:
            core_details_message += admin_info

    return core_details_message, synopsis_text

def create_detail_keyboard(entry: asyncpg.Record) -> Dict:
    """Create keyboard for movie detail page from a database record."""
    imdb_id = entry['unique_id']
    keyboard = []

    if entry.get('srt_url'):
        keyboard.append([{'text': '📥 Download Subtitle', 'callback_data': f"download_{imdb_id}"}])

    if entry.get('imdb_url'):
        keyboard.append([{'text': '🎬 View on IMDb', 'url': entry['imdb_url']}])

    keyboard_row = []
    if entry.get('source_url'):
        keyboard_row.append({'text': '🔗 Source', 'url': entry['source_url']})
    else:
        keyboard_row.append({'text': '🔙 Back to Search', 'callback_data': 'back_search'})
    
    keyboard_row.append({'text': ' Close', 'callback_data': 'menu_close'})
    keyboard.append(keyboard_row)

    return {'inline_keyboard': keyboard}

async def handle_callback_query(callback_data: str, message_data: dict, chat_id: str, user_id: int) -> Optional[Dict]:
    """Handle callback query from inline keyboards by querying the database."""
    if not db_pool: return None
    try:
        logger.info(f"Handling callback query: {callback_data}")
        if callback_data.startswith('menu_'):
            menu_type = callback_data.replace('menu_', '')
            if menu_type == 'close':
                return {'method': 'deleteMessage', 'chat_id': chat_id, 'message_id': message_data.get('message_id')}

            text_map = {'home': WELCOME_MESSAGE, 'about': ABOUT_MESSAGE, 'help': HELP_MESSAGE, 'tos': TOS_MESSAGE}
            return {
                'method': 'editMessageText',
                'text': text_map.get(menu_type, WELCOME_MESSAGE),
                'reply_markup': create_menu_keyboard(menu_type),
                'parse_mode': 'Markdown',
                'chat_id': chat_id,
                'message_id': message_data.get('message_id')
            }

        elif callback_data.startswith('close_'):
            try:
                _, photo_id, synopsis_id = callback_data.split('_')
                return {'method': 'delete_both', 'chat_id': chat_id, 'main_message_id': int(synopsis_id), 'reply_to_message_id': int(photo_id)}
            except (IndexError, ValueError) as e:
                logger.error(f"Error parsing close callback: {e}")
                return {'method': 'deleteMessage', 'chat_id': chat_id, 'message_id': message_data.get('message_id')}

        elif callback_data.startswith('view_'):
            unique_id = callback_data.replace('view_', '')
            entry = await db_pool.fetchrow("SELECT * FROM subtitles WHERE unique_id = $1", unique_id)

            if entry:
                poster_url = entry.get('poster_url')
                if poster_url and poster_url.startswith('https'):
                    return {'method': 'delete_and_resend', 'chat_id': chat_id, 'message_id': message_data.get('message_id'), 'entry': entry}
                else:
                    core_details, synopsis = format_movie_details(entry, user_id)
                    full_text = f"{core_details}\n{synopsis}" if synopsis else core_details
                    return {
                        'method': 'editMessageText',
                        'chat_id': chat_id,
                        'message_id': message_data.get('message_id'),
                        'text': full_text,
                        'reply_markup': create_detail_keyboard(entry),
                        'parse_mode': 'Markdown'
                    }
            else:
                return {'method': 'answerCallbackQuery', 'text': 'Movie not found in database.', 'show_alert': True}

        elif callback_data.startswith('download_'):
            unique_id = callback_data.replace('download_', '')
            entry = await db_pool.fetchrow("SELECT title, srt_url FROM subtitles WHERE unique_id = $1", unique_id)

            if entry and entry['srt_url']:
                status_message = await send_telegram_message({'chat_id': chat_id, 'text': '⏳ Preparing to download...'})
                status_message_id = status_message.get('result', {}).get('message_id')
                if status_message_id:
                    asyncio.create_task(download_and_upload_subtitle(entry['srt_url'], chat_id, entry['title'], status_message_id))
                    return {'method': 'answerCallbackQuery', 'text': 'Download started! You will receive the file shortly.'}
                else:
                    return {'method': 'answerCallbackQuery', 'text': 'Could not start download process.', 'show_alert': True}
            else:
                return {'method': 'answerCallbackQuery', 'text': 'Download link not available for this entry.', 'show_alert': True}

        elif callback_data == 'back_search':
            return {'method': 'editMessageText', 'text': '🔍 Send me a movie or series name to search.'}

    except Exception as e:
        logger.error(f"Error handling callback query: {e}")
        return {'method': 'answerCallbackQuery', 'text': 'An error occurred. Please try again.', 'show_alert': True}
    return None

async def handle_telegram_message(message_data: dict) -> Optional[Dict]:
    """Handle Telegram messages with database integration."""
    # Callback Query Handling
    if 'callback_query' in message_data:
        callback = message_data['callback_query']
        user_id = callback['from']['id']
        await add_user(user_id)
        response = await handle_callback_query(callback['data'], callback['message'], str(callback['message']['chat']['id']), user_id)

        if response:
            if response.get('method') == 'delete_and_resend':
                await send_telegram_message({'method': 'deleteMessage', 'chat_id': response['chat_id'], 'message_id': response['message_id']})
                entry = response['entry']
                core_details, synopsis = format_movie_details(entry, user_id)
                photo_message = await send_telegram_message({'method': 'sendPhoto', 'chat_id': response['chat_id'], 'photo': entry['poster_url'], 'caption': core_details, 'parse_mode': 'Markdown'})
                synopsis_message = await send_telegram_message({'method': 'sendMessage', 'chat_id': response['chat_id'], 'text': synopsis or "No synopsis."})

                if photo_message and synopsis_message:
                    photo_id = photo_message.get('result', {}).get('message_id')
                    synopsis_id = synopsis_message.get('result', {}).get('message_id')
                    keyboard = create_detail_keyboard(entry)
                    for row in keyboard['inline_keyboard']:
                        for button in row:
                            if button.get('callback_data') == 'menu_close':
                                button['callback_data'] = f"close_{photo_id}_{synopsis_id}"
                    await send_telegram_message({'method': 'editMessageReplyMarkup', 'chat_id': response['chat_id'], 'message_id': synopsis_id, 'reply_markup': keyboard})

            elif response.get('method') == 'delete_both':
                await asyncio.gather(
                    send_telegram_message({'method': 'deleteMessage', 'chat_id': response['chat_id'], 'message_id': response['main_message_id']}),
                    send_telegram_message({'method': 'deleteMessage', 'chat_id': response['chat_id'], 'message_id': response['reply_to_message_id']})
                )
            else:
                await send_telegram_message(response)

        await send_telegram_message({'method': 'answerCallbackQuery', 'callback_query_id': callback['id']})
        return None

    # Regular Message Handling
    message = message_data.get('message', {})
    text = message.get('text', '').strip()
    chat_id = message.get('chat', {}).get('id')
    user = message.get('from', {})
    user_id = user.get('id')

    if not all([chat_id, user_id, text, db_pool]):
        return None

    await add_user(user_id)
    logger.info(f"Message: '{text}' from {user.get('username', 'unknown')} ({user_id})")

    # Admin Commands
    if str(user_id) == OWNER_ID:
        if text.startswith('/'):
            parts = text.split()
            command = parts[0]

            if command == '/stats':
                stats = await db_pool.fetchrow("""
                    SELECT
                        (SELECT COUNT(*) FROM subtitles WHERE is_series = FALSE) as movies,
                        (SELECT COUNT(DISTINCT series_name) FROM subtitles WHERE is_series = TRUE) as series,
                        (SELECT COUNT(*) FROM subtitles) as total_entries,
                        (SELECT COUNT(*) FROM users) as total_users
                """)
                return {'chat_id': chat_id, 'text': f"📊 **Bot Statistics**\n\n🎬 Movies: {stats['movies']}\n📺 Series: {stats['series']}\n📚 Total Entries: {stats['total_entries']}\n👥 Users: {stats['total_users']}", 'parse_mode': 'Markdown'}

            elif command == '/delete':
                if len(parts) < 2: return {'chat_id': chat_id, 'text': "Usage: `/delete <unique_id>`"}
                deleted_count = await db_pool.execute("DELETE FROM subtitles WHERE unique_id = $1", parts[1])
                msg = f"Deleted entry `{parts[1]}`." if deleted_count != 'DELETE 0' else f"Entry `{parts[1]}` not found."
                return {'chat_id': chat_id, 'text': msg, 'parse_mode': 'Markdown'}

            # Simplified /rescrape for brevity in this refactoring context
            elif command == '/rescrape':
                if len(parts) < 2: return {'chat_id': chat_id, 'text': "Usage: `/rescrape <unique_id>`"}
                unique_id = parts[1]

                original_entry = await db_pool.fetchrow("SELECT source_url, title FROM subtitles WHERE unique_id = $1", unique_id)
                if not original_entry or not original_entry['source_url']:
                    return {'chat_id': chat_id, 'text': f"Cannot rescrape. Source URL not found for `{unique_id}`."}

                status_message = await send_telegram_message({'chat_id': chat_id, 'text': f"⏳ Rescraping **{original_entry['title']}**..."})

                loop = asyncio.get_event_loop()
                new_data = await loop.run_in_executor(None, scrape_detail_page, original_entry['source_url'])

                if new_data:
                    from scraper import extract_imdb_id # Keep import local to avoid circular dependency issues
                    await upsert_subtitle_from_app(new_data)
                    await send_telegram_message({'method': 'editMessageText', 'chat_id': chat_id, 'message_id': status_message.get('result', {}).get('message_id'), 'text': f"✅ Successfully rescraped and updated **{new_data.get('title')}**."})
                else:
                    await send_telegram_message({'method': 'editMessageText', 'chat_id': chat_id, 'message_id': status_message.get('result', {}).get('message_id'), 'text': f"❌ Failed to rescrape `{unique_id}`. Scraper returned no data."})
                return None


    # Regular Commands
    if text.startswith('/start'):
        await send_log(f"✅ User @{user.get('username', 'unknown')} ({user_id}) started.")
        return {'chat_id': chat_id, 'text': WELCOME_MESSAGE, 'reply_markup': create_menu_keyboard('home'), 'parse_mode': 'Markdown'}

    # Search
    if len(text) < 2: return {'chat_id': chat_id, 'text': "Please use at least 2 characters."}

    await send_log(f"🔍 User @{user.get('username', 'unknown')} ({user_id}) searched: `{text}`")
    results = await search_content(text)

    if not results:
        return {'chat_id': chat_id, 'text': f'😔 No subtitles found for "{text}"'}

    if len(results) == 1 and results[0]['type'] == 'direct':
        entry = await db_pool.fetchrow("SELECT * FROM subtitles WHERE unique_id = $1", results[0]['imdb_id'])
        if entry:
            core_details, synopsis = format_movie_details(entry)
            full_text = f"{core_details}\n{synopsis}" if synopsis else core_details
            if entry['poster_url']:
                return {'method': 'sendPhoto', 'chat_id': chat_id, 'photo': entry['poster_url'], 'caption': full_text, 'reply_markup': create_detail_keyboard(entry), 'parse_mode': 'Markdown'}
            else:
                return {'chat_id': chat_id, 'text': full_text, 'reply_markup': create_detail_keyboard(entry), 'parse_mode': 'Markdown'}

    # Handle series with multiple seasons
    first_entry = results[0]['entry']
    if first_entry.get('is_series'):
        series_name = get_base_series_name(first_entry.get('title', ''))
        if series_name:
            seasons = await get_series_seasons(series_name)
            if len(seasons) > 1:
                return {'chat_id': chat_id, 'text': f"📺 **{series_name}**\n\nFound {len(seasons)} seasons. Please select one:", 'reply_markup': create_series_seasons_keyboard(seasons), 'parse_mode': 'Markdown'}

    # Show list of results
    return {'chat_id': chat_id, 'text': f"🔍 **Found these for '{text}':**", 'reply_markup': create_search_results_keyboard(results), 'parse_mode': 'Markdown'}

async def upsert_subtitle_from_app(post_details: dict):
    """A helper to allow the app to upsert data, similar to the scraper."""
    if not db_pool: return
    from scraper import extract_imdb_id, extract_season_info # Local import

    imdb_id = extract_imdb_id(post_details.get('imdbURL'))
    if not imdb_id: return

    season_info = extract_season_info(post_details.get('title', ''))
    unique_id = f"{imdb_id}-S{season_info['season_number']}" if season_info.get('is_series') else imdb_id

    db_record = {
        'unique_id': unique_id, 'imdb_id': imdb_id, 'source_url': post_details.get('source_url'),
        'scraped_at': datetime.now(), 'title': post_details.get('title'),
        'year': int(post_details['year']) if post_details.get('year') else None,
        'is_series': season_info.get('is_series'), 'season_number': season_info.get('season_number'),
        'series_name': season_info.get('series_name'), 'total_seasons': None,
        'srt_url': post_details.get('srtURL'), 'poster_url': post_details.get('posterMalayalam'),
        'imdb_url': post_details.get('imdbURL'), 'description': post_details.get('descriptionMalayalam'),
        'director': json.dumps(post_details.get('director')), 'genre': json.dumps(post_details.get('genre')),
        'language': json.dumps(post_details.get('language')), 'translator': json.dumps(post_details.get('translatedBy')),
        'imdb_rating': json.dumps(post_details.get('imdb_rating')), 'msone_release': json.dumps(post_details.get('msone_release')),
        'certification': json.dumps(post_details.get('certification')), 'poster_maker': json.dumps(post_details.get('poster_maker')),
    }

    query = """
        INSERT INTO subtitles (
            unique_id, imdb_id, source_url, scraped_at, title, year, is_series, season_number, series_name, total_seasons,
            srt_url, poster_url, imdb_url, description, director, genre, language, translator, imdb_rating,
            msone_release, certification, poster_maker
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22)
        ON CONFLICT (unique_id) DO UPDATE SET
            source_url = EXCLUDED.source_url, scraped_at = EXCLUDED.scraped_at, title = EXCLUDED.title, year = EXCLUDED.year,
            is_series = EXCLUDED.is_series, season_number = EXCLUDED.season_number, series_name = EXCLUDED.series_name,
            srt_url = EXCLUDED.srt_url, poster_url = EXCLUDED.poster_url, imdb_url = EXCLUDED.imdb_url,
            description = EXCLUDED.description, director = EXCLUDED.director, genre = EXCLUDED.genre, language = EXCLUDED.language,
            translator = EXCLUDED.translator, imdb_rating = EXCLUDED.imdb_rating, msone_release = EXCLUDED.msone_release,
            certification = EXCLUDED.certification, poster_maker = EXCLUDED.poster_maker;
    """
    await db_pool.execute(query, *db_record.values())
    logger.info(f"App UPSERTED: {db_record['title']} ({db_record['unique_id']})")


async def send_telegram_message(data: dict):
    """Send message to Telegram using Bot API."""
    if not TOKEN or not data:
        return {}

    import aiohttp

    method = data.pop('method', 'sendMessage')
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"

    try:
        async with aiohttp.ClientSession() as session:
            # The reference code had a special check for photos.
            # A cleaner way is to just use the 'sendPhoto' method directly when needed.
            # However, to respect the structure, we will keep the FormData logic for photo uploads.
            if method == 'sendPhoto':
                form_data = aiohttp.FormData()
                for key, value in data.items():
                    if key == 'reply_markup' and isinstance(value, dict):
                        form_data.add_field(key, json.dumps(value))
                    elif key != 'photo':
                        form_data.add_field(key, str(value))

                # The photo itself needs to be handled carefully
                # Assuming the 'photo' value is a URL for this logic to work simply.
                # For local file uploads, the stream needs to be passed.
                form_data.add_field('photo', data['photo'])

                async with session.post(url, data=form_data) as resp:
                    if resp.status != 200:
                        logger.error(f"Failed to send photo: {resp.status} - {await resp.text()}")
                    return await resp.json()
            else:
                # Regular API call with JSON payload
                async with session.post(url, json=data) as resp:
                    if resp.status != 200:
                        logger.error(f"Failed to send message: {resp.status} - {await resp.text()}")
                    return await resp.json()

    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return {}

# --- FastAPI App ---
app = FastAPI(
    title="Enhanced Subtitle Search Bot API",
    description="Advanced Telegram bot for Malayalam subtitles with comprehensive features",
    version="2.0.0"
)

@app.on_event("startup")
async def startup_event():
    """Initialize application on startup."""
    logger.info("Starting enhanced application...")
    await init_db()

    # Set webhook if token is available
    # if TOKEN:
    #     import aiohttp
    #
    #     try:
    #         base_url = os.environ.get("RENDER_EXTERNAL_URL", "https://subto-mso-tga.onrender.com")
    #         webhook_url = f"{base_url}/telegram"
    #
    #         url = f"https://api.telegram.org/bot{TOKEN}/setWebhook"
    #         data = {
    #             "url": webhook_url,
    #             "secret_token": WEBHOOK_SECRET,
    #             "drop_pending_updates": True
    #         }
    #
    #         async with aiohttp.ClientSession() as session:
    #             async with session.post(url, json=data) as resp:
    #                 if resp.status == 200:
    #                     logger.info(f"Webhook set to {webhook_url}")
    #
    #                     # Notify owner
    #                     if OWNER_ID:
    #                         await send_telegram_message({
    #                             'chat_id': OWNER_ID,
    #                             'text': '🟢 **Bot Online & DB Connected!**\n\n✅ Subtitle DB loaded\n✅ User DB connected\n✅ Ready to serve',
    #                             'parse_mode': 'Markdown'
    #                         })
    #                 else:
    #                     logger.error(f"Failed to set webhook: {resp.status}")
    #     except Exception as e:
    #         logger.error(f"Error setting webhook: {e}")

@app.on_event("shutdown")
async def shutdown_event():
    """Clean up application on shutdown."""
    logger.info("Shutting down application...")
    if db_pool:
        await db_pool.close()
        logger.info("Database connection pool closed.")

# --- API Endpoints ---
@app.get("/")
async def root():
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not connected")

    stats = await db_pool.fetchrow("""
        SELECT
            (SELECT COUNT(*) FROM subtitles) as total_entries,
            (SELECT COUNT(DISTINCT series_name) FROM subtitles WHERE is_series = TRUE) as series_count
    """)
    return {
        "status": "ok",
        "message": "Enhanced Subtitle Search Bot API v2.0",
        "database_entries": stats['total_entries'],
        "series_count": stats['series_count']
    }

@app.head("/")
async def head_root():
    """Handles HEAD requests for the root path, used by UptimeRobot."""
    return Response(status_code=HTTPStatus.OK)

@app.get("/healthz")
async def health_check():
    is_healthy = db_pool is not None
    return {
        "status": "healthy" if is_healthy else "unhealthy",
        "database_connected": is_healthy
    }

@app.get("/api/subtitles")
async def api_search(
    query: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(10, ge=1, le=50)
):
    """Enhanced API search with series support."""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not connected")
    try:
        results = await search_content(query)

        if not results:
            return {"query": query, "count": 0, "results": [], "message": "No results found"}

        limited_results = results[:limit]
        formatted_results = []
        for result in limited_results:
            entry = result['entry']
            entry['imdb_id'] = result['imdb_id']
            entry['relevance_score'] = result.get('relevance', 0)
            formatted_results.append(entry)

        return {
            "query": query,
            "count": len(results),
            "returned": len(formatted_results),
            "results": formatted_results
        }

    except Exception as e:
        logger.error(f"API search error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/telegram")
async def telegram_webhook(request: Request):
    """Enhanced webhook endpoint with callback query support."""
    if not TOKEN:
        return Response(status_code=HTTPStatus.SERVICE_UNAVAILABLE)

    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        logger.warning("Webhook secret mismatch!")
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN)

    try:
        data = await request.json()
        logger.info(f"Webhook data: {json.dumps(data, indent=2)}")

        response_data = await handle_telegram_message(data)
        if response_data:
            await send_telegram_message(response_data)

        return Response(status_code=HTTPStatus.OK)

    except Exception as e:
        logger.error(f"Webhook error: {e}")
        raise HTTPException(status_code=HTTPStatus.INTERNAL_SERVER_ERROR)
