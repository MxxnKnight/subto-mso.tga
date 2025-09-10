import os
import json
import logging
import asyncio
import zipfile
import tempfile
from http import HTTPStatus
from typing import Dict, Any, List
from urllib.parse import urlparse
import re

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

    # Load main database
    try:
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            db = json.load(f)
            logger.info(f"Loaded main database: {len(db)} entries from {DB_FILE}")
    except Exception as e:
        logger.error(f"Error loading main database from {DB_FILE}: {e}")
        db = {}

    # Load series database
    try:
        with open(SERIES_DB_FILE, 'r', encoding='utf-8') as f:
            series_db = json.load(f)
            logger.info(f"Loaded series database: {len(series_db)} series from {SERIES_DB_FILE}")
    except Exception as e:
        logger.warning(f"Series database not found at {SERIES_DB_FILE}: {e}")
        series_db = {}

def search_content(query: str) -> List[Dict]:
    """Enhanced search with series support."""
    if not db or not query:
        return []

    query_lower = query.lower().strip()
    results = []

    # Direct IMDb ID search
    if query_lower.startswith('tt') and query_lower[2:].isdigit():
        if query_lower in db:
            return [{'type': 'direct', 'imdb_id': query_lower, 'entry': db[query_lower]}]

    # Search in main database
    for imdb_id, entry in db.items():
        title = entry.get('title', '').lower()
        series_name = entry.get('series_name', '').lower() if entry.get('series_name') else ''

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
    """Calculate search relevance score."""
    score = 0
    query_words = query.split()

    # Exact title match gets highest score
    if query in title:
        score += 100
    if series_name and query in series_name:
        score += 100

    # Word matches
    for word in query_words:
        if word in title:
            score += 10
        if series_name and word in series_name:
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

async def download_and_upload_subtitle(download_url: str, chat_id: str, title: str, status_message_id: int):
    """Download, upload, and provide status updates for a subtitle file."""
    if not all([download_url, TOKEN, status_message_id]):
        return

    import aiohttp
    import aiofiles

    error_occurred = False
    try:
        await send_telegram_message({
            'method': 'editMessageText',
            'chat_id': chat_id,
            'message_id': status_message_id,
            'text': f"📥 Downloading subtitle for **{title}**...",
            'parse_mode': 'Markdown'
        })

        with tempfile.TemporaryDirectory() as temp_dir:
            headers = {'User-Agent': 'Mozilla/5.0'}
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(download_url, timeout=60) as resp:
                    resp.raise_for_status()
                    content = await resp.read()

                    filename = f"{title.replace(' ', '_')}.zip"
                    if 'content-disposition' in resp.headers:
                        cd = resp.headers['content-disposition']
                        filename_match = re.search(r'filename="([^"]+)"', cd)
                        if filename_match:
                            filename = filename_match.group(1)
                    
                    file_path = os.path.join(temp_dir, filename)
                    async with aiofiles.open(file_path, 'wb') as f:
                        await f.write(content)

                    await send_telegram_message({
                        'method': 'editMessageText',
                        'chat_id': chat_id,
                        'message_id': status_message_id,
                        'text': f"📤 Uploading **{title}**...",
                        'parse_mode': 'Markdown'
                    })

                    if filename.lower().endswith('.zip'):
                        await upload_zip_contents(file_path, chat_id, title)
                    else:
                        await upload_single_file(file_path, chat_id, filename)

    except Exception as e:
        error_occurred = True
        logger.error(f"Error in download/upload process: {e}")
        await send_telegram_message({
            'method': 'editMessageText',
            'chat_id': chat_id,
            'message_id': status_message_id,
            'text': "❌ **Download Failed**\nAn error occurred while fetching the subtitle. Please try again later.",
            'parse_mode': 'Markdown'
        })
    finally:
        if status_message_id:
            if error_occurred:
                await asyncio.sleep(5)
            await send_telegram_message({
                'method': 'deleteMessage',
                'chat_id': chat_id,
                'message_id': status_message_id
            })

async def upload_zip_contents(zip_path: str, chat_id: str, title: str) -> bool:
    """Extract and upload all files from zip."""
    try:
        with tempfile.TemporaryDirectory() as extract_dir:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)

            # Upload each extracted file
            for root, dirs, files in os.walk(extract_dir):
                for file in files:
                    if file.lower().endswith(('.srt', '.ass', '.ssa', '.vtt')):
                        file_path = os.path.join(root, file)
                        await upload_single_file(file_path, chat_id, file)

            return True
    except Exception as e:
        logger.error(f"Error processing zip file: {e}")
        return False

