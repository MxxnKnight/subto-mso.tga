import os
import json
import logging
import asyncio
import zipfile
import tempfile
import time
from http import HTTPStatus
from typing import Dict, Any, List
from urllib.parse import urlparse
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
    
    # Load main database
    try:
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            db = json.load(f)
            logger.info(f"Loaded main database: {len(db)} entries")
    except Exception as e:
        logger.error(f"Error loading main database: {e}")
        db = {}
    
    # Load series database
    try:
        with open(SERIES_DB_FILE, 'r', encoding='utf-8') as f:
            series_db = json.load(f)
            logger.info(f"Loaded series database: {len(series_db)} series")
    except Exception as e:
        logger.warning(f"Series database not found: {e}")
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

async def download_and_upload_subtitle(download_url: str, chat_id: str, title: str, source_url: str) -> bool:
    """Download subtitle file and upload to Telegram."""
    if not download_url or not TOKEN:
        logger.warning(f"Download aborted: Missing download_url or TOKEN for chat_id {chat_id}")
        return False
    
    import aiohttp
    import aiofiles
    
    logger.info(f"[ChatID: {chat_id}] Starting download for '{title}' from URL: {download_url}")

    status_message_id = None
    try:
        # Send initial message and store its ID
        status_message = await send_telegram_message({
            'chat_id': chat_id,
            'text': f"📥 Downloading subtitle for **{title}**...",
            'parse_mode': 'Markdown'
        })
        if status_message and status_message.get('ok'):
            status_message_id = status_message['result']['message_id']

        # Create temp directory
        with tempfile.TemporaryDirectory() as temp_dir:
            logger.info(f"[ChatID: {chat_id}] Created temporary directory: {temp_dir}")

            # Download file with proper headers
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Referer': 'https://malayalamsubtitles.org/'
            }
            
            async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as session:
                logger.info(f"[ChatID: {chat_id}] Attempting to GET download URL.")
                async with session.get(download_url) as resp:
                    if resp.status != 200:
                        error_body = await resp.text()
                        logger.error(f"[ChatID: {chat_id}] Download failed with status {resp.status}. Response: {error_body[:200]}")
                        await send_telegram_message({
                            'method': 'editMessageText',
                            'chat_id': chat_id,
                            'message_id': status_message_id,
                            'text': f"❌ Failed to download subtitle (Server returned HTTP {resp.status}). Please try again later."
                        })
                        return False # Keep the error message visible
                    
                    # Get filename from headers or URL
                    filename = f"{title.replace(' ', '_')}.zip" # Default to zip
                    if 'content-disposition' in resp.headers:
                        cd = resp.headers['content-disposition']
                        filename_match = re.search(r'filename\*?=(.+)', cd, re.IGNORECASE)
                        if filename_match:
                            raw_filename = filename_match.group(1).strip('"')
                            if "''" in raw_filename:
                                raw_filename = raw_filename.split("''")[-1]
                            filename = raw_filename
                    else:
                        parsed_url = urlparse(download_url)
                        url_filename = os.path.basename(parsed_url.path)
                        if '.' in url_filename:
                            filename = url_filename
                    
                    file_path = os.path.join(temp_dir, filename)
                    
                    # Save file
                    bytes_written = 0
                    async with aiofiles.open(file_path, 'wb') as f:
                        async for chunk in resp.content.iter_chunked(8192):
                            await f.write(chunk)
                            bytes_written += len(chunk)
                    
                    if bytes_written == 0:
                        logger.warning(f"[ChatID: {chat_id}] Downloaded file is empty.")
                        await send_telegram_message({'chat_id': chat_id, 'text': "❌ Downloaded file was empty."})
                        return False

                    logger.info(f"[ChatID: {chat_id}] File saved to {file_path}, size: {bytes_written} bytes.")
                    
                    # Update progress by editing the status message
                    if status_message_id:
                        await send_telegram_message({
                            'method': 'editMessageText',
                            'chat_id': chat_id,
                            'message_id': status_message_id,
                            'text': f"📤 Uploading subtitle file(s)..."
                        })
                    
                    # Process and upload the file
                    if filename.lower().endswith('.zip'):
                        return await upload_zip_contents(file_path, chat_id, title, source_url)
                    else:
                        return await upload_single_file(file_path, chat_id, filename, source_url)
    
    except asyncio.TimeoutError:
        logger.error(f"[ChatID: {chat_id}] Download timeout for {download_url}")
        await send_telegram_message({
            'chat_id': chat_id,
            'text': "⏰ Download timed out. The server took too long to respond. Please try again later."
        })
        return False
    except Exception as e:
        logger.exception(f"[ChatID: {chat_id}] An unexpected error occurred in download_and_upload_subtitle: {e}")
        await send_telegram_message({
            'chat_id': chat_id,
            'text': f"❌ An unexpected error occurred. The admin has been notified."
        })
        return False
    finally:
        # Clean up the status message after a short delay
        if status_message_id:
            await asyncio.sleep(5)
            await send_telegram_message({
                'method': 'deleteMessage',
                'chat_id': chat_id,
                'message_id': status_message_id
            })

