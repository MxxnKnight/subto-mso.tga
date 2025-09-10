import requests
from bs4 import BeautifulSoup
import json
import time
import re
import logging
import os
from urllib.parse import urljoin

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BASE_URL = "https://malayalamsubtitles.org"
RELEASES_URL = f"{BASE_URL}/releases/"

# Configuration
MAX_PAGES = int(os.environ.get("SCRAPER_MAX_PAGES", "300"))

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

def get_soup(url, timeout=20):
    """Fetch URL and return BeautifulSoup object."""
    try:
        logger.info(f"Fetching: {url}")
        response = requests.get(url, headers=HEADERS, timeout=timeout)
        response.raise_for_status()
        return BeautifulSoup(response.text, 'html.parser')
    except Exception as e:
        logger.error(f"Error fetching {url}: {e}")
        return None

def extract_imdb_id(imdb_url):
    """Extract IMDb ID from URL."""
    if not imdb_url:
        return None
    match = re.search(r'tt\d+', imdb_url)
    return match.group(0) if match else None

def extract_year(title):
    """Extract year from title."""
    year_match = re.search(r'\((\d{4})\)', title)
    return year_match.group(1) if year_match else None

def extract_season_info(title):
    """Extract season information from title."""
    # Check for season patterns
    season_patterns = [
        r'Season\s*(\d+)',
        r'സീസൺ\s*(\d+)',
        r'S0?(\d+)',
        r'സീസണ്‍\s*(\d+)'
    ]
    
    for pattern in season_patterns:
        match = re.search(pattern, title, re.IGNORECASE)
        if match:
            season_num = int(match.group(1))
            return {
                'is_series': True,
                'season_number': season_num,
                'series_name': re.sub(pattern, '', title, flags=re.IGNORECASE).strip()
            }
    
    # Check if it's a series without specific season
    if any(keyword in title.lower() for keyword in ['season', 'series', 'സീസൺ', 'സീസണ്‍']):
        return {
            'is_series': True,
            'season_number': 1,
            'series_name': title
        }
    
    return {
        'is_series': False,
        'season_number': None,
        'series_name': None
    }

def extract_msone_number(url):
    """Extract MSOne release number from URL."""
    # Pattern like /download/movie-name-2023/?wpdmdl=12345
    match = re.search(r'wpdmdl=(\d+)', url)
    return match.group(1) if match else None

def clean_text(text):
    """Clean and normalize text."""
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text.strip())