async def upload_single_file(file_path: str, chat_id: str, filename: str) -> bool:
    """Upload single file to Telegram."""
    import aiohttp

    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendDocument"

        async with aiohttp.ClientSession() as session:
            with open(file_path, 'rb') as f:
                data = aiohttp.FormData()
                data.add_field('chat_id', chat_id)
                data.add_field('document', f, filename=filename)
                data.add_field('caption', f'📁 {filename}')

                async with session.post(url, data=data) as resp:
                    return resp.status == 200

    except Exception as e:
        logger.error(f"Error uploading file: {e}")
        return False

def create_menu_keyboard(current_menu: str) -> Dict:
    """Create inline keyboard for menus."""
    keyboards = {
        'home': [
            [{'text': 'ℹ️ About', 'callback_data': 'menu_about'}],
            [{'text': '🆘 Help', 'callback_data': 'menu_help'}],
            [{'text': '📋 Terms of Service', 'callback_data': 'menu_tos'}],
            [{'text': '❌ Close', 'callback_data': 'menu_close'}]
        ],
        'about': [
            [{'text': '🏠 Home', 'callback_data': 'menu_home'}],
            [{'text': '🆘 Help', 'callback_data': 'menu_help'}],
            [{'text': '📋 Terms of Service', 'callback_data': 'menu_tos'}],
            [{'text': '❌ Close', 'callback_data': 'menu_close'}]
        ],
        'help': [
            [{'text': '🏠 Home', 'callback_data': 'menu_home'}],
            [{'text': 'ℹ️ About', 'callback_data': 'menu_about'}],
            [{'text': '📋 Terms of Service', 'callback_data': 'menu_tos'}],
            [{'text': '❌ Close', 'callback_data': 'menu_close'}]
        ],
        'tos': [
            [{'text': '🏠 Home', 'callback_data': 'menu_home'}],
            [{'text': 'ℹ️ About', 'callback_data': 'menu_about'}],
            [{'text': '🆘 Help', 'callback_data': 'menu_help'}],
            [{'text': '❌ Close', 'callback_data': 'menu_close'}]
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

    keyboard.append([{'text': '❌ Close', 'callback_data': 'menu_close'}])
    return {'inline_keyboard': keyboard}

def create_series_seasons_keyboard(seasons: Dict[int, str]) -> Dict:
    """Create keyboard for series seasons."""
    keyboard = []

    for season_num in sorted(seasons.keys()):
        keyboard.append([{
            'text': f'Season {season_num}',
            'callback_data': f"view_{seasons[season_num]}"
        }])

    keyboard.append([{'text': '❌ Close', 'callback_data': 'menu_close'}])
    return {'inline_keyboard': keyboard}

def format_movie_details(entry: Dict, imdb_id: str) -> str:
    """Format movie/series details for display."""
    title = entry.get('title', 'Unknown Title')
    year = f" ({entry['year']})" if entry.get('year') else ""

    # Main title
    message = f"🎬 **{title}{year}**\n\n"

    # MSOne release number
    if entry.get('msone_release_number'):
        message += f"🆔 MSOne Release: `{entry['msone_release_number']}`\n\n"

    # Movie details
    details = []
    if entry.get('language'):
        details.append(f"🗣️ **Language:** {entry['language']}")
    if entry.get('director') and entry['director'] != 'Unknown':
        details.append(f"🎬 **Director:** {entry['director']}")
    if entry.get('genre') and entry['genre'] != 'Unknown':
        details.append(f"🎭 **Genre:** {entry['genre']}")
    if entry.get('imdb_rating') and entry['imdb_rating'] != 'N/A':
        details.append(f"⭐ **IMDb Rating:** {entry['imdb_rating']}")
    if entry.get('certification') and entry['certification'] != 'Not Rated':
        details.append(f"🏷️ **Certification:** {entry['certification']}")

    if entry.get('translatedBy') and entry['translatedBy']['name'] != 'Unknown':
        details.append(f"🌐 **Translator:** {entry['translatedBy']['name']}")

    if details:
        message += "\n".join(details) + "\n\n"

    # Series information
    if entry.get('is_series'):
        message += f"📺 **Series Information:**\n"
        if entry.get('season_number'):
            message += f"• Season: {entry['season_number']}\n"
        if entry.get('total_seasons'):
            message += f"• Total Seasons Available: {entry['total_seasons']}\n"
        message += "\n"

    # Synopsis
    if entry.get('descriptionMalayalam') and entry['descriptionMalayalam'] != 'No description available':
        message += f"📖 **Synopsis:**\n{entry['descriptionMalayalam']}\n\n"

    return message

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

    # Back and close buttons
    keyboard.append([
        {'text': '🔙 Back to Search', 'callback_data': 'back_search'},
        {'text': '❌ Close', 'callback_data': 'menu_close'}
    ])

    return {'inline_keyboard': keyboard}

async def handle_callback_query(callback_data: str, message_data: dict, chat_id: str) -> Dict:
    """Handle callback query from inline keyboards."""
    try:
        if callback_data.startswith('menu_'):
            menu_type = callback_data.replace('menu_', '')

            if menu_type == 'home':
                return {
                    'method': 'editMessageText',
                    'text': WELCOME_MESSAGE,
                    'reply_markup': create_menu_keyboard('home'),
                    'parse_mode': 'Markdown'
                }
            elif menu_type == 'about':
                return {
                    'method': 'editMessageText',
                    'text': ABOUT_MESSAGE,
                    'reply_markup': create_menu_keyboard('about'),
                    'parse_mode': 'Markdown'
                }
            elif menu_type == 'help':
                return {
                    'method': 'editMessageText',
                    'text': HELP_MESSAGE,
                    'reply_markup': create_menu_keyboard('help'),
                    'parse_mode': 'Markdown'
                }
            elif menu_type == 'tos':
                return {
                    'method': 'editMessageText',
                    'text': TOS_MESSAGE,
                    'reply_markup': create_menu_keyboard('tos'),
                    'parse_mode': 'Markdown'
                }
            elif menu_type == 'close':
                return {
                    'method': 'deleteMessage'
                }

        elif callback_data.startswith('view_'):
            imdb_id = callback_data.replace('view_', '')
            if imdb_id in db:
                chat_id_from_message = message_data.get('chat', {}).get('id')
                message_id = message_data.get('message_id')

                # First, delete the existing message to avoid media/text mismatch errors.
                await send_telegram_message({
                    'chat_id': chat_id_from_message,
                    'message_id': message_id,
                    'method': 'deleteMessage'
                })

                # Now, prepare and send a new message.
                entry = db[imdb_id]
                detail_text = format_movie_details(entry, imdb_id)
                keyboard = create_detail_keyboard(entry, imdb_id)
                poster_url = entry.get('posterMalayalam')

                new_message_payload = {
                    'chat_id': chat_id_from_message,
                    'reply_markup': keyboard,
                    'parse_mode': 'Markdown'
                }

                if poster_url and poster_url.startswith('https'):
                    logger.info(f"Sending poster for {imdb_id}")
                    new_message_payload['photo'] = poster_url
                    new_message_payload['caption'] = detail_text
                else:
                    logger.warning(f"No valid poster for {imdb_id}, sending text only.")
                    new_message_payload['text'] = detail_text
                    new_message_payload['disable_web_page_preview'] = False

                await send_telegram_message(new_message_payload)

            # Since we handled everything manually, we return None to stop the parent function.
            return None

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
                'reply_markup': {'inline_keyboard': [[{'text': '❌ Close', 'callback_data': 'menu_close'}]]}
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

            response = await handle_callback_query(callback_data, callback['message'], str(chat_id))
            if response:
                response['chat_id'] = chat_id
                response['message_id'] = message_id
                return response

            return None

        # Handle regular messages
        message = message_data.get('message', {})
        text = message.get('text', '').strip()
        chat_id = message.get('chat', {}).get('id')
        user = message.get('from', {})
        user_id = user.get('id')

        if not chat_id or not text:
            return None

        logger.info(f"Message: '{text}' from {user.get('username', 'unknown')} ({user_id})")

        # Admin commands
        if str(user_id) == OWNER_ID:
            if text.startswith('/broadcast '):
                broadcast_text = text.replace('/broadcast ', '')
                # TODO: Implement broadcast functionality
                return {
                    'chat_id': chat_id,
                    'text': f"Broadcast feature coming soon!\n\nMessage to broadcast: {broadcast_text}",
                    'parse_mode': 'Markdown'
                }
            elif text == '/scrape_start':
                return {
                    'chat_id': chat_id,
                    'text': "🔄 **Scraper Control**\n\nScraper start/stop functionality will be implemented with background tasks.",
                    'parse_mode': 'Markdown',
                    'reply_markup': {
                        'inline_keyboard': [
                            [{'text': '▶️ Start Scraper', 'callback_data': 'scraper_start'}],
                            [{'text': '⏹️ Stop Scraper', 'callback_data': 'scraper_stop'}],
                            [{'text': '📊 Scraper Status', 'callback_data': 'scraper_status'}],
                            [{'text': '❌ Close', 'callback_data': 'menu_close'}]
                        ]
                    }
                }

        # Regular commands
        if text.startswith('/start'):
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
        elif text.startswith('/stats'):
            total_movies = sum(1 for entry in db.values() if not entry.get('is_series'))
            total_series = len(series_db)
            total_episodes = sum(1 for entry in db.values() if entry.get('is_series'))

            stats_text = f"""📊 **Bot Statistics**

🎬 **Movies:** {total_movies:,}
📺 **Series:** {total_series:,}
🎭 **Episodes:** {total_episodes:,}
📚 **Total Database:** {len(db):,} entries

🤖 **Bot Status:** Online
💾 **Last Updated:** {db.get('last_updated', 'Unknown') if db else 'No data'}
"""
            return {
                'chat_id': chat_id,
                'text': stats_text,
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

                # Check if it's a series search (multiple seasons)
                series_seasons = {}
                series_name = None

                for result in results[:5]:  # Check first 5 results
                    entry = result['entry']
                    if entry.get('is_series') and entry.get('series_name'):
                        current_series = entry['series_name']
                        if not series_name:
                            series_name = current_series

                        if current_series.lower() == series_name.lower():
                            season_num = entry.get('season_number', 1)
                            series_seasons[season_num] = result['imdb_id']

                # If multiple seasons found, show season selector
                if len(series_seasons) > 1:
                    keyboard = create_series_seasons_keyboard(series_seasons)
                    return {
                        'chat_id': chat_id,
                        'text': f"📺 **{series_name}**\n\nFound {len(series_seasons)} seasons available. Select a season:",
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
        return False

    import aiohttp

    # Determine method
    method = data.pop('method', 'sendMessage')
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"

    try:
        async with aiohttp.ClientSession() as session:
            if 'photo' in data and method == 'sendMessage':
                # Send photo with caption
                form_data = aiohttp.FormData()
                for key, value in data.items():
                    if key == 'reply_markup' and isinstance(value, dict):
                        form_data.add_field(key, json.dumps(value))
                    else:
                        form_data.add_field(key, str(value))

                async with session.post(url.replace('sendMessage', 'sendPhoto'), data=form_data) as resp:
                    success = resp.status == 200
                    if not success:
                        logger.error(f"Failed to send photo: {resp.status}")
                    return success
            else:
                # Regular API call
                async with session.post(url, json=data) as resp:
                    success = resp.status == 200
                    if not success:
                        logger.error(f"Failed to send message: {resp.status} - {await resp.text()}")
                    return success

    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return False

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
    load_databases()

    # Set webhook if token is available
    if TOKEN:
        import aiohttp

        try:
            base_url = os.environ.get("RENDER_EXTERNAL_URL", "https://subto-mso-tga.onrender.com")
            webhook_url = f"{base_url}/telegram"

            url = f"https://api.telegram.org/bot{TOKEN}/setWebhook"
            data = {
                "url": webhook_url,
                "secret_token": WEBHOOK_SECRET,
                "drop_pending_updates": True
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=data) as resp:
                    if resp.status == 200:
                        logger.info(f"Webhook set to {webhook_url}")

                        # Notify owner
                        if OWNER_ID:
                            await send_telegram_message({
                                'chat_id': OWNER_ID,
                                'text': '🟢 **Enhanced Bot v2.0 is Online!**\n\n✅ Database loaded successfully\n✅ All features activated\n✅ Ready to serve users',
                                'parse_mode': 'Markdown'
                            })
                    else:
                        logger.error(f"Failed to set webhook: {resp.status}")
        except Exception as e:
            logger.error(f"Error setting webhook: {e}")

# --- API Endpoints ---
@app.get("/")
async def root():
    return {
        "status": "ok",
        "message": "Enhanced Subtitle Search Bot API v2.0",
        "database_entries": len(db),
        "series_count": len(series_db)
    }

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
