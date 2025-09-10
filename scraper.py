import requests
from bs4 import BeautifulSoup
import json
import time
import re
import logging
import os
from urllib.parse import urljoin

# --- Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BASE_URL = "https://malayalamsubtitles.org"
RELEASES_URL = f"{BASE_URL}/releases/"
MAX_PAGES = int(os.environ.get("SCRAPER_MAX_PAGES", "300"))
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}

# --- Helper Functions ---
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
    for pattern in patterns:
        match = re.search(pattern, title, re.IGNORECASE)
        if match:
            return {'is_series': True, 'season_number': int(match.group(1)), 'series_name': re.sub(pattern, '', title, flags=re.IGNORECASE).strip()}
    if any(keyword in title.lower() for keyword in ['season', 'series', 'സീസൺ', 'സീസണ്‍']):
        return {'is_series': True, 'season_number': 1, 'series_name': title}
    return {'is_series': False, 'season_number': None, 'series_name': None}

def scrape_detail_page(url):
    """Scrapes comprehensive details from a movie/series page."""
    logger.info(f"Scraping detail page: {url}")
    soup = get_soup(url)
    if not soup: return None

    try:
        details = {'source_url': url, 'scraped_at': time.strftime('%Y-%m-%d %H:%M:%S')}
        
        title_tag = soup.select_one('h1.entry-title, h1#release-title')
        details['title'] = clean_text(title_tag.get_text()) if title_tag else "Unknown Title"
        details['year'] = extract_year(details['title'])
        details.update(extract_season_info(details['title']))
        
        srt_tag = soup.select_one('a#download-button')
        details['srtURL'] = (srt_tag.get('data-downloadurl') or srt_tag.get('href')) if srt_tag else None
        
        poster_tag = soup.select_one('figure#release-poster img')
        if poster_tag and poster_tag.get('src'):
            details['posterMalayalam'] = urljoin(BASE_URL, poster_tag['src'])
        
        imdb_tag = soup.select_one('a#imdb-button')
        details['imdbURL'] = imdb_tag['href'] if imdb_tag else None

        desc_tag = soup.select_one('div#synopsis')
        if desc_tag: details['descriptionMalayalam'] = clean_text(desc_tag.get_text(separator='\n', strip=True))

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

        details['director'] = get_field_data(['director', 'സംവിധായകൻ'])
        details['genre'] = get_field_data(['genre', 'വിഭാഗം'])
        details['language'] = get_field_data(['language', 'ഭാഷ'])
        details['translatedBy'] = get_field_data(['translator', 'translators', 'പരിഭാഷകർ', 'പരിഭാഷകൻ', 'translation', 'പരിഭാഷ'])

        poster_credit_tag = soup.select_one('figure#release-poster figcaption a')
        if poster_credit_tag:
            details['poster_maker'] = {'name': clean_text(poster_credit_tag.get_text()), 'url': poster_credit_tag.get('href')}
        
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
        logger.info(f"Loaded {len(final_db)} existing entries from db.json")
    except (FileNotFoundError, json.JSONDecodeError):
        final_db = {}
        logger.info("No existing db.json found or it's invalid. Starting fresh.")

    current_page_url = RELEASES_URL
    newly_added_count = 0

    for page_num in range(1, MAX_PAGES + 1):
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
            post_details = scrape_detail_page(detail_url)
            if not post_details: continue

            imdb_id = extract_imdb_id(post_details.get('imdbURL'))
            if not imdb_id:
                logger.warning(f"Skipping entry with no IMDb ID: {post_details.get('title')}")
                continue

            unique_id = f"{imdb_id}-S{post_details['season_number']}" if post_details.get('is_series') else imdb_id

            if unique_id not in final_db:
                final_db[unique_id] = post_details
                newly_added_count += 1
                new_on_this_page += 1
                logger.info(f"NEW: {post_details['title']} ({unique_id})")

            time.sleep(0.2)

        if new_on_this_page == 0 and page_num > 5: # Stop if a page has no new entries after checking a few pages
             logger.info(f"Stopping early on page {page_num}: No new entries found.")
             break

        next_page_tag = list_soup.select_one('a.next.page-numbers')
        if next_page_tag and next_page_tag.get('href'):
            current_page_url = urljoin(BASE_URL, next_page_tag['href'])
        else:
            logger.info("No next page found, stopping.")
            break
        time.sleep(0.5)

    logger.info(f"Scraping finished. Added {newly_added_count} new entries.")
    logger.info(f"Total entries in database: {len(final_db)}")

    series_db = {}
    for unique_id, entry in final_db.items():
        if entry.get('is_series') and entry.get('series_name'):
            series_name = entry['series_name']
            if series_name not in series_db:
                series_db[series_name] = {}
            season_num = entry.get('season_number', 1)
            series_db[series_name][season_num] = unique_id
            entry['total_seasons'] = len(series_db[series_name])

    try:
        if os.path.exists('db.json'):
            os.rename('db.json', 'db.json.backup')
        with open('db.json', 'w', encoding='utf-8') as f:
            json.dump(final_db, f, ensure_ascii=False, indent=2)
        
        if os.path.exists('series_db.json'):
            os.rename('series_db.json', 'series_db.json.backup')
        with open('series_db.json', 'w', encoding='utf-8') as f:
            json.dump(series_db, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Successfully created db.json and series_db.json")
    except Exception as e:
        logger.error(f"Error writing database: {e}")

if __name__ == "__main__":
    main()
