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
DB_FILE = os.environ.get("DB_FILE", "db.json")
SERIES_DB_FILE = os.environ.get("SERIES_DB_FILE", "series_db.json")

# --- Global Variables ---
db: Dict[str, Any] = {}
series_db: Dict[str, Dict[int, str]] = {}
tracked_users: set = set()
USERS_FILE = os.environ.get("USERS_FILE", "users.json")
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

async def load_databases():
    """Load both main and series databases asynchronously."""
    global db, series_db

    # Load main database
    try:
        async with aiofiles.open(DB_FILE, 'r', encoding='utf-8') as f:
            content = await f.read()
            db = json.loads(content)
            logger.info(f"Loaded main database: {len(db)} entries from {DB_FILE}")
    except Exception as e:
        logger.error(f"Error loading main database from {DB_FILE}: {e}")
        db = {}

    # Load series database
    try:
        async with aiofiles.open(SERIES_DB_FILE, 'r', encoding='utf-8') as f:
            content = await f.read()
            series_db = json.loads(content)
            logger.info(f"Loaded series database: {len(series_db)} series from {SERIES_DB_FILE}")
    except Exception as e:
        logger.warning(f"Series database not found at {SERIES_DB_FILE}: {e}")
        series_db = {}

async def load_tracked_users():
    """Load tracked users from file asynchronously."""
    global tracked_users
    try:
        async with aiofiles.open(USERS_FILE, 'r', encoding='utf-8') as f:
            content = await f.read()
            content = content.strip()
            if not content:
                logger.info(f"Users file {USERS_FILE} is empty, starting with empty set")
                tracked_users = set()
                return

            users_data = json.loads(content)
            tracked_users = set(users_data.get('users', []))
            logger.info(f"Loaded {len(tracked_users)} tracked users from {USERS_FILE}")
    except json.JSONDecodeError as e:
        logger.warning(f"Users file {USERS_FILE} contains invalid JSON: {e}. Starting with empty set.")
        tracked_users = set()
        try:
            await save_tracked_users()
        except Exception as save_error:
            logger.error(f"Could not create fresh users file: {save_error}")
    except FileNotFoundError:
        logger.info(f"Users file {USERS_FILE} not found, starting with empty set")
        tracked_users = set()
    except Exception as e:
        logger.warning(f"Error loading users file {USERS_FILE}: {e}. Starting with empty set.")
        tracked_users = set()

async def save_tracked_users():
    """Save tracked users to file asynchronously."""
    try:
        users_data = {'users': list(tracked_users)}
        async with aiofiles.open(USERS_FILE, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(users_data, indent=2))
        logger.info(f"Saved {len(tracked_users)} tracked users to {USERS_FILE}")
    except Exception as e:
        logger.error(f"Error saving tracked users to {USERS_FILE}: {e}")

def get_base_series_name(title: str) -> str:
    """Extracts the base name of a series from its full title."""
    if not title:
        return ""
    # This regex splits the title by "Season X" or "സീസൺ X" and takes the first part.
    # It's a reliable way to get the base name for the current database structure.
    base_name = re.split(r'\s+Season\s+\d|\s+സീസൺ\s+\d', title, 1, re.IGNORECASE)[0]
    return base_name.strip()

async def add_user(user_id: int):
    """Add user to tracked users if not already present."""
    if user_id not in tracked_users:
        tracked_users.add(user_id)
        await save_tracked_users()
        logger.info(f"Added new user {user_id} to tracking")


async def periodic_save_users():
    """Periodically save the tracked users list."""
    while True:
        await asyncio.sleep(300)  # 5 minutes
        logger.info("Performing periodic save of tracked users.")
        await save_tracked_users()

