import os
import json
import logging
import asyncio
import zipfile
import tempfile
import threading
import time
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

# --- Global Variables ---
db: Dict[str, Any] = {}
series_db: Dict[str, Dict[int, str]] = {}
scraper_status = {"running": False, "progress": ""}

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
        with open('series_db.json', 'r', encoding='utf-8') as f:
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

async def download_and_upload_subtitle(download_url: str, chat_id: str, title: str) -> bool:
    """Download subtitle file and upload to Telegram."""
    if not download_url or not TOKEN:
        return False
    
    import aiohttp
    import aiofiles
    
    try:
        logger.info(f"Starting download: {download_url}")
        
        # Send initial message
        await send_telegram_message({
            'chat_id': chat_id,
            'text': f"📥 Downloading subtitle for **{title}**...",
            'parse_mode': 'Markdown'
        })
        
        # Create temp directory
        with tempfile.TemporaryDirectory() as temp_dir:
            # Download file with proper headers
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as session:
                async with session.get(download_url) as resp:
                    if resp.status != 200:
                        logger.error(f"Download failed: {resp.status}")
                        await send_telegram_message({
                            'chat_id': chat_id,
                            'text': f"❌ Failed to download subtitle (HTTP {resp.status})"
                        })
                        return False
                    
                    # Get filename from headers or URL
                    filename = f"{title.replace(' ', '_')}.srt"
                    if 'content-disposition' in resp.headers:
                        cd = resp.headers['content-disposition']
                        filename_match = re.search(r'filename[*]?="([^"]+)"', cd)
                        if filename_match:
                            filename = filename_match.group(1)
                    elif download_url:
                        url_filename = download_url.split('/')[-1]
                        if '.' in url_filename:
                            filename = url_filename
                    
                    file_path = os.path.join(temp_dir, filename)
                    
                    # Save file
                    async with aiofiles.open(file_path, 'wb') as f:
                        async for chunk in resp.content.iter_chunked(8192):
                            await f.write(chunk)
                    
                    logger.info(f"File downloaded: {file_path}, size: {os.path.getsize(file_path)}")
                    
                    # Update progress
                    await send_telegram_message({
                        'chat_id': chat_id,
                        'text': f"📤 Uploading subtitle file..."
                    })
                    
                    # Check if it's a zip file
                    if filename.lower().endswith('.zip'):
                        return await upload_zip_contents(file_path, chat_id, title)
                    else:
                        return await upload_single_file(file_path, chat_id, filename)
    
    except asyncio.TimeoutError:
        logger.error("Download timeout")
        await send_telegram_message({
            'chat_id': chat_id,
            'text': "⏰ Download timeout. Please try again later."
        })
        return False
    except Exception as e:
        logger.error(f"Error downloading/uploading subtitle: {e}")
        await send_telegram_message({
            'chat_id': chat_id,
            'text': f"❌ Error: {str(e)}"
        })
        return False

async def upload_zip_contents(zip_path: str, chat_id: str, title: str) -> bool:
    """Extract and upload all files from zip."""
    try:
        logger.info(f"Processing zip file: {zip_path}")
        
        with tempfile.TemporaryDirectory() as extract_dir:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
            
            # Find subtitle files
            subtitle_files = []
            for root, dirs, files in os.walk(extract_dir):
                for file in files:
                    if file.lower().endswith(('.srt', '.ass', '.ssa', '.vtt')):
                        subtitle_files.append(os.path.join(root, file))
            
            if not subtitle_files:
                await send_telegram_message({
                    'chat_id': chat_id,
                    'text': "❌ No subtitle files found in the archive."
                })
                return False
            
            # Upload each file
            uploaded_count = 0
            for file_path in subtitle_files:
                filename = os.path.basename(file_path)
                if await upload_single_file(file_path, chat_id, filename):
                    uploaded_count += 1
                await asyncio.sleep(1)  # Rate limiting
            
            await send_telegram_message({
                'chat_id': chat_id,
                'text': f"✅ Uploaded {uploaded_count} subtitle files from archive."
            })
            
            return uploaded_count > 0
    except Exception as e:
        logger.error(f"Error processing zip file: {e}")
        return False

