import requests
from bs4 import BeautifulSoup
import json
import time
import re
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BASE_URL = "https://malayalamsubtitles.org"
RELEASES_URL = f"{BASE_URL}/releases/"
MAX_PAGES = 3  # Limited for Render build time

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

def get_soup(url, timeout=15):
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

def scrape_detail_page(url):
    """Scrape details from a single movie page."""
    logger.info(f"Scraping detail page: {url}")
    soup = get_soup(url)
    if not soup:
        return None

    try:
        details = {}
        
        # Title
        title_tag = soup.select_one('h1#release-title') or soup.select_one('h1.entry-title') or soup.select_one('h1')
        details['title'] = title_tag.get_text(strip=True) if title_tag else "Unknown Title"

        # Poster
        poster_tag = soup.select_one('figure#release-poster img') or soup.select_one('.post-thumbnail img')
        details['posterMalayalam'] = poster_tag['src'] if poster_tag and poster_tag.get('src') else None

        # Description
        desc_tag = soup.select_one('div#synopsis') or soup.select_one('.entry-content')
        if desc_tag:
            description = desc_tag.get_text(strip=True)
            details['descriptionMalayalam'] = description[:1000] + "..." if len(description) > 1000 else description
        else:
            details['descriptionMalayalam'] = "No description available"

        # IMDb URL
        imdb_tag = soup.select_one('a#imdb-button') or soup.select_one('a[href*="imdb.com"]')
        details['imdbURL'] = imdb_tag['href'] if imdb_tag else None

        # SRT URL
        srt_tag = soup.select_one('a#download-button')
        if srt_tag:
            details['srtURL'] = srt_tag.get('data-downloadurl') or srt_tag.get('href')
        else:
            details['srtURL'] = None

        # Translator
        translator_tag = soup.select_one('#release-details-table a[href*="/tag/"]')
        if translator_tag:
            details['translatedBy'] = {
                'name': translator_tag.get_text(strip=True),
                'url': translator_tag['href']
            }
        else:
            details['translatedBy'] = {'name': 'Unknown', 'url': None}

        logger.info(f"Successfully scraped: {details['title']}")
        return details

    except Exception as e:
        logger.error(f"Error scraping detail page {url}: {e}")
        return None

def main():
    """Main scraper function."""
    logger.info("Starting Malayalam subtitle scraper...")
    
    all_results = []
    current_page_url = RELEASES_URL

    for page_num in range(1, MAX_PAGES + 1):
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
            logger.info(f"Processing entry {i+1}/{len(entries)}")
            
            link_tag = entry.select_one('h2.entry-title a') or entry.select_one('a[href]')
            if link_tag and link_tag.get('href'):
                detail_url = link_tag['href']
                
                # Ensure absolute URL
                if detail_url.startswith('/'):
                    detail_url = BASE_URL + detail_url
                
                post_details = scrape_detail_page(detail_url)
                if post_details:
                    all_results.append(post_details)
                
                # Be respectful to the server
                time.sleep(1)

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
        time.sleep(2)

    logger.info(f"Scraped {len(all_results)} total entries")

    # Create database
    final_db = {}
    skipped = 0
    
    for result in all_results:
        imdb_id = extract_imdb_id(result.get('imdbURL'))
        if imdb_id:
            if imdb_id not in final_db:  # Avoid duplicates
                final_db[imdb_id] = result
            else:
                skipped += 1
        else:
            logger.warning(f"No IMDb ID for: {result.get('title')}")
            skipped += 1

    # Write database
    try:
        with open('db.json', 'w', encoding='utf-8') as f:
            json.dump(final_db, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Successfully created db.json with {len(final_db)} entries")
        logger.info(f"Skipped {skipped} entries without IMDb IDs")
        
        # Verify file exists and has content
        import os
        if os.path.exists('db.json'):
            size = os.path.getsize('db.json')
            logger.info(f"Database file size: {size} bytes")
        
        return True
        
    except Exception as e:
        logger.error(f"Error writing database: {e}")
        return False

if __name__ == "__main__":
    success = main()
    if success:
        logger.info("Scraper completed successfully!")
    else:
        logger.error("Scraper failed!")
        exit(1)
