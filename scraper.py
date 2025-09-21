import requests
from bs4 import BeautifulSoup
import json
import time
import re
import logging
import os
from urllib.parse import urljoin
import asyncio
import asyncpg
from datetime import datetime, timedelta

# --- Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")
BASE_URL = "https://malayalamsubtitles.org"
RELEASES_URL = f"{BASE_URL}/releases/"
MAX_PAGES = int(os.environ.get("SCRAPER_MAX_PAGES", "6"))
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}

# --- Helper Functions (Unchanged from original) ---
def get_soup(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
        return BeautifulSoup(response.text, 'html.parser')
    except requests.RequestException as e:
        logger.error(f"Error fetching {url}: {e}")
        return None

def clean_text(text):
    return re.sub(r'\s+', ' ', text.strip()) if text else ""

def extract_imdb_id(url):
    match = re.search(r'tt\d+', url) if url else None
    return match.group(0) if match else None

def extract_year(title):
    match = re.search(r'\((\d{4})\)', title)
    return match.group(1) if match else None

def extract_season_info(title):
    patterns = [r'Season\s*(\d+)', r'സീസൺ\s*(\d+)', r'S0?(\d+)', r'സീസണ്‍\s*(\d+)']
    season_number = None
    is_series = False

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

def scrape_detail_page(url):
    """Scrapes comprehensive details from a movie/series page."""
    soup = get_soup(url)
    if not soup: return None

    try:
        details = {'source_url': url}
        
        title_tag = soup.select_one('h1.entry-title, h1#release-title')
        details['title'] = clean_text(title_tag.get_text()) if title_tag else "Unknown Title"
        details['year'] = extract_year(details['title'])
        details.update(extract_season_info(details['title']))
        
        srt_tag = soup.select_one('a#download-button')
        details['srtURL'] = (srt_tag.get('data-downloadurl') or srt_tag.get('href')) if srt_tag else None
        
        poster_tag = soup.select_one('figure#release-poster img, .entry-content figure img')
        if poster_tag and poster_tag.get('src'):
            details['posterMalayalam'] = urljoin(BASE_URL, poster_tag['src'])
        
        imdb_tag = soup.select_one('a#imdb-button, a[href*="imdb.com"]')
        if imdb_tag:
            details['imdbURL'] = imdb_tag['href']

        desc_tag = soup.select_one('div#synopsis, .entry-content p')
        if desc_tag:
            details['descriptionMalayalam'] = clean_text(desc_tag.get_text(separator='\n', strip=True))

        details_table = soup.select_one('#release-details-table tbody')
        table_data = {}
        if details_table:
            for row in details_table.select('tr'):
                cells = row.select('td')
                if len(cells) >= 2:
                    label = clean_text(cells[0].get_text()).lower().strip().replace(':', '')
                    table_data[label] = cells[1]

        def get_field_data(labels):
            for label in labels:
                if label in table_data:
                    cell = table_data[label]
                    text = clean_text(cell.get_text())
                    link = cell.select_one('a')
                    url = urljoin(BASE_URL, link['href']) if link and link.has_attr('href') else None
                    return {'name': text, 'url': url}
            return None

        field_mappings = [
            ('director', ['director', 'സംവിധായകൻ', 'director(s)', 'directors', 'directed by', 'direction', 'സംവിധാനം']),
            ('genre', ['genre', 'വിഭാഗം', 'genres', 'category', 'categories', 'ജോണർ', 'type']),
            ('language', ['language', 'ഭാഷ', 'languages']),
            ('translatedBy', ['translator', 'translators', 'പരിഭാഷകർ', 'പരിഭാഷകൻ', 'translation', 'പരിഭാഷ', 'translated by', 'subtitled by']),
            ('imdb_rating', ['imdb rating', 'imdb', 'ഐ.എം.ഡി.ബി', 'rating', 'ratings', 'imdb score', 'score']),
            ('msone_release', ['msone release', 'msone', 'റിലീസ് നം', 'release number', 'release no', 'release', 'ms one']),
            ('certification', ['certification', 'സെർട്ടിഫിക്കേഷൻ', 'rated', 'rating', 'certificate', 'age rating'])
        ]

        for field_name, labels in field_mappings:
            field_value = get_field_data(labels)
            if field_value:
                details[field_name] = field_value
        
        return details
    except Exception as e:
        logger.exception(f"Error scraping detail page {url}")
        return None


# --- Database Functions ---

async def upsert_subtitle(conn, post_details):
    """Inserts or updates a subtitle entry in the database."""
    imdb_id = extract_imdb_id(post_details.get('imdbURL'))
    if not imdb_id:
        logger.warning(f"Skipping entry with no IMDb ID: {post_details.get('title')}")
        return 0

    unique_id = f"{imdb_id}-S{post_details['season_number']}" if post_details.get('is_series') else imdb_id

    # This is the master mapping from scraped data to database columns
    # It ensures all keys exist and are properly named
    db_record = {
        'unique_id': unique_id,
        'imdb_id': imdb_id,
        'source_url': post_details.get('source_url'),
        'scraped_at': datetime.now(),
        'title': post_details.get('title'),
        'year': int(post_details['year']) if post_details.get('year') else None,
        'is_series': post_details.get('is_series'),
        'season_number': post_details.get('season_number'),
        'series_name': post_details.get('series_name'),
        'total_seasons': None, # This will be updated later
        'srt_url': post_details.get('srtURL'),
        'poster_url': post_details.get('posterMalayalam'),
        'imdb_url': post_details.get('imdbURL'),
        'description': post_details.get('descriptionMalayalam'),
        'director': json.dumps(post_details.get('director')) if post_details.get('director') else None,
        'genre': json.dumps(post_details.get('genre')) if post_details.get('genre') else None,
        'language': json.dumps(post_details.get('language')) if post_details.get('language') else None,
        'translator': json.dumps(post_details.get('translatedBy')) if post_details.get('translatedBy') else None,
        'imdb_rating': json.dumps(post_details.get('imdb_rating')) if post_details.get('imdb_rating') else None,
        'msone_release': json.dumps(post_details.get('msone_release')) if post_details.get('msone_release') else None,
        'certification': json.dumps(post_details.get('certification')) if post_details.get('certification') else None,
        'poster_maker': json.dumps(post_details.get('poster_maker')) if post_details.get('poster_maker') else None,
    }

    # The UPSERT query
    # If a subtitle with the same unique_id exists, it updates the record.
    # Otherwise, it inserts a new one.
    query = """
        INSERT INTO subtitles (
            unique_id, imdb_id, source_url, scraped_at, title, year, is_series,
            season_number, series_name, total_seasons, srt_url, poster_url, imdb_url,
            description, director, genre, language, translator, imdb_rating,
            msone_release, certification, poster_maker
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
            $15, $16, $17, $18, $19, $20, $21, $22
        )
        ON CONFLICT (unique_id) DO UPDATE SET
            source_url = EXCLUDED.source_url,
            scraped_at = EXCLUDED.scraped_at,
            title = EXCLUDED.title,
            year = EXCLUDED.year,
            is_series = EXCLUDED.is_series,
            season_number = EXCLUDED.season_number,
            series_name = EXCLUDED.series_name,
            srt_url = EXCLUDED.srt_url,
            poster_url = EXCLUDED.poster_url,
            imdb_url = EXCLUDED.imdb_url,
            description = EXCLUDED.description,
            director = EXCLUDED.director,
            genre = EXCLUDED.genre,
            language = EXCLUDED.language,
            translator = EXCLUDED.translator,
            imdb_rating = EXCLUDED.imdb_rating,
            msone_release = EXCLUDED.msone_release,
            certification = EXCLUDED.certification,
            poster_maker = EXCLUDED.poster_maker
        RETURNING unique_id;
    """
    try:
        result = await conn.fetchval(query, *db_record.values())
        if result:
            logger.info(f"UPSERTED: {db_record['title']} ({db_record['unique_id']})")
            return 1
    except Exception as e:
        logger.error(f"Error upserting {db_record['unique_id']}: {e}")
    return 0


async def update_total_seasons(conn):
    """Queries the database to calculate and update the total_seasons for all series."""
    logger.info("Post-processing: Updating total seasons count for all series...")

    # 1. Get all distinct series names and their season counts
    query = """
        SELECT series_name, COUNT(DISTINCT season_number) as season_count
        FROM subtitles
        WHERE is_series = TRUE AND series_name IS NOT NULL
        GROUP BY series_name;
    """
    series_counts = await conn.fetch(query)

    if not series_counts:
        logger.info("No series found to update.")
        return

    # 2. Prepare and execute the update statements
    update_query = """
        UPDATE subtitles
        SET total_seasons = $1
        WHERE is_series = TRUE AND series_name = $2;
    """
    updates = []
    for record in series_counts:
        updates.append((record['season_count'], record['series_name']))

    try:
        await conn.executemany(update_query, updates)
        logger.info(f"Successfully updated total_seasons for {len(updates)} unique series.")
    except Exception as e:
        logger.error(f"Failed to update total_seasons: {e}")


async def main():
    """Main async scraper function."""
    if not DATABASE_URL:
        logger.error("DATABASE_URL environment variable not set. Cannot run scraper.")
        return

    conn = None
    newly_added_count = 0
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        logger.info("Successfully connected to the database.")

        # --- Update old series entries ---
        logger.info("Checking for and updating old series entries...")
        seven_days_ago = datetime.now() - timedelta(days=7)
        old_series_to_update = await conn.fetch(
            "SELECT unique_id, source_url, title FROM subtitles WHERE is_series = TRUE AND scraped_at < $1",
            seven_days_ago
        )

        update_count = 0
        logger.info(f"Found {len(old_series_to_update)} series entries older than 7 days to check for updates.")
        for record in old_series_to_update:
            logger.info(f"Rescraping '{record['title']}'...")
            post_details = scrape_detail_page(record['source_url'])
            if post_details:
                update_count += await upsert_subtitle(conn, post_details)
                await asyncio.sleep(0.2) # Be nice to the server
        logger.info(f"Finished updating old entries. Updated {update_count} entries.")

        # --- Scrape for new entries ---
        logger.info("Scraping for new entries...")
        current_page_url = RELEASES_URL
        page_num = 1

        existing_ids = await conn.fetch("SELECT unique_id FROM subtitles")
        existing_ids_set = {record['unique_id'] for record in existing_ids}
        logger.info(f"Loaded {len(existing_ids_set)} existing IDs from database.")

        while page_num <= MAX_PAGES:
            logger.info(f"Scraping page {page_num}/{MAX_PAGES}: {current_page_url}")
            list_soup = get_soup(current_page_url)
            if not list_soup: break

            entries = list_soup.select('article.loop-entry')
            if not entries: break

            new_on_this_page = 0
            for entry in entries:
                link_tag = entry.select_one('h2.entry-title a')
                if not (link_tag and link_tag.get('href')): continue

                detail_url = urljoin(BASE_URL, link_tag['href'])
                imdb_id_from_url = extract_imdb_id(detail_url) # Quick check

                # Simple optimization: if a movie ID is already in the DB, skip detailed scraping
                if imdb_id_from_url and imdb_id_from_url in existing_ids_set:
                    # This could miss series updates, but we handle that in the "update old series" step
                    continue

                post_details = scrape_detail_page(detail_url)
                if not post_details: continue

                added = await upsert_subtitle(conn, post_details)
                newly_added_count += added
                new_on_this_page += added
                await asyncio.sleep(0.1)

            if new_on_this_page == 0 and page_num > 5:
                logger.info("Stopping early: No new entries found on this page.")
                break

            next_page_tag = list_soup.select_one('a.next.page-numbers')
            if next_page_tag and next_page_tag.get('href'):
                current_page_url = urljoin(BASE_URL, next_page_tag['href'])
                page_num += 1
            else:
                logger.info("No next page found or reached the last page.")
                break
            await asyncio.sleep(0.2)

        logger.info(f"Scraping finished. Added/updated {newly_added_count} new entries.")
        
        # --- Post-processing ---
        await update_total_seasons(conn)

    except Exception as e:
        logger.exception(f"An error occurred during the main scraping process: {e}")
    finally:
        if conn:
            await conn.close()
            logger.info("Database connection closed.")


if __name__ == "__main__":
    asyncio.run(main())