async def upload_single_file(file_path: str, chat_id: str, filename: str) -> bool:
    """Upload single file to Telegram."""
    import aiohttp
    
    try:
        if not os.path.exists(file_path):
            logger.error(f"File not found: {file_path}")
            return False
        
        file_size = os.path.getsize(file_path)
        if file_size > 50 * 1024 * 1024:  # 50MB limit
            await send_telegram_message({
                'chat_id': chat_id,
                'text': f"❌ File {filename} is too large (>50MB). Telegram limit exceeded."
            })
            return False
        
        url = f"https://api.telegram.org/bot{TOKEN}/sendDocument"
        
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
            with open(file_path, 'rb') as f:
                data = aiohttp.FormData()
                data.add_field('chat_id', chat_id)
                data.add_field('document', f, filename=filename)
                data.add_field('caption', f'📁 {filename}')
                
                async with session.post(url, data=data) as resp:
                    success = resp.status == 200
                    if not success:
                        error_text = await resp.text()
                        logger.error(f"Upload failed: {resp.status} - {error_text}")
                    else:
                        logger.info(f"Successfully uploaded: {filename}")
                    return success
    
    except Exception as e:
        logger.error(f"Error uploading file: {e}")
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

def create_search_results_keyboard(results: List[Dict]) -> Dict:
    """Create keyboard for search results."""
    keyboard = []
    
    for result in results[:10]:  # Limit to 10 results
        entry = result['entry']
        title = entry.get('title', 'Unknown')[:45]  # Truncate long titles
        
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
    """Format movie/series details with proper field handling."""
    title = entry.get('title', 'Unknown Title')
    year = f" ({entry['year']})" if entry.get('year') else ""
    
    # Main title
    message = f"🎬 **{title}{year}**\n\n"
    
    # MSOne release number
    if entry.get('msone_release_number'):
        message += f"🆔 MSOne Release: `{entry['msone_release_number']}`\n\n"
    
    # Movie details - each on separate line, use N/A for missing
    details = []
    
    language = entry.get('language', 'N/A')
    if language and language != 'Unknown':
        details.append(f"🗣️ **Language:** {language}")
    else:
        details.append(f"🗣️ **Language:** N/A")
    
    director = entry.get('director', 'N/A')
    if director and director != 'Unknown':
        details.append(f"🎬 **Director:** {director}")
    else:
        details.append(f"🎬 **Director:** N/A")
    
    genre = entry.get('genre', 'N/A')
    if genre and genre != 'Unknown':
        details.append(f"🎭 **Genre:** {genre}")
    else:
        details.append(f"🎭 **Genre:** N/A")
    
    rating = entry.get('imdb_rating', 'N/A')
    if rating and rating != 'N/A':
        details.append(f"⭐ **IMDb Rating:** {rating}")
    else:
        details.append(f"⭐ **IMDb Rating:** N/A")
    
    cert = entry.get('certification', 'N/A')
    if cert and cert != 'Not Rated':
        details.append(f"🏷️ **Certification:** {cert}")
    else:
        details.append(f"🏷️ **Certification:** N/A")
    
    # Translator
    translator = entry.get('translatedBy', {})
    if translator and translator.get('name') and translator['name'] != 'Unknown':
        details.append(f"🌐 **Translator:** {translator['name']}")
    else:
        details.append(f"🌐 **Translator:** N/A")
    
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
    synopsis = entry.get('descriptionMalayalam', '')
    if synopsis and synopsis != 'No description available':
        message += f"📖 **Synopsis:**\n{synopsis}\n\n"
    else:
        message += f"📖 **Synopsis:** N/A\n\n"
    
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

