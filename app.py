import os
import json
import logging
import asyncio
from http import HTTPStatus
from typing import Dict, Any

from fastapi import FastAPI, Request, Response, HTTPException, Query
from telegram import Update
from contextlib import asynccontextmanager

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- Environment Variables ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN environment variable")

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "a-random-string")
OWNER_ID = os.environ.get("OWNER_ID")
DB_FILE = os.environ.get("DB_FILE", "db.json")

# --- Global Variables ---
db: Dict[str, Any] = {}
ptb_app = None

# --- Database Functions ---
async def load_database(max_retries: int = 3, retry_delay: int = 5) -> Dict[str, Any]:
    """Load database with retry mechanism."""
    for attempt in range(max_retries):
        try:
            with open(DB_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                logger.info(f"Successfully loaded {DB_FILE} with {len(data)} entries (attempt {attempt + 1})")
                return data
        except FileNotFoundError:
            logger.warning(f"Database file {DB_FILE} not found (attempt {attempt + 1})")
            if attempt < max_retries - 1:
                logger.info(f"Retrying in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in {DB_FILE}: {e}")
            break
        except Exception as e:
            logger.error(f"Unexpected error loading database: {e}")
            break
    
    logger.warning("Using empty database")
    return {}

# --- Search Function ---
def search_subtitles(query: str) -> list:
    """Search subtitles in database."""
    if not db or not query:
        return []
    
    # Sanitize query
    cleaned_query = query.lower().replace('.', ' ').strip()[:100]  # Limit query length
    
    results = [
        (imdb_id, entry) for imdb_id, entry in db.items()
        if cleaned_query in entry.get('title', '').lower().replace('.', ' ').strip()
    ]
    return results

# --- Lifecycle Management ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle."""
    global db, ptb_app
    
    # Startup
    logger.info("Starting application...")
    
    # Load database
    db = await load_database()
    
    # Initialize bot
    from bot import create_ptb_application
    ptb_app = create_ptb_application(TOKEN)
    ptb_app.bot_data["db"] = db
    
    try:
        await ptb_app.initialize()
        
        # Set webhook
        base_url = os.environ.get("RENDER_EXTERNAL_URL", "https://subto-mso-tga.onrender.com")
        webhook_url = f"{base_url}/webhook"
        
        await ptb_app.bot.set_webhook(
            url=webhook_url,
            secret_token=WEBHOOK_SECRET,
            drop_pending_updates=True,
            max_connections=10
        )
        logger.info(f"Webhook set to {webhook_url}")
        
        # Notify owner
        if OWNER_ID:
            try:
                await ptb_app.bot.send_message(
                    chat_id=OWNER_ID, 
                    text="🟢 Bot is up and running!\n\nDatabase loaded successfully."
                )
                logger.info(f"Sent startup notification to owner {OWNER_ID}")
            except Exception as e:
                logger.error(f"Failed to send startup notification: {e}")
    
    except Exception as e:
        logger.error(f"Failed to initialize bot: {e}")
        raise
    
    yield
    
    # Shutdown
    logger.info("Shutting down application...")
    if ptb_app:
        try:
            if OWNER_ID:
                await ptb_app.bot.send_message(
                    chat_id=OWNER_ID, 
                    text="🔴 Bot is shutting down..."
                )
        except Exception as e:
            logger.error(f"Failed to send shutdown notification: {e}")
        
        await ptb_app.shutdown()

# --- FastAPI App ---
app = FastAPI(
    title="Subtitle Search Bot API",
    description="Telegram bot and API for searching Malayalam subtitles",
    version="1.0.0",
    lifespan=lifespan
)

# --- Middleware ---
@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Add security headers."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response

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
        
        # Limit results and format response
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

@app.post("/webhook")
async def webhook(request: Request):
    """Telegram webhook endpoint."""
    # Verify secret token
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        logger.warning("Webhook secret mismatch!")
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="Invalid secret")
    
    try:
        data = await request.json()
        update = Update.de_json(data, ptb_app.bot)
        await ptb_app.process_update(update)
        return Response(status_code=HTTPStatus.OK)
        
    except json.JSONDecodeError:
        logger.error("Failed to decode JSON from Telegram webhook")
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail="Invalid JSON")
    
    except Exception as e:
        logger.error(f"Webhook processing error: {e}")
        raise HTTPException(status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail="Processing error")

# --- Debug Endpoints (only in development) ---
if os.environ.get("ENVIRONMENT") == "development":
    @app.get("/debug/db-info")
    async def debug_db_info():
        """Debug endpoint to check database status."""
        return {
            "database_loaded": len(db) > 0,
            "total_entries": len(db),
            "sample_keys": list(db.keys())[:5] if db else []
        }
