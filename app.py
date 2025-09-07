import os
import json
import logging
from http import HTTPStatus
from typing import Dict, Any

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

# --- Database Functions ---
def load_database() -> Dict[str, Any]:
    """Load database from file."""
    try:
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            logger.info(f"Successfully loaded {DB_FILE} with {len(data)} entries")
            return data
    except FileNotFoundError:
        logger.warning(f"Database file {DB_FILE} not found")
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in {DB_FILE}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error loading database: {e}")
    
    logger.warning("Using empty database")
    return {}

# --- Search Function ---
def search_subtitles(query: str) -> list:
    """Search subtitles in database."""
    if not db or not query:
        return []
    
    cleaned_query = query.lower().replace('.', ' ').strip()[:100]
    
    results = [
        (imdb_id, entry) for imdb_id, entry in db.items()
        if cleaned_query in entry.get('title', '').lower().replace('.', ' ').strip()
    ]
    return results

# --- Manual Bot Response Handler ---
async def handle_telegram_message(message_data: dict) -> dict:
    """Handle Telegram messages manually without ptb library issues."""
    try:
        message = message_data.get('message', {})
        text = message.get('text', '').strip()
        chat_id = message.get('chat', {}).get('id')
        user = message.get('from', {})
        
        if not chat_id or not text:
            return None
        
        logger.info(f"Received message: '{text}' from user {user.get('username', 'unknown')}")
        
        # Handle commands
        if text.startswith('/start'):
            response_text = (
                "🎬 *Welcome to Malayalam Subtitle Search Bot!*\n\n"
                "Send me a movie or TV show name to search for Malayalam subtitles.\n\n"
                "*Example:* Just type 'Dune' or 'Breaking Bad'\n\n"
                "_Powered by malayalamsubtitles.org_"
            )
        elif text.startswith('/help'):
            response_text = (
                "*How to use:*\n\n"
                "1️⃣ Send me any movie or TV show name\n"
                "2️⃣ I'll search for Malayalam subtitles\n"
                "3️⃣ Click download links to get subtitle files\n\n"
                "*Commands:*\n"
                "• /start - Welcome message\n"
                "• /help - This help\n"
                "• /stats - Statistics"
            )
        elif text.startswith('/stats'):
            response_text = f"📊 *Bot Statistics:*\n\n🎬 Movies/Shows: {len(db)}\n🤖 Status: Online"
        else:
            # Search query
            if len(text) < 2:
                response_text = "Please send a movie name with at least 2 characters."
            elif len(text) > 100:
                response_text = "Movie name too long. Please use a shorter search term."
            else:
                results = search_subtitles(text)
                if not results:
                    response_text = f'No subtitles found for "{text}"\n\nTry different keywords or check spelling.'
                else:
                    # Format first result
                    imdb_id, entry = results[0]
                    title = entry.get('title', 'Unknown')
                    imdb_url = entry.get('imdbURL', '')
                    download_url = entry.get('srtURL', '')
                    
                    response_text = f"🎬 *{title}*\n\n"
                    if imdb_url:
                        response_text += f"📝 [IMDb]({imdb_url})\n"
                    if download_url:
                        response_text += f"📥 [Download Subtitle]({download_url})\n"
                    response_text += f"\n🆔 `{imdb_id}`"
                    
                    if len(results) > 1:
                        response_text += f"\n\n_Found {len(results)} total results. Showing first result._"
        
        return {
            'chat_id': chat_id,
            'text': response_text,
            'parse_mode': 'Markdown',
            'disable_web_page_preview': True
        }
        
    except Exception as e:
        logger.error(f"Error handling message: {e}")
        return None

async def send_telegram_message(response_data: dict):
    """Send message back to Telegram using Bot API."""
    if not TOKEN or not response_data:
        return
    
    import aiohttp
    
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=response_data) as resp:
                if resp.status == 200:
                    logger.info("Message sent successfully")
                else:
                    logger.error(f"Failed to send message: {resp.status}")
    except Exception as e:
        logger.error(f"Error sending message: {e}")

# --- FastAPI App ---
app = FastAPI(
    title="Subtitle Search Bot API",
    description="Telegram bot and API for searching Malayalam subtitles",
    version="1.0.0"
)

@app.on_event("startup")
async def startup_event():
    """Initialize application on startup."""
    global db
    
    logger.info("Starting application...")
    db = load_database()
    
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
                                'text': 'Bot is up and running!',
                                'parse_mode': 'Markdown'
                            })
                    else:
                        logger.error(f"Failed to set webhook: {resp.status}")
        except Exception as e:
            logger.error(f"Error setting webhook: {e}")
    else:
        logger.info("No bot token - webhook not set")

# --- API Endpoints ---
@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "status": "ok", 
        "message": "Subtitle Search Bot API is running",
        "database_entries": len(db)
    }

@app.get("/healthz")
async def health_check():
    """Health check endpoint for Render."""
    return {"status": "healthy", "database_loaded": len(db) > 0}

@app.get("/api/subtitles")
async def api_search(
    query: str = Query(..., min_length=1, max_length=100, description="Search query"),
    limit: int = Query(10, ge=1, le=50, description="Maximum results to return")
):
    """Search subtitles via API."""
    try:
        results = search_subtitles(query)
        
        if not results:
            return {
                "query": query,
                "count": 0,
                "results": [],
                "message": "No results found"
            }
        
        limited_results = results[:limit]
        formatted_results = [
            {**entry, "imdb_id": imdb_id} 
            for imdb_id, entry in limited_results
        ]
        
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
    """Telegram webhook endpoint."""
    if not TOKEN:
        logger.warning("No bot token - webhook ignored")
        return Response(status_code=HTTPStatus.SERVICE_UNAVAILABLE)
        
    # Verify secret token
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        logger.warning("Webhook secret mismatch!")
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="Invalid secret")
    
    try:
        data = await request.json()
        logger.info(f"Received webhook data: {json.dumps(data, indent=2)}")
        
        # Handle the message
        response_data = await handle_telegram_message(data)
        if response_data:
            await send_telegram_message(response_data)
        
        return Response(status_code=HTTPStatus.OK)
        
    except json.JSONDecodeError:
        logger.error("Failed to decode JSON from Telegram webhook")
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail="Invalid JSON")
    
    except Exception as e:
        logger.error(f"Webhook processing error: {e}")
        raise HTTPException(status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail="Processing error")
