import json
import logging
from http import HTTPStatus
from fastapi import FastAPI, Response

# --- Logging Setup ---
logger = logging.getLogger(__name__)

# --- Database Loading ---
DB_FILE = 'db.json'
db = {}
try:
    with open(DB_FILE, 'r', encoding='utf-8') as f:
        db = json.load(f)
    logger.info(f"Successfully loaded db.json with {len(db)} entries from api.py.")
except (FileNotFoundError, json.JSONDecodeError) as e:
    logger.error(f"Could not load or parse db.json in api.py: {e}")

# --- Search Logic ---
def search_subtitles(query: str) -> list:
    if not db:
        return []
    cleaned_query = query.lower().replace('.', ' ').strip()
    results = [
        (imdb_id, entry) for imdb_id, entry in db.items()
        if cleaned_query in entry.get('title', '').lower().replace('.', ' ').strip()
    ]
    return results

# --- FastAPI App and Routes ---
app = FastAPI()

@app.get("/")
async def root():
    return Response(content="ok", status_code=HTTPStatus.OK)

@app.get("/healthz")
async def healthz():
    return Response(content="healthy", status_code=HTTPStatus.OK)

@app.get("/api/subtitles")
async def api_search(query: str):
    results = search_subtitles(query)
    if not results:
        return {"error": "No results found"}
    # Convert list of tuples to list of dicts for JSON response
    return {"count": len(results), "results": [dict(r[1], **{'imdb_id': r[0]}) for r in results]}
