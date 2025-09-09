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
# Set a high default for cron jobs, but allow override. 15 pages is roughly 60 entries.
MAX_PAGES = int(os.environ.get("SCRAPER_MAX_PAGES", "200"))
SCRAPER_RUNNING = False

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
    global SCRAPER_RUNNING
    if not SCRAPER_RUNNING:
        return None
        
    logger.info(f"Scraping detail page: {url}")
    soup = get_soup(url)
    if not soup:
        return None

    try:
        details = {}
        
        # Basic title
        title_tag = soup.select_one('h1#release-title') or soup.select_one('h1.entry-title') or soup.select_one('h1')
        raw_title = title_tag.get_text(strip=True) if title_tag else "Unknown Title"
        details['title'] = clean_text(raw_title)
        
        # Extract year from title
        details['year'] = extract_year(raw_title)
        
        # Extract season information
        season_info = extract_season_info(raw_title)
        details.update(season_info)
        
        # MSOne release number
        srt_tag = soup.select_one('a#download-button')
        if srt_tag:
            download_url = srt_tag.get('data-downloadurl') or srt_tag.get('href')
            details['srtURL'] = download_url
            details['msone_release_number'] = extract_msone_number(download_url) if download_url else None
        else:
            details['srtURL'] = None
            details['msone_release_number'] = None
        
        # Poster
        poster_tag = soup.select_one('figure#release-poster img') or soup.select_one('.post-thumbnail img')
        if poster_tag and poster_tag.get('src'):
            poster_url = poster_tag['src']
            if poster_url.startswith('/'):
                poster_url = urljoin(BASE_URL, poster_url)
            details['posterMalayalam'] = poster_url
        else:
            details['posterMalayalam'] = None
        
        # Poster maker (credit)
        poster_credit = soup.select_one('figure#release-poster figcaption')
        details['poster_maker'] = clean_text(poster_credit.get_text()) if poster_credit else None
        
        # Synopsis/Description
        desc_tag = soup.select_one('div#synopsis') or soup.select_one('.entry-content')
        if desc_tag:
            description = clean_text(desc_tag.get_text())
            details['descriptionMalayalam'] = description[:2000] + "..." if len(description) > 2000 else description
        else:
            details['descriptionMalayalam'] = "No description available"
        
        # IMDb URL
        imdb_tag = soup.select_one('a#imdb-button') or soup.select_one('a[href*="imdb.com"]')
        details['imdbURL'] = imdb_tag['href'] if imdb_tag else None
        
        # Parse release details table
        details_table = soup.select_one('#release-details-table tbody')
        if details_table:
            rows = details_table.select('tr')
            
            for row in rows:
                cells = row.select('td')
                if len(cells) >= 2:
                    # Clean the label thoroughly for stricter matching
                    label = clean_text(cells[0].get_text()).lower().strip().replace(':', '')
                    value = clean_text(cells[1].get_text())
                    
                    if label == 'language' or label == 'ഭാഷ':
                        details['language'] = value
                    elif label == 'director' or label == 'സംവിധായകൻ':
                        details['director'] = value
                    elif label == 'genre' or label == 'വിഭാഗം':
                        details['genre'] = value
                    elif label == 'certification' or label == 'സർട്ടിഫിക്കേഷൻ':
                        details['certification'] = value
                    elif label == 'imdb rating' or label == 'റേറ്റിംഗ്':
                        details['imdb_rating'] = value
                    elif label.startswith('translat') or label == 'പരിഭാഷകർ': # translations, translator, etc.
                        # Translator info
                        translator_link = cells[1].select_one('a')
                        if translator_link:
                            details['translatedBy'] = {
                                'name': clean_text(translator_link.get_text()),
                                'url': translator_link.get('href')
                            }
                        else:
                            details['translatedBy'] = {
                                'name': value,
                                'url': None
                            }
        
        # Set defaults for missing fields
        defaults = {
            'language': 'Malayalam',
            'director': 'Unknown',
            'genre': 'Unknown',
            'certification': 'Not Rated',
            'imdb_rating': 'N/A',
            'translatedBy': {'name': 'Unknown', 'url': None}
        }
        
        for key, default_value in defaults.items():
            if key not in details:
                details[key] = default_value
        
        # Additional metadata
        details['scraped_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
        details['source_url'] = url
        
        logger.info(f"Successfully scraped: {details['title']} ({details.get('year', 'Unknown')})")
        return details

    except Exception as e:
        logger.error(f"Error scraping detail page {url}: {e}")
        return None

def get_series_seasons(series_name, all_results):
    """Get all seasons for a series."""
    seasons = {}
    for result in all_results:
        if result.get('is_series') and result.get('series_name'):
            # Normalize series name for comparison
            result_series = re.sub(r'[^\w\s]', '', result['series_name'].lower())
            search_series = re.sub(r'[^\w\s]', '', series_name.lower())
            
            if result_series in search_series or search_series in result_series:
                season_num = result.get('season_number', 1)
                seasons[season_num] = result
    
    return seasons

def stop_scraper():
    """Stop the scraper gracefully."""
    global SCRAPER_RUNNING
    SCRAPER_RUNNING = False
    logger.info("Scraper stop requested - will finish current entry")

def start_scraper():
    """Start the scraper."""
    global SCRAPER_RUNNING
    SCRAPER_RUNNING = True
    return main()

def main():
    """Main scraper function."""
    global SCRAPER_RUNNING
    SCRAPER_RUNNING = True
    
    logger.info("Starting enhanced Malayalam subtitle scraper...")
    
    all_results = []
    current_page_url = RELEASES_URL
    processed_count = 0

    for page_num in range(1, MAX_PAGES + 1):
        if not SCRAPER_RUNNING:
            logger.info("Scraper stopped by user request")
            break
            
        logger.info(f"Scraping page {page_num}/{MAX_PAGES}: {current_page_url}")
        
        list_soup = get_soup(current_page_url)
        if not list_soup:
            logger.error(f"Failed to load page {page_num}")
            break

        # Find entries
        entries = list_soup.select('article.loop-entry') or list_soup.select('article')
        if not entries:
            logger.warning("No entries found on this page")
            break
            
        logger.info(f"Found {len(entries)} entries on page {page_num}")
        
        for i, entry in enumerate(entries):
            if not SCRAPER_RUNNING:
                logger.info("Scraper stopped - finishing current page")
                break
                
            logger.info(f"Processing entry {i+1}/{len(entries)} (Total: {processed_count + 1})")
            
            link_tag = entry.select_one('h2.entry-title a') or entry.select_one('a[href]')
            if link_tag and link_tag.get('href'):
                detail_url = link_tag['href']
                
                # Ensure absolute URL
                if detail_url.startswith('/'):
                    detail_url = BASE_URL + detail_url
                
                post_details = scrape_detail_page(detail_url)
                if post_details:
                    all_results.append(post_details)
                    processed_count += 1
                
                # Respectful delay
                time.sleep(1.5)

        # Find next page
        next_page_tag = list_soup.select_one('a.next.page-numbers')
        if next_page_tag and next_page_tag.get('href'):
            current_page_url = next_page_tag['href']
            if current_page_url.startswith('/'):
                current_page_url = BASE_URL + current_page_url
        else:
            logger.info("No next page found")
            break

        # Delay between pages
        time.sleep(3)

    logger.info(f"Scraped {len(all_results)} total entries")

    # Process series and create database
    final_db = {}
    series_db = {}  # Separate tracking for series
    skipped = 0
    
    for result in all_results:
        imdb_id = extract_imdb_id(result.get('imdbURL'))
        if imdb_id:
            if imdb_id not in final_db:
                # Add to main database
                final_db[imdb_id] = result
                
                # Track series separately
                if result.get('is_series') and result.get('series_name'):
                    series_name = result['series_name']
                    if series_name not in series_db:
                        series_db[series_name] = {}
                    
                    season_num = result.get('season_number', 1)
                    series_db[series_name][season_num] = imdb_id
                    
                    # Update total seasons count
                    result['total_seasons'] = len(series_db[series_name])
                    final_db[imdb_id] = result  # Update with season count
            else:
                skipped += 1
        else:
            logger.warning(f"No IMDb ID for: {result.get('title')}")
            skipped += 1

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
        logger.info(f"Skipped {skipped} entries without IMDb IDs")
        
        # Verify file
        size = os.path.getsize('db.json')
        logger.info(f"Database file size: {size:,} bytes")
        
        return True
        
    except Exception as e:
        logger.error(f"Error writing database: {e}")
        return False
    finally:
        SCRAPER_RUNNING = False

if __name__ == "__main__":
    success = start_scraper()
    if success:
        logger.info("Scraper completed successfully!")
    else:
        logger.error("Scraper failed!")
        exit(1)