async def upload_zip_contents(zip_path: str, chat_id: str, title: str, source_url: str) -> bool:
    """Extract and upload all files from zip."""
    logger.info(f"[ChatID: {chat_id}] Processing zip file: {zip_path}")
    try:
        with tempfile.TemporaryDirectory() as extract_dir:
            logger.info(f"[ChatID: {chat_id}] Extracting zip to {extract_dir}")
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
            
            # Find subtitle files
            subtitle_files = []
            for root, dirs, files in os.walk(extract_dir):
                for file in files:
                    if file.lower().endswith(('.srt', '.ass', '.ssa', '.vtt')):
                        subtitle_files.append(os.path.join(root, file))
            
            logger.info(f"[ChatID: {chat_id}] Found {len(subtitle_files)} subtitle file(s) in zip.")

            if not subtitle_files:
                await send_telegram_message({
                    'chat_id': chat_id,
                    'text': "🤷‍♂️ No subtitle files (.srt, .ass, etc.) were found inside the downloaded archive."
                })
                return False
            
            # Upload each file
            uploaded_count = 0
            for file_path in subtitle_files:
                filename = os.path.basename(file_path)
                if await upload_single_file(file_path, chat_id, filename, source_url):
                    uploaded_count += 1
                else:
                    logger.warning(f"[ChatID: {chat_id}] Failed to upload {filename} from zip.")
                await asyncio.sleep(1)  # Rate limiting
            
            logger.info(f"[ChatID: {chat_id}] Successfully uploaded {uploaded_count}/{len(subtitle_files)} files.")

            if uploaded_count > 0:
                await send_telegram_message({
                    'chat_id': chat_id,
                    'text': f"✅ Successfully uploaded {uploaded_count} subtitle file(s) for **{title}**."
                })
            
            return uploaded_count > 0

    except zipfile.BadZipFile:
        logger.error(f"[ChatID: {chat_id}] The downloaded file is not a valid zip archive.")
        await send_telegram_message({'chat_id': chat_id, 'text': "❌ The downloaded file was corrupted or not a valid zip file."})
        # Fallback: try to upload the file directly
        return await upload_single_file(zip_path, chat_id, os.path.basename(zip_path), source_url)
    except Exception as e:
        logger.exception(f"[ChatID: {chat_id}] Error processing zip file: {e}")
        await send_telegram_message({'chat_id': chat_id, 'text': "❌ An error occurred while processing the zip file."})
        return False