def start_scraper_background():
    """Start scraper in background thread."""
    def scraper_thread():
        global scraper_status
        try:
            scraper_status["running"] = True
            scraper_status["progress"] = "Starting scraper..."
            
            # Import and run scraper
            from scraper import start_scraper
            success = start_scraper()
            
            scraper_status["running"] = False
            if success:
                scraper_status["progress"] = "Scraper completed successfully!"
                # Reload databases
                load_databases()
            else:
                scraper_status["progress"] = "Scraper failed!"
                
        except Exception as e:
            logger.error(f"Scraper thread error: {e}")
            scraper_status["running"] = False
            scraper_status["progress"] = f"Scraper error: {str(e)}"
    
    thread = threading.Thread(target=scraper_thread)
    thread.daemon = True
    thread.start()

def stop_scraper_background():
    """Stop scraper gracefully."""
    try:
        from scraper import stop_scraper
        stop_scraper()
        scraper_status["progress"] = "Stop signal sent to scraper..."
    except Exception as e:
        logger.error(f"Error stopping scraper: {e}")

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
        
        elif callback_data.startswith('scraper_'):
            action = callback_data.replace('scraper_', '')
            
            if action == 'start':
                if scraper_status["running"]:
                    return {
                        'method': 'answerCallbackQuery',
                        'text': 'Scraper is already running!',
                        'show_alert': True
                    }
                else:
                    start_scraper_background()
                    return {
                        'method': 'editMessageText',
                        'text': f"🔄 **Scraper Started!**\n\nStatus: {scraper_status['progress']}\n\nThis may take several minutes...",
                        'parse_mode': 'Markdown',
                        'reply_markup': {
                            'inline_keyboard': [
                                [{'text': '⏹️ Stop Scraper', 'callback_data': 'scraper_stop'}],
                                [{'text': '📊 Check Status', 'callback_data': 'scraper_status'}],
                                [{'text': '❌ Close', 'callback_data': 'menu_close'}]
                            ]
                        }
                    }
            elif action == 'stop':
                stop_scraper_background()
                return {
                    'method': 'answerCallbackQuery',
                    'text': 'Stop signal sent to scraper.',
                    'show_alert': True
                }
            elif action == 'status':
                status_text = f"🔄 **Scraper Status**\n\n"
                status_text += f"**Running:** {'Yes' if scraper_status['running'] else 'No'}\n"
                status_text += f"**Progress:** {scraper_status['progress']}\n"
                status_text += f"**Database Size:** {len(db)} entries"
                
                return {
                    'method': 'editMessageText',
                    'text': status_text,
                    'parse_mode': 'Markdown',
                    'reply_markup': {
                        'inline_keyboard': [
                            [{'text': '▶️ Start Scraper', 'callback_data': 'scraper_start'}] if not scraper_status['running'] else [{'text': '⏹️ Stop Scraper', 'callback_data': 'scraper_stop'}],
                            [{'text': '🔄 Refresh Status', 'callback_data': 'scraper_status'}],
                            [{'text': '❌ Close', 'callback_data': 'menu_close'}]
                        ]
                    }
                }
        
        elif callback_data.startswith('view_'):
            imdb_id = callback_data.replace('view_', '')
            if imdb_id in db:
                entry = db[imdb_id]
                
                # Format message
                detail_text = format_movie_details(entry, imdb_id)
                keyboard = create_detail_keyboard(entry, imdb_id)
                
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
                        'disable_web_page_preview': False
                    }
        
        elif callback_data.startswith('download_'):
            imdb_id = callback_data.replace('download_', '')
            if imdb_id in db:
                entry = db[imdb_id]
                download_url = entry.get('srtURL')
                
                if download_url:
                    # Start download in background
                    asyncio.create_task(download_and_upload_subtitle(
                        download_url, 
                        chat_id, 
                        entry.get('title', 'subtitle')
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
        
        elif callback_data == 'back_search':
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
            elif text == '/scrape' or text == '/scrape_start':
                return {