def scrape_detail_page(url):
    """Scrape comprehensive details from a movie/series page."""
    logger.info(f"Scraping detail page: {url}")
    soup = get_soup(url)
    if not soup:
        return None

    try:
        details = {}
        
        title_tag = soup.select_one('h1.entry-title') or soup.select_one('h1#release-title')
        details['title'] = clean_text(title_tag.get_text()) if title_tag else "Unknown Title"
        details['year'] = extract_year(details['title'])
        details.update(extract_season_info(details['title']))
        
        srt_tag = soup.select_one('a#download-button')
        details['srtURL'] = (srt_tag.get('data-downloadurl') or srt_tag.get('href')) if srt_tag else None
        details['msone_release_number'] = extract_msone_number(details['srtURL']) if details['srtURL'] else None
        
        poster_tag = soup.select_one('figure#release-poster img') or soup.select_one('.post-thumbnail img')
        if poster_tag and poster_tag.get('src'):
            details['posterMalayalam'] = urljoin(BASE_URL, poster_tag['src'])
        
        imdb_tag = soup.select_one('a#imdb-button') or soup.select_one('a[href*="imdb.com"]')
        details['imdbURL'] = imdb_tag['href'] if imdb_tag else None

        desc_tag = soup.select_one('div#synopsis') or soup.select_one('.entry-content')
        if desc_tag:
            details['descriptionMalayalam'] = clean_text(desc_tag.get_text(separator='\n', strip=True))

        # --- Two-pass table parsing for robustness ---
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
                    link_tag = cell.select_one('a')
                    url = urljoin(BASE_URL, link_tag['href']) if link_tag and link_tag.has_attr('href') else None
                    return {'name': text, 'url': url}
            return None

        details['director'] = get_field_data(['director', 'സംവിധായകൻ'])
        details['genre'] = get_field_data(['genre', 'വിഭാഗം'])
        details['language'] = get_field_data(['language', 'ഭാഷ'])
        details['translatedBy'] = get_field_data(['translator', 'translators', 'പരിഭാഷകർ', 'പരിഭാഷകൻ', 'translation', 'പരിഭാഷ'])

        def get_simple_field(labels):
            for label in labels:
                if label in table_data:
                    return clean_text(table_data[label].get_text())
            return None

        details['imdb_rating'] = get_simple_field(['imdb rating', 'റേറ്റിംഗ്'])
        details['certification'] = get_simple_field(['certification', 'സർട്ടിഫിക്കേഷൻ'])

        poster_credit_tag = soup.select_one('figure#release-poster figcaption a')
        if poster_credit_tag:
            details['poster_maker'] = {'name': clean_text(poster_credit_tag.get_text()), 'url': poster_credit_tag.get('href')}
        else:
            poster_credit_text = soup.select_one('figure#release-poster figcaption')
            if poster_credit_text:
                details['poster_maker'] = {'name': clean_text(poster_credit_text.get_text()), 'url': None}

        # --- Set defaults for any fields that were not found ---
        defaults = {
            'language': {'name': 'Unknown', 'url': None}, 'director': {'name': 'Unknown', 'url': None},
            'genre': {'name': 'Unknown', 'url': None}, 'certification': 'Not Rated', 'imdb_rating': 'N/A',
            'translatedBy': {'name': 'Unknown', 'url': None}, 'poster_maker': {'name': 'Unknown', 'url': None}
        }
        for key, default_value in defaults.items():
            if not details.get(key):
                details[key] = default_value

        details['scraped_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
        details['source_url'] = url
        
        logger.info(f"Successfully scraped: {details['title']}")
        return details

    except Exception as e:
        logger.exception(f"Error scraping detail page {url}: {e}")
        return None

def main():
    """Main scraper function."""
    logger.info("Starting Malayalam subtitle scraper...")

    try:
        with open('db.json', 'r', encoding='utf-8') as f:
            final_db = json.load(f)
        logger.info(f"Loaded {len(final_db)} entries from existing db.json")
    except (FileNotFoundError, json.JSONDecodeError):
        final_db = {}
        logger.info("No existing db.json found or it's invalid. Starting fresh.")

    current_page_url = RELEASES_URL
    newly_added_count = 0
    skipped_count = 0

    for page_num in range(1, MAX_PAGES + 1):
        logger.info(f"Scraping page {page_num}/{MAX_PAGES}: {current_page_url}")
        
        list_soup = get_soup(current_page_url)
        if not list_soup:
            logger.error(f"Failed to load page {page_num}, stopping.")
            break

        entries = list_soup.select('article.loop-entry') or list_soup.select('article')
        if not entries:
            logger.warning("No entries found on this page, stopping.")
            break
            
        logger.info(f"Found {len(entries)} entries on page {page_num}")
        
        new_on_this_page = 0
        for entry in entries:
            link_tag = entry.select_one('h2.entry-title a') or entry.select_one('a[href]')
            if not (link_tag and link_tag.get('href')):
                continue

            detail_url = urljoin(BASE_URL, link_tag['href'])

            post_details = scrape_detail_page(detail_url)
            if not post_details:
                continue

            imdb_id = extract_imdb_id(post_details.get('imdbURL'))
            if imdb_id and imdb_id not in final_db:
                final_db[imdb_id] = post_details
                newly_added_count += 1
                new_on_this_page += 1
                logger.info(f"NEW: {post_details['title']} ({imdb_id})")
            elif imdb_id:
                logger.debug(f"EXISTING: {post_details['title']} ({imdb_id})")
            else:
                logger.warning(f"Skipping entry with no IMDb ID: {post_details.get('title')}")
                skipped_count += 1

            time.sleep(0.5) # Shorter delay between entries

        if new_on_this_page == 0 and page_num > 1: # Stop if a page has no new entries (after the first page)
             logger.info(f"Stopping early on page {page_num}: No new entries found.")
             break

        next_page_tag = list_soup.select_one('a.next.page-numbers')
        if next_page_tag and next_page_tag.get('href'):
            current_page_url = urljoin(BASE_URL, next_page_tag['href'])
        else:
            logger.info("No next page found, stopping.")
            break

        time.sleep(1) # Shorter delay between pages

    logger.info(f"Scraping finished. Added {newly_added_count} new entries.")
    logger.info(f"Total entries in database: {len(final_db)}")

    # Rebuild the series_db from the final, combined database
    series_db = {}
    for imdb_id, entry in final_db.items():
        if entry.get('is_series') and entry.get('series_name'):
            series_name = entry['series_name']
            if series_name not in series_db:
                series_db[series_name] = {}

            season_num = entry.get('season_number', 1)
            series_db[series_name][season_num] = imdb_id

            # Update total seasons count
            entry['total_seasons'] = len(series_db[series_name])

    # Write database
    try:
        # Backup existing database
        if os.path.exists('db.json'):
            os.rename('db.json', 'db.json.backup')
            logger.info("Created backup of existing database")
        
        with open('db.json', 'w', encoding='utf-8') as f:
            json.dump(final_db, f, ensure_ascii=False, indent=2)
        
        # Also save series mapping
        with open('series_db.json', 'w', encoding='utf-8') as f:
            json.dump(series_db, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Successfully created db.json with {len(final_db)} entries")
        logger.info(f"Created series_db.json with {len(series_db)} series")
        logger.info(f"Skipped {skipped_count} entries without IMDb IDs")
        
        # Verify file
        size = os.path.getsize('db.json')
        logger.info(f"Database file size: {size:,} bytes")
        
    except Exception as e:
        logger.error(f"Error writing database: {e}")

if __name__ == "__main__":
    main()