async def upload_single_file(file_path: str, chat_id: str, filename: str, source_url: str) -> bool:
    """Upload single file to Telegram."""
    import aiohttp
    
    logger.info(f"[ChatID: {chat_id}] Preparing to upload single file: {filename} from {file_path}")

    try:
        if not os.path.exists(file_path):
            logger.error(f"[ChatID: {chat_id}] File not found for upload: {file_path}")
            return False
        
        file_size = os.path.getsize(file_path)
        logger.info(f"[ChatID: {chat_id}] File size: {file_size} bytes.")
        if file_size > 49 * 1024 * 1024:  # 49MB limit for safety
            logger.warning(f"[ChatID: {chat_id}] File {filename} is too large ({file_size} bytes).")
            await send_telegram_message({
                'chat_id': chat_id,
                'text': f"❌ File **{filename}** is too large ({file_size / 1024 / 1024:.2f} MB). Telegram's limit is 50MB."
            })
            return False
        
        url = f"https://api.telegram.org/bot{TOKEN}/sendDocument"
        
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
            with open(file_path, 'rb') as f:
                data = aiohttp.FormData()
                data.add_field('chat_id', chat_id)
                data.add_field('document', f, filename=filename)
                
                # Create hyperlinked caption
                caption = f"[{filename}]({source_url})" if source_url else filename
                data.add_field('caption', caption)
                data.add_field('parse_mode', 'Markdown')

                logger.info(f"[ChatID: {chat_id}] Posting file to Telegram API with caption: {caption}")
                async with session.post(url, data=data) as resp:
                    success = resp.status == 200
                    if not success:
                        error_text = await resp.text()
                        logger.error(f"[ChatID: {chat_id}] Telegram upload failed with status {resp.status}: {error_text}")
                    else:
                        logger.info(f"[ChatID: {chat_id}] Successfully uploaded {filename} to Telegram.")
                    return success
    
    except Exception as e:
        logger.exception(f"[ChatID: {chat_id}] An unexpected error occurred during file upload: {e}")
        return False

def create_menu_keyboard(current_menu: str) -> Dict:
    """Create inline keyboard for menus with 2 buttons per row."""
    keyboards = {
        'home': [
            [{'text': 'ℹ️ About', 'callback_data': 'menu_about'}, {'text': '🆘 Help', 'callback_data': 'menu_help'}],
            [{'text': '📋 Terms of Service', 'callback_data': 'menu_tos'}, {'text': '❌ Close', 'callback_data': 'menu_close'}]
        ],
        'about': [
            [{'text': '🏠 Home', 'callback_data': 'menu_home'}, {'text': '🆘 Help', 'callback_data': 'menu_help'}],
            [{'text': '📋 Terms of Service', 'callback_data': 'menu_tos'}, {'text': '❌ Close', 'callback_data': 'menu_close'}]
        ],
        'help': [
            [{'text': '🏠 Home', 'callback_data': 'menu_home'}, {'text': 'ℹ️ About', 'callback_data': 'menu_about'}],
            [{'text': '📋 Terms of Service', 'callback_data': 'menu_tos'}, {'text': '❌ Close', 'callback_data': 'menu_close'}]
        ],
        'tos': [
            [{'text': '🏠 Home', 'callback_data': 'menu_home'}, {'text': 'ℹ️ About', 'callback_data': 'menu_about'}],
            [{'text': '🆘 Help', 'callback_data': 'menu_help'}, {'text': '❌ Close', 'callback_data': 'menu_close'}]
        ]
    }
    
    return {'inline_keyboard': keyboards.get(current_menu, keyboards['home'])}

def create_search_results_keyboard(results: List[Dict], query: str) -> Dict:
    """Create keyboard for search results."""
    keyboard = []
    
    # --- Check if it's a series search ---
    series_names = {r['entry']['series_name'] for r in results if r['entry'].get('is_series') and r['entry'].get('series_name')}
    if len(series_names) == 1:
        series_name = series_names.pop()
        keyboard.append([{
            'text': f"📺 View All Seasons for {series_name}",
            'callback_data': f"vs_{series_name[:35]}" # vs = view_series
        }])

    for result in results[:10]:  # Limit to 10 results
        entry = result['entry']
        title = entry.get('title', 'Unknown')[:45]  # Truncate long titles
        
        if entry.get('is_series'):
            title += f" (S{entry.get('season_number', 1)})"
        
        keyboard.append([{
            'text': title,
            'callback_data': f"v_{result['imdb_id']}_{query[:30]}" # v_ = view
        }])
    
    keyboard.append([{'text': '❌ Close', 'callback_data': 'menu_close'}])
    return {'inline_keyboard': keyboard}