async def broadcast_message(message_data: dict, admin_chat_id: int) -> Dict:
    """Broadcast a message to all tracked users."""
    if not tracked_users:
        return {
            'chat_id': admin_chat_id,
            'text': "❌ No users to broadcast to. Users will be added when they interact with the bot.",
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
    
    for user_id in tracked_users:
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
📊 **Total users:** {len(tracked_users)} users

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

def search_content(query: str) -> List[Dict]:
    """Enhanced search with series support and Unicode normalization."""
    if not db or not query:
        return []

    # Normalize query for consistent matching
    query_lower = unicodedata.normalize('NFC', query.lower().strip())
    results = []

    # Direct IMDb ID search
    if query_lower.startswith('tt') and query_lower[2:].isdigit():
        if query_lower in db:
            return [{'type': 'direct', 'imdb_id': query_lower, 'entry': db[query_lower]}]

    # Search in main database
    for imdb_id, entry in db.items():
        # Normalize titles for consistent matching
        title = unicodedata.normalize('NFC', entry.get('title', '').lower())
        series_name = unicodedata.normalize('NFC', entry.get('series_name', '').lower()) if entry.get('series_name') else ''

        # Check various fields for matches
        if (query_lower in title or
            query_lower in series_name or
            any(word in title for word in query_lower.split()) or
            (series_name and any(word in series_name for word in query_lower.split()))):

            results.append({
                'type': 'match',
                'imdb_id': imdb_id,
                'entry': entry,
                'relevance': calculate_relevance(query_lower, title, series_name)
            })

    # Sort by relevance
    results.sort(key=lambda x: x.get('relevance', 0), reverse=True)
    return results[:20]  # Limit results

def calculate_relevance(query: str, title: str, series_name: str) -> int:
    """Calculate search relevance score with Unicode normalization."""
    # Ensure all inputs are normalized for consistent scoring
    norm_query = unicodedata.normalize('NFC', query)
    norm_title = unicodedata.normalize('NFC', title)
    norm_series_name = unicodedata.normalize('NFC', series_name)

    score = 0
    query_words = norm_query.split()

    # Exact title match gets highest score
    if norm_query in norm_title:
        score += 100
    if norm_series_name and norm_query in norm_series_name:
        score += 100

    # Word matches
    for word in query_words:
        if word in norm_title:
            score += 10
        if norm_series_name and word in norm_series_name:
            score += 10

    return score

def get_series_seasons(series_name: str) -> Dict[int, str]:
    """Get all seasons for a series."""
    if not series_name:
        return {}

    # Check series database first
    if series_name in series_db:
        return series_db[series_name]

    # Fallback to scanning main database
    seasons = {}
    for imdb_id, entry in db.items():
        if (entry.get('is_series') and entry.get('series_name') and
            entry['series_name'].lower() == series_name.lower()):
            season_num = entry.get('season_number', 1)
            seasons[season_num] = imdb_id

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

def format_movie_details(entry: Dict, imdb_id: str) -> (str, str):
    """Formats movie/series details into two parts: core info and synopsis."""

    def get_display_value(value):
        """Safely parse stringified dicts and return the 'name' value."""
        if isinstance(value, str):
            try:
                evaluated = ast.literal_eval(value)
                if isinstance(evaluated, dict) and 'name' in evaluated:
                    return evaluated['name']
            except (ValueError, SyntaxError):
                return value
        elif isinstance(value, dict) and 'name' in value:
            return value['name']
        return value

    title = entry.get('title', 'Unknown Title')
    year_val = str(entry.get('year', ''))

    # Prevent duplicate year in title
    if year_val and f"({year_val})" not in title:
        title_with_year = f"{title} ({year_val})"
    else:
        title_with_year = title

    core_details_message = f"🎬 **{title_with_year}**\n\n"

    details = []
    fields_to_format = [
        ("msone_release", "🆔 **MSOne Release:**"),
        ("language", "🗣️ **Language:**"),
        ("director", "🎬 **Director:**"),
        ("genre", "🎭 **Genre:**"),
        ("imdb_rating", "⭐ **IMDb Rating:**"),
        ("certification", "🏷️ **Certification:**"),
        ("translatedBy", "🌐 **Translator:**")
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

    # Series information
    if entry.get('is_series'):
        core_details_message += f"📺 **Series Information:**\n"
        if entry.get('season_number'):
            core_details_message += f"• Season: {entry['season_number']}\n"
        if entry.get('total_seasons'):
            core_details_message += f"• Total Seasons Available: {entry['total_seasons']}\n"
        core_details_message += "\n"

    # Synopsis
    synopsis_text = ""
    if entry.get('descriptionMalayalam') and entry['descriptionMalayalam'] != 'No description available':
        synopsis_text = f"📖 **Synopsis:**\n{entry['descriptionMalayalam']}"

    return core_details_message, synopsis_text

def create_detail_keyboard(entry: Dict, imdb_id: str) -> Dict:
    """Create keyboard for movie detail page."""
    keyboard = []

    # Download button
    if entry.get('srtURL'):
        keyboard.append([{
            'text': '📥 Download Subtitle',
            'callback_data': f"download_{imdb_id}"
        }])

    # IMDb link
    if entry.get('imdbURL'):
        keyboard.append([{
            'text': '🎬 View on IMDb',
            'url': entry['imdbURL']
        }])

    # Source and close buttons
    keyboard_row = []
    
    # Add source button if source_url is available
    if entry.get('source_url'):
        keyboard_row.append({
            'text': '🔗 Source',
            'url': entry['source_url']
        })
    else:
        # Fallback to back button if no source URL
        keyboard_row.append({
            'text': '🔙 Back to Search', 
            'callback_data': 'back_search'
        })
    
    # Add close button
    keyboard_row.append({'text': ' Close', 'callback_data': 'menu_close'})

    keyboard.append(keyboard_row)

    return {'inline_keyboard': keyboard}

async def handle_callback_query(callback_data: str, message_data: dict, chat_id: str) -> Dict:
    """Handle callback query from inline keyboards."""
    try:
        logger.info(f"Handling callback query: {callback_data}")
        if callback_data.startswith('menu_'):
            menu_type = callback_data.replace('menu_', '')
            logger.info(f"Menu type: {menu_type}")

            if menu_type == 'home':
                logger.info("Processing home menu")
                return {
                    'method': 'editMessageText',
                    'text': WELCOME_MESSAGE,
                    'reply_markup': create_menu_keyboard('home'),
                    'parse_mode': 'Markdown',
                    'chat_id': chat_id,
                    'message_id': message_data.get('message_id')
                }
            elif menu_type == 'about':
                logger.info("Processing about menu")
                # Check if ABOUT_MESSAGE has any issues
                logger.info(f"ABOUT_MESSAGE length: {len(ABOUT_MESSAGE)}")
                return {
                    'method': 'editMessageText',
                    'text': ABOUT_MESSAGE,
                    'reply_markup': create_menu_keyboard('about'),
                    'parse_mode': 'Markdown',
                    'chat_id': chat_id,
                    'message_id': message_data.get('message_id')
                }
            elif menu_type == 'help':
                logger.info("Processing help menu")
                return {
                    'method': 'editMessageText',
                    'text': HELP_MESSAGE,
                    'reply_markup': create_menu_keyboard('help'),
                    'parse_mode': 'Markdown',
                    'chat_id': chat_id,
                    'message_id': message_data.get('message_id')
                }
            elif menu_type == 'tos':
                logger.info("Processing tos menu")
                return {
                    'method': 'editMessageText',
                    'text': TOS_MESSAGE,
                    'reply_markup': create_menu_keyboard('tos'),
                    'parse_mode': 'Markdown',
                    'chat_id': chat_id,
                    'message_id': message_data.get('message_id')
                }
            elif menu_type == 'close':
                logger.info("Processing close menu for a single message")
                return {
                    'method': 'deleteMessage',
                    'chat_id': chat_id,
                    'message_id': message_data.get('message_id')
                }

        elif callback_data.startswith('close_'):
            logger.info("Processing custom close menu for two messages")
            try:
                parts = callback_data.split('_')
                photo_id = int(parts[1])
                synopsis_id = int(parts[2])
                return {
                    'method': 'delete_both',
                    'chat_id': chat_id,
                    'main_message_id': synopsis_id,
                    'reply_to_message_id': photo_id  # Reusing the key for the photo id
                }
            except (IndexError, ValueError) as e:
                logger.error(f"Error parsing close callback data: {callback_data} - {e}")
                # Fallback to deleting just the current message
                return {
                    'method': 'deleteMessage',
                    'chat_id': chat_id,
                    'message_id': message_data.get('message_id')
                }

        elif callback_data.startswith('view_'):
            imdb_id = callback_data.replace('view_', '')
            if imdb_id in db:
                entry = db[imdb_id]
                keyboard = create_detail_keyboard(entry, imdb_id)
                poster_url = entry.get('posterMalayalam')

                # If there's a poster, we need to delete the current message and send a new one with photo
                if poster_url and poster_url.startswith('https'):
                    logger.info(f"Movie has poster, will delete and resend with photo for {imdb_id}")
                    # Return a special response that indicates we need to delete and resend
                    return {
                        'method': 'delete_and_resend',
                        'chat_id': chat_id,
                        'message_id': message_data.get('message_id'),
                        'entry': entry,
                        'imdb_id': imdb_id
                    }
                else:
                    # No poster, so send a single combined text message
                    logger.info(f"No poster for {imdb_id}, editing message with text only")
                    core_details, synopsis = format_movie_details(entry, imdb_id)
                    full_text = core_details
                    if synopsis:
                        full_text += f"\n{synopsis}"

                    return {
                        'method': 'editMessageText',
                        'chat_id': chat_id,
                        'message_id': message_data.get('message_id'),
                        'text': full_text,
                        'reply_markup': keyboard,
                        'parse_mode': 'Markdown'
                    }
            else:
                return {
                    'method': 'answerCallbackQuery',
                    'text': 'Movie not found in database.',
                    'show_alert': True
                }

        elif callback_data.startswith('download_'):
            imdb_id = callback_data.replace('download_', '')
            if imdb_id in db:
                entry = db[imdb_id]
                download_url = entry.get('srtURL')

                if download_url:
                    # Send a preparing message to get a message_id for status updates
                    status_message = await send_telegram_message({
                        'chat_id': chat_id,
                        'text': '⏳ Preparing to download...',
                        'parse_mode': 'Markdown'
                    })
                    status_message_id = status_message.get('result', {}).get('message_id')

                    if status_message_id:
                        # Start the download in the background
                        asyncio.create_task(download_and_upload_subtitle(
                            download_url,
                            chat_id,
                            entry.get('title', 'subtitle'),
                            status_message_id
                        ))
                        # Immediately confirm to the user that the download has started
                        return {
                            'method': 'answerCallbackQuery',
                            'text': 'Download started! You will receive the file shortly.',
                            'show_alert': False
                        }
                    else:
                        return {
                             'method': 'answerCallbackQuery',
                             'text': 'Could not start download process. Please try again.',
                             'show_alert': True
                        }
                else:
                    return {
                        'method': 'answerCallbackQuery',
                        'text': 'Download link not available for this entry.',
                        'show_alert': True
                    }

        elif callback_data == 'back_search':
            return {
                'method': 'editMessageText',
                'text': '🔍 Send me a movie or series name to search for subtitles.',
                'reply_markup': {'inline_keyboard': [[{'text': ' Close', 'callback_data': 'menu_close'}]]}
            }

        # Default response
        return {
            'method': 'answerCallbackQuery',
            'text': 'Action not recognized.',
            'show_alert': False
        }

    except Exception as e:
        logger.error(f"Error handling callback query: {e}")
        return {
            'method': 'answerCallbackQuery',
            'text': 'An error occurred. Please try again.',
            'show_alert': True
        }

async def handle_telegram_message(message_data: dict) -> Dict:
    """Handle Telegram messages with enhanced features."""
    try:
        # Handle callback queries
        if 'callback_query' in message_data:
            callback = message_data['callback_query']
            callback_data = callback.get('data', '')
            chat_id = callback['message']['chat']['id']
            message_id = callback['message']['message_id']
            user_id = callback['from']['id']
            
            # Track user
            await add_user(user_id)

            response = await handle_callback_query(callback_data, callback['message'], str(chat_id))
            # The chat_id and message_id are now added in handle_callback_query
            if response:
                # Handle special case for delete_and_resend (movies with posters)
                if response.get('method') == 'delete_and_resend':
                    # Delete the current message
                    await send_telegram_message({
                        'method': 'deleteMessage',
                        'chat_id': response['chat_id'],
                        'message_id': response['message_id']
                    })

                    # --- New "Send-Send-Edit" Flow ---
                    entry = response['entry']
                    imdb_id = response['imdb_id']
                    poster_url = entry.get('posterMalayalam')
                    core_details, synopsis = format_movie_details(entry, imdb_id)

                    # 1. Send the photo message
                    photo_message = await send_telegram_message({
                        'method': 'sendPhoto',
                        'chat_id': response['chat_id'],
                        'photo': poster_url,
                        'caption': core_details,
                        'parse_mode': 'Markdown'
                    })
                    photo_message_id = photo_message.get('result', {}).get('message_id')

                    # 2. Send the synopsis message (without keyboard)
                    synopsis_message = await send_telegram_message({
                        'method': 'sendMessage',
                        'chat_id': response['chat_id'],
                        'text': synopsis if synopsis else "No synopsis available.",
                        'parse_mode': 'Markdown'
                    })
                    synopsis_message_id = synopsis_message.get('result', {}).get('message_id')

                    # 3. Dynamically create keyboard and edit the synopsis message
                    if photo_message_id and synopsis_message_id:
                        # Re-create the keyboard with a custom close button
                        keyboard_data = create_detail_keyboard(entry, imdb_id)
                        # Find the row with the 'menu_close' button and replace its callback_data
                        for row in keyboard_data['inline_keyboard']:
                            for button in row:
                                if button.get('callback_data') == 'menu_close':
                                    button['callback_data'] = f"close_{photo_message_id}_{synopsis_message_id}"
                                    break

                        await send_telegram_message({
                            'method': 'editMessageReplyMarkup',
                            'chat_id': response['chat_id'],
                            'message_id': synopsis_message_id,
                            'reply_markup': keyboard_data
                        })
                elif response.get('method') == 'delete_both':
                    await asyncio.gather(
                        send_telegram_message({
                            'method': 'deleteMessage',
                            'chat_id': response['chat_id'],
                            'message_id': response['main_message_id']
                        }),
                        send_telegram_message({
                            'method': 'deleteMessage',
                            'chat_id': response['chat_id'],
                            'message_id': response['reply_to_message_id']
                        })
                    )
                else:
                    # Send the response for all other methods (edit message, etc.)
                    await send_telegram_message(response)

                # Answer the callback query to remove the loading state
                await send_telegram_message({
                    'method': 'answerCallbackQuery',
                    'callback_query_id': callback['id']
                })
            return None

        # Handle regular messages
        message = message_data.get('message', {})
        text = message.get('text', '').strip()
        chat_id = message.get('chat', {}).get('id')
        user = message.get('from', {})
        user_id = user.get('id')

        if not chat_id or not user_id:
            return None

        # Track user
        await add_user(user_id)

        if not text:
            return None

        logger.info(f"Message: '{text}' from {user.get('username', 'unknown')} ({user_id})")

        # Admin commands
        if str(user_id) == OWNER_ID:
            if text == '/broadcast':
                # Handle broadcast command with reply
                return await broadcast_message(message, chat_id)
            elif text == '/broadcaststats':
                # Show broadcast statistics
                return {
                    'chat_id': chat_id,
                    'text': f"📊 **Broadcast Statistics**\n\n👥 **Total tracked users:** {len(tracked_users)}\n📈 **Users ready for broadcast:** {len(tracked_users)}",
                    'parse_mode': 'Markdown'
                }
            elif text == '/stats':
                # Show comprehensive bot statistics (admin only)
                total_movies = sum(1 for entry in db.values() if not entry.get('is_series'))
                total_series = len(series_db)
                total_episodes = sum(1 for entry in db.values() if entry.get('is_series'))

                stats_text = f"""📊 **Bot Statistics**

🎬 **Movies:** {total_movies:,}
📺 **Series:** {total_series:,}
📚 **Total Database:** {len(db):,} entries

👥 **Users:** {len(tracked_users):,} tracked
📈 **Broadcast Ready:** {len(tracked_users):,} users

🤖 **Bot Status:** Online
💾 **Last Updated:** {db.get('last_updated', 'Unknown') if db else 'No data'}"""
                return {
                    'chat_id': chat_id,
                    'text': stats_text,
                    'parse_mode': 'Markdown'
                }
            elif text == '/ahelp':
                # Admin help command
                admin_help_text = """🔧 **Admin Commands**

**📢 Broadcasting:**
• `/broadcast` - Reply to any message to broadcast it to all users
• `/broadcaststats` - Show broadcast statistics

**📊 Statistics:**
• `/stats` - Show comprehensive bot statistics

**ℹ️ Regular Commands:**
• `/start` - Start the bot
• `/help` - Show help information
• `/about` - Show about information
• `/tos` - Show terms of service

**🔍 Search:**
• Send any movie/series name to search for subtitles

**📝 Note:**
All admin commands are restricted to bot owner only."""
                return {
                    'chat_id': chat_id,
                    'text': admin_help_text,
                    'parse_mode': 'Markdown'
                }

        # Regular commands
        if text.startswith('/start'):
            user_info = user.get('username', "unknown")
            log_text = f"✅ User @{user_info} ({user_id}) started the bot."
            await send_log(log_text)
            return {
                'chat_id': chat_id,
                'text': WELCOME_MESSAGE,
                'reply_markup': create_menu_keyboard('home'),
                'parse_mode': 'Markdown'
            }
        elif text.startswith('/help'):
            return {
                'chat_id': chat_id,
                'text': HELP_MESSAGE,
                'reply_markup': create_menu_keyboard('help'),
                'parse_mode': 'Markdown'
            }
        elif text.startswith('/about'):
            return {
                'chat_id': chat_id,
                'text': ABOUT_MESSAGE,
                'reply_markup': create_menu_keyboard('about'),
                'parse_mode': 'Markdown'
            }
        elif text.startswith('/tos'):
            return {
                'chat_id': chat_id,
                'text': TOS_MESSAGE,
                'reply_markup': create_menu_keyboard('tos'),
                'parse_mode': 'Markdown'
            }
        else:
            # Search query
            if len(text) < 2:
                return {
                    'chat_id': chat_id,
                    'text': "Please send a movie name with at least 2 characters."
                }
            elif len(text) > 100:
                return {
                    'chat_id': chat_id,
                    'text': "Movie name too long. Please use a shorter search term."
                }
            else:
                user_info = user.get('username', "unknown")
                log_text = f"🔍 User @{user_info} ({user_id}) searched for: `{text}`"
                await send_log(log_text)
                # Perform search
                results = search_content(text)

                if not results:
                    return {
                        'chat_id': chat_id,
                        'text': f'😔 No subtitles found for "{text}"\n\nTry different keywords or check spelling.',
                        'parse_mode': 'Markdown'
                    }

                # If single direct match, show details immediately
                if len(results) == 1 and results[0]['type'] == 'direct':
                    entry = results[0]['entry']
                    detail_text = format_movie_details(entry, results[0]['imdb_id'])
                    keyboard = create_detail_keyboard(entry, results[0]['imdb_id'])

                    poster_url = entry.get('posterMalayalam')
                    if poster_url:
                        return {
                            'method': 'sendPhoto',
                            'chat_id': chat_id,
                            'photo': poster_url,
                            'caption': detail_text,
                            'reply_markup': keyboard,
                            'parse_mode': 'Markdown'
                        }
                    else:
                        return {
                            'chat_id': chat_id,
                            'text': detail_text,
                            'reply_markup': keyboard,
                            'parse_mode': 'Markdown'
                        }

                # New, corrected logic for series handling
                if results:
                    # The first result is the most relevant one.
                    most_relevant_entry = results[0]['entry']

                    if most_relevant_entry.get('is_series'):
                        # Get the base name of the most relevant series
                        base_name_to_show = get_base_series_name(most_relevant_entry.get('title', ''))

                        if base_name_to_show:
                            # Group all seasons for *only this specific series* from the results
                            seasons_for_this_series = {}
                            for result in results:
                                entry = result['entry']
                                if entry.get('is_series'):
                                    base_name = get_base_series_name(entry.get('title', ''))
                                    if base_name.lower() == base_name_to_show.lower():
                                        season_num = entry.get('season_number', 1)
                                        if season_num not in seasons_for_this_series:
                                            seasons_for_this_series[str(season_num)] = result['imdb_id']

                            # If we found multiple seasons for the most relevant series, show selector
                            if len(seasons_for_this_series) > 1:
                                keyboard = create_series_seasons_keyboard(seasons_for_this_series)
                                return {
                                    'chat_id': chat_id,
                                    'text': f"📺 **{base_name_to_show}**\n\nI found {len(seasons_for_this_series)} seasons for this series. Please select one:",
                                    'reply_markup': keyboard,
                                    'parse_mode': 'Markdown'
                                }

                # Show search results
                keyboard = create_search_results_keyboard(results)
                return {
                    'chat_id': chat_id,
                    'text': f"🔍 **Here's what I found for '{text}':**\n\nSelect a title to view details:",
                    'reply_markup': keyboard,
                    'parse_mode': 'Markdown'
                }

    except Exception as e:
        logger.error(f"Error handling message: {e}")
        return {
            'chat_id': chat_id,
            'text': "An error occurred. Please try again later."
        }

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
    await load_databases()
    await load_tracked_users()
    asyncio.create_task(periodic_save_users())

    # Set webhook if token is available
    # if TOKEN:
    #     import aiohttp

    #     try:
    #         base_url = os.environ.get("RENDER_EXTERNAL_URL", "https://subto-mso-tga.onrender.com")
    #         webhook_url = f"{base_url}/telegram"

    #         url = f"https://api.telegram.org/bot{TOKEN}/setWebhook"
    #         data = {
    #             "url": webhook_url,
    #             "secret_token": WEBHOOK_SECRET,
    #             "drop_pending_updates": True
    #         }

    #         async with aiohttp.ClientSession() as session:
    #             async with session.post(url, json=data) as resp:
    #                 if resp.status == 200:
    #                     logger.info(f"Webhook set to {webhook_url}")

    #                     # Notify owner
    #                     if OWNER_ID:
    #                         await send_telegram_message({
    #                             'chat_id': OWNER_ID,
    #                             'text': '🟢 **Enhanced Bot v2.0 is Online!**\n\n✅ Database loaded successfully\n✅ All features activated\n✅ Ready to serve users',
    #                             'parse_mode': 'Markdown'
    #                         })
    #                 else:
    #                     logger.error(f"Failed to set webhook: {resp.status}")
    #     except Exception as e:
    #         logger.error(f"Error setting webhook: {e}")

# --- API Endpoints ---
@app.get("/")
async def root():
    return {
        "status": "ok",
        "message": "Enhanced Subtitle Search Bot API v2.0",
        "database_entries": len(db),
        "series_count": len(series_db)
    }

@app.head("/")
async def head_root():
    """
    Handles HEAD requests for the root path, used by UptimeRobot.
    """
    return Response(status_code=HTTPStatus.OK)

@app.get("/healthz")
async def health_check():
    return {
        "status": "healthy",
        "database_loaded": len(db) > 0,
        "series_db_loaded": len(series_db) > 0
    }

@app.get("/api/subtitles")
async def api_search(
    query: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(10, ge=1, le=50)
):
    """Enhanced API search with series support."""
    try:
        results = search_content(query)

        if not results:
            return {
                "query": query,
                "count": 0,
                "results": [],
                "message": "No results found"
            }

        limited_results = results[:limit]
        formatted_results = []

        for result in limited_results:
            entry = result['entry'].copy()
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