def create_series_seasons_keyboard(seasons: Dict[int, str], series_name: str, page: int = 1) -> Dict:
    """Create a paginated keyboard for series seasons."""
    PAGE_SIZE = 10
    
    sorted_seasons = sorted(seasons.items())
    start_index = (page - 1) * PAGE_SIZE
    end_index = start_index + PAGE_SIZE

    paginated_seasons = sorted_seasons[start_index:end_index]
    
    keyboard = []
    # Create a 2-column layout for season buttons
    row = []
    for season_num, imdb_id in paginated_seasons:
        row.append({'text': f'S{season_num}', 'callback_data': f"view_{imdb_id}"})
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    # --- Pagination Controls ---
    nav_row = []
    if start_index > 0:
        nav_row.append({'text': '⬅️ Back', 'callback_data': f'sp_{page-1}_{series_name[:30]}'})

    if end_index < len(sorted_seasons):
        nav_row.append({'text': 'Next ➡️', 'callback_data': f'sp_{page+1}_{series_name[:30]}'})

    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([{'text': '❌ Close', 'callback_data': 'menu_close'}])
    return {'inline_keyboard': keyboard}

def format_movie_details(entry: Dict, imdb_id: str) -> str:
    """Format movie/series details with proper field handling and hyperlinks."""
    title = entry.get('title', 'Unknown Title')
    year = f" ({entry['year']})" if entry.get('year') else ""
    
    # Main title
    message = f"🎬 **{title}{year}**\n\n"
    
    # MSOne release number
    if entry.get('msone_release_number'):
        message += f"🆔 MSOne Release: `{entry['msone_release_number']}`\n\n"
    
    # --- Helper to format a field with an optional link ---
    def format_field(data, prefix):
        if not data or not data.get('name') or data['name'] == 'Unknown':
            return None
        if data.get('url'):
            return f"{prefix} [{data['name']}]({data['url']})"
        return f"{prefix} {data['name']}"

    details = []
    
    # Language
    lang_str = format_field(entry.get('language'), "🗣️ **Language:**")
    if lang_str: details.append(lang_str)

    # Director
    director_str = format_field(entry.get('director'), "🎬 **Director:**")
    if director_str: details.append(director_str)

    # Genre
    genre_str = format_field(entry.get('genre'), "🎭 **Genre:**")
    if genre_str: details.append(genre_str)

    # Rating
    rating = entry.get('imdb_rating')
    if rating and rating != 'N/A':
        details.append(f"⭐ **IMDb Rating:** {rating}")

    # Certification
    cert = entry.get('certification')
    if cert and cert != 'Not Rated':
        details.append(f"🏷️ **Certification:** {cert}")
    
    # Translator
    translator_str = format_field(entry.get('translatedBy'), "🌐 **Translator:**")
    if translator_str: details.append(translator_str)
    
    # Poster Maker
    poster_maker_str = format_field(entry.get('poster_maker'), "🎨 **Poster by:**")
    if poster_maker_str: details.append(poster_maker_str)

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
    
    # Full synopsis
    synopsis = entry.get('descriptionMalayalam')
    if synopsis and synopsis != 'No description available':
        message += f"📖 **Synopsis:**\n{synopsis}\n\n"
    
    # Source URL
    if entry.get('source_url'):
        message += f"[Source]({entry['source_url']})"

    return message

def create_detail_keyboard(entry: Dict, imdb_id: str, query: str) -> Dict:
    """Create keyboard for movie detail page."""
    keyboard = []
    
    # Download button
    if entry.get('srtURL'):
        keyboard.append([{
            'text': '📥 Download Subtitle',
            'callback_data': f"dl_{imdb_id}" # dl_ = download
        }])
    
    # IMDb link
    if entry.get('imdbURL'):
        keyboard.append([{
            'text': '🎬 View on IMDb',
            'url': entry['imdbURL']
        }])
    
    # Back and close buttons
    back_button_data = f"bs_{query}" if query else "menu_home"
    keyboard.append([
        {'text': '🔙 Back to Search', 'callback_data': back_button_data},
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
        
        elif callback_data.startswith('v_'):
            parts = callback_data.split('_')
            imdb_id = parts[1]
            query = "_".join(parts[2:])

            if imdb_id in db:
                entry = db[imdb_id]
                
                # Format message
                detail_text = format_movie_details(entry, imdb_id)
                keyboard = create_detail_keyboard(entry, imdb_id, query)
                
                # Try to send with poster
                poster_url = entry.get('posterMalayalam')
                if poster_url:
                    return {
                        'method': 'editMessageMedia',
                        'media': {
                            'type': 'photo',
                            'media': poster_url,
                            'caption': detail_text,
                            'parse_mode': 'Markdown'
                        },
                        'reply_markup': keyboard
                    }
                else:
                    return {
                        'method': 'editMessageText',
                        'text': detail_text,
                        'reply_markup': keyboard,
                        'parse_mode': 'Markdown',
                        'disable_web_page_preview': True
                    }
        
        elif callback_data.startswith('dl_'):
            imdb_id = callback_data.replace('dl_', '')
            if imdb_id in db:
                entry = db[imdb_id]
                download_url = entry.get('srtURL')
                
                if download_url:
                    # Start download in background
                    asyncio.create_task(download_and_upload_subtitle(
                        download_url,
                        chat_id,
                        entry.get('title', 'subtitle'),
                        entry.get('source_url')
                    ))
                    
                    return {
                        'method': 'answerCallbackQuery',
                        'text': 'Download started! Please wait...',
                        'show_alert': False
                    }
                else:
                    return {
                        'method': 'answerCallbackQuery',
                        'text': 'Download link not available.',
                        'show_alert': True
                    }
        
        elif callback_data.startswith('vs_'):
            series_name = callback_data.replace('vs_', '')
            seasons = get_series_seasons(series_name)
            if not seasons:
                return {
                    'method': 'answerCallbackQuery',
                    'text': 'Could not find seasons for this series.',
                    'show_alert': True
                }

            keyboard = create_series_seasons_keyboard(seasons, series_name, page=1)
            return {
                'method': 'editMessageText',
                'text': f"📺 Seasons for **{series_name}**:",
                'reply_markup': keyboard,
                'parse_mode': 'Markdown'
            }

        elif callback_data.startswith('sp_'):
            parts = callback_data.split('_')
            page = int(parts[1])
            series_name = "_".join(parts[2:])

            seasons = get_series_seasons(series_name)
            keyboard = create_series_seasons_keyboard(seasons, series_name, page=page)

            return {
                'method': 'editMessageText',
                'text': f"📺 Seasons for **{series_name}** (Page {page}):",
                'reply_markup': keyboard,
                'parse_mode': 'Markdown'
            }

        elif callback_data == 'back_search':
            # If the original message had a photo, we must edit the caption.
            # Otherwise, we can edit the text. This avoids a Telegram API error.
            if 'photo' in message_data:
                return {
                    'method': 'editMessageCaption',
                    'caption': '🔍 Send me a movie or series name to search again.',
                    'reply_markup': {'inline_keyboard': [[{'text': '❌ Close', 'callback_data': 'menu_close'}]]}
                }
            else:
                return {
                    'method': 'editMessageText',
                    'text': '🔍 Send me a movie or series name to search for subtitles.',
                    'reply_markup': {'inline_keyboard': [[{'text': '❌ Close', 'callback_data': 'menu_close'}]]}
                }
        
        # Default response
        return {
            'method': 'answerCallbackQuery',
            'text': 'Action completed.',
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

        # --- Handle Commands & Search ---
        if text.startswith('/'):
            command = text.split(' ')[0].lower()
            if command == '/start':
                return {
                    'method': 'sendMessage',
                    'chat_id': chat_id,
                    'text': WELCOME_MESSAGE,
                    'reply_markup': create_menu_keyboard('home'),
                    'parse_mode': 'Markdown'
                }
            elif command == '/help':
                 return {
                    'method': 'sendMessage',
                    'chat_id': chat_id,
                    'text': HELP_MESSAGE,
                    'reply_markup': create_menu_keyboard('help'),
                    'parse_mode': 'Markdown'
                }
            elif command == '/about':
                 return {
                    'method': 'sendMessage',
                    'chat_id': chat_id,
                    'text': ABOUT_MESSAGE,
                    'reply_markup': create_menu_keyboard('about'),
                    'parse_mode': 'Markdown'
                }
            # Admin commands go here
            elif str(user_id) == OWNER_ID:
                if command == '/broadcast':
                    broadcast_text = text.replace('/broadcast ', '')
                    # TODO: Implement broadcast functionality
                    return {
                        'chat_id': chat_id,
                        'text': f"Broadcast feature coming soon!\n\nMessage to broadcast: {broadcast_text}",
                        'parse_mode': 'Markdown'
                    }
            else:
                return {
                    'method': 'sendMessage',
                    'chat_id': chat_id,
                    'text': "🤔 Unrecognized command. Type a movie or series name to search."
                }
        else:
            # --- Handle Search Query ---
            results = search_content(text)
            if not results:
                return {
                    'method': 'sendMessage',
                    'chat_id': chat_id,
                    'text': f"🤷‍♀️ No subtitles found for **{text}**. Please check the spelling or try another name.",
                    'parse_mode': 'Markdown'
                }

            return {
                'method': 'sendMessage',
                'chat_id': chat_id,
                'text': f"🔎 Found {len(results)} results for **{text}**:",
                'reply_markup': create_search_results_keyboard(results, text),
                'parse_mode': 'Markdown'
            }

        # Fallback for admin check
        if str(user_id) == OWNER_ID:
            if text.startswith('/broadcast '):
                broadcast_text = text.replace('/broadcast ', '')
                # TODO: Implement broadcast functionality
                return {
                    'chat_id': chat_id,
                    'text': f"Broadcast feature coming soon!\n\nMessage to broadcast: {broadcast_text}",
                    'parse_mode': 'Markdown'
                }
            # Add other admin commands here with elif

    except Exception as e:
        logger.error(f"Error handling telegram message: {e}")
        return None

# --- Telegram API Communication ---
async def send_telegram_message(payload: Dict):
    """Send a message to the Telegram API."""
    import aiohttp
    url = f"https://api.telegram.org/bot{TOKEN}/{payload.get('method', 'sendMessage')}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Telegram API error: {response.status} - {error_text}")
                return await response.json()
    except Exception as e:
        logger.error(f"Error sending message to Telegram: {e}")
    return None


# --- FastAPI Application Events ---
@app.on_event("startup")
async def startup_event():
    """On startup, load DB, set webhook, and notify owner."""
    load_databases()

    webhook_url = os.environ.get("RENDER_EXTERNAL_URL")
    if webhook_url and TOKEN:
        webhook_url += "/telegram"
        payload = {
            'url': webhook_url,
            'secret_token': WEBHOOK_SECRET
        }
        logger.info(f"Setting webhook to: {webhook_url}")
        # Use a raw request to set the webhook
        import requests
        url = f"https://api.telegram.org/bot{TOKEN}/setWebhook"
        response = requests.post(url, data=payload)
        if response.status_code == 200:
            logger.info("Webhook set successfully!")
        else:
            logger.error(f"Failed to set webhook: {response.status_code} - {response.text}")

        # Notify owner if ID is set
        if OWNER_ID:
            await send_telegram_message({
                'chat_id': OWNER_ID,
                'text': '✅ **Bot is up and running!**',
                'parse_mode': 'Markdown'
            })

@app.get("/")
def read_root():
    """A simple endpoint to confirm the bot is online."""
    return {"status": "ok", "message": "Subtitle Search Bot is running"}

@app.get("/healthz")
def health_check():
    """Health check endpoint for Render."""
    return {"status": "ok"}

@app.post("/telegram")
async def telegram_webhook(request: Request):
    """Main webhook endpoint to receive updates from Telegram."""
    # Verify secret token
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        logger.warning("Invalid secret token received")
        return Response(status_code=HTTPStatus.FORBIDDEN)

    try:
        data = await request.json()
        response_payload = await handle_telegram_message(data)

        if response_payload:
            return response_payload

    except json.JSONDecodeError:
        logger.error("Failed to decode JSON from Telegram request")
        return Response(status_code=HTTPStatus.BAD_REQUEST)

    return Response(status_code=HTTPStatus.OK)

@app.get("/api/subtitles")
def api_search(query: str = Query(..., min_length=1)):
    """REST API endpoint for searching subtitles."""
    results = search_content(query)
    return {"query": query, "results": results}
