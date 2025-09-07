#!/usr/bin/env python3
"""
Malayalam Subtitle Scraper
Scrapes subtitle data from malayalamsubtitles.org and creates db.json
"""

import requests
from bs4 import BeautifulSoup
import json
import time
import re
import sys
import logging
from urllib.parse import urljoin, urlparse
from pathlib import Path
import os

# --- Configuration ---
BASE_URL = "https://malayalamsubtitles.org"
RELEASES_URL = f"{BASE_URL}/releases/"

# Environment-based configuration
ENVIRONMENT = os.environ.get("ENVIRONMENT", "development")
MAX_PAGES = int(os.environ.get("SCRAPER_MAX_PAGES", "10" if ENVIRONMENT == "production" else "3"))
REQUEST_DELAY = float(os.environ.get("SCRAPER_DELAY", "1.0"))
DB_FILE = os.environ.get("DB_FILE", "db.json")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Accept-Encoding': 'gzip, deflate, br',
    'DNT': '1',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1'
}

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('scraper.log', mode='a', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

class MalayalamSubtitleScraper:
    """Malayalam Subtitle Scraper Class."""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.scraped_urls = set()
        self.failed_urls = set()
        
    def get_soup(self, url, retries=3):
        """Fetches a URL and returns a BeautifulSoup object with retry logic."""
        for attempt in range(retries):
            try:
                logger.info(f"Fetching {url} (attempt {attempt + 1}/{retries})")
                response = self.session.get(url, timeout=30)
                response.raise_for_status()
                
                # Check if response is HTML
                content_type = response.headers.get('content-type', '').lower()
                if 'html' not in content_type:
                    logger.warning(f"Non-HTML response for {url}: {content_type}")
                    return None
                
                return BeautifulSoup(response.text, 'html.parser')
                
            except requests.exceptions.Timeout:
                logger.warning(f"Timeout for {url} (attempt {attempt + 1}/{retries})")
            except requests.exceptions.RequestException as e:
                logger.error(f"Request error for {url}: {e}")
                if attempt == retries - 1:
                    self.failed_urls.add(url)
            except Exception as e:
                logger.error(f"Unexpected error fetching {url}: {e}")
                
            if attempt < retries - 1:
                wait_time = (attempt + 1) * 2  # Exponential backoff
                logger.info(f"Waiting {wait_time}s before retry...")
                time.sleep(wait_time)
        
        return None

    def extract_imdb_id(self, imdb_url):
        """Extracts IMDb ID from a URL."""
        if not imdb_url:
            return None
        
        # Handle both full URLs and partial URLs
        if imdb_url.startswith('/'):
            imdb_url = f"https://www.imdb.com{imdb_url}"
        
        match = re.search(r'tt\d+', imdb_url)
        return match.group(0) if match else None

    def validate_url(self, url):
        """Validates if URL is properly formatted."""
        try:
            result = urlparse(url)
            return all([result.scheme, result.netloc])
        except:
            return False

    def scrape_detail_page(self, url):
        """Scrapes the details from a single movie/series page."""
        if url in self.scraped_urls:
            logger.info(f"Already scraped: {url}")
            return None
            
        if not self.validate_url(url):
            logger.error(f"Invalid URL: {url}")
            return None
        
        logger.info(f"Scraping detail page: {url}")
        soup = self.get_soup(url)
        if not soup:
            return None

        self.scraped_urls.add(url)
        details = {}
        
        try:
            # Title - multiple selectors as fallback
            title_selectors = [
                'h1#release-title',
                'h1.entry-title',
                '.post-title h1',
                'h1'
            ]
            
            title = None
            for selector in title_selectors:
                title_tag = soup.select_one(selector)
                if title_tag:
                    title = title_tag.get_text(separator=' ', strip=True)
                    break
            
            details['title'] = title or "Unknown Title"

            # Poster - multiple selectors
            poster_selectors = [
                'figure#release-poster img',
                '.post-thumbnail img',
                '.entry-content img',
                'img[src*="poster"]'
            ]
            
            poster_url = None
            for selector in poster_selectors:
                poster_tag = soup.select_one(selector)
                if poster_tag and poster_tag.get('src'):
                    poster_url = poster_tag['src']
                    # Convert relative URLs to absolute
                    if poster_url.startswith('/'):
                        poster_url = urljoin(BASE_URL, poster_url)
                    break
            
            details['posterMalayalam'] = poster_url

            # Description - multiple selectors
            desc_selectors = [
                'div#synopsis',
                '.entry-content',
                '.post-content',
                '.synopsis'
            ]
            
            description = None
            for selector in desc_selectors:
                desc_tag = soup.select_one(selector)
                if desc_tag:
                    # Clean description text
                    description = desc_tag.get_text(strip=True)
                    # Remove excessive whitespace
                    description = re.sub(r'\s+', ' ', description)
                    # Limit length
                    if len(description) > 2000:
                        description = description[:2000] + "..."
                    break
            
            details['descriptionMalayalam'] = description or "No description available"

            # IMDb URL - multiple selectors
            imdb_selectors = [
                'a#imdb-button',
                'a[href*="imdb.com"]',
                'a[title*="IMDb"]'
            ]
            
            imdb_url = None
            for selector in imdb_selectors:
                imdb_tag = soup.select_one(selector)
                if imdb_tag and imdb_tag.get('href'):
                    imdb_url = imdb_tag['href']
                    # Ensure proper IMDb URL format
                    if 'imdb.com' not in imdb_url:
                        continue
                    break
            
            details['imdbURL'] = imdb_url

            # SRT Download URL - multiple selectors
            srt_selectors = [
                'a#download-button',
                'a[href*="download"]',
                'a[data-downloadurl]',
                '.download-link a'
            ]
            
            srt_url = None
            for selector in srt_selectors:
                srt_tag = soup.select_one(selector)
                if srt_tag:
                    # Try different attributes
                    for attr in ['data-downloadurl', 'href']:
                        if srt_tag.get(attr):
                            srt_url = srt_tag[attr]
                            break
                    if srt_url:
                        break
            
            # Convert relative URLs to absolute
            if srt_url and srt_url.startswith('/'):
                srt_url = urljoin(BASE_URL, srt_url)
            
            details['srtURL'] = srt_url

            # Translator information - multiple selectors
            translator_selectors = [
                '#release-details-table tbody tr:nth-of-type(3) td:nth-of-type(2) a',
                '.translator-info a',
                'a[href*="/tag/"]'
            ]
            
            translator_info = {'name': 'Unknown', 'url': None}
            for selector in translator_selectors:
                translator_tag = soup.select_one(selector)
                if translator_tag and translator_tag.get_text(strip=True):
                    translator_info = {
                        'name': translator_tag.get_text(strip=True),
                        'url': translator_tag.get('href')
                    }
                    break
            
            details['translatedBy'] = translator_info

            # Additional metadata
            details['scrapedAt'] = time.strftime('%Y-%m-%d %H:%M:%S')
            details['sourceUrl'] = url

            logger.info(f"Successfully scraped: {details['title']}")
            return details

        except Exception as e:
            logger.error(f"Error scraping detail page {url}: {e}")
            return None

    def scrape_listing_page(self, page_url):
        """Scrapes a listing page and returns detail page URLs."""
        logger.info(f"Scraping listing page: {page_url}")
        soup = self.get_soup(page_url)
        if not soup:
            return [], None

        # Find movie/series entries
        entry_selectors = [
            'article.loop-entry',
            '.post-item',
            '.movie-item',
            'article'
        ]
        
        entries = []
        for selector in entry_selectors:
            entries = soup.select(selector)
            if entries:
                break
        
        detail_urls = []
        for entry in entries:
            # Find link to detail page
            link_selectors = [
                'h2.entry-title a',
                '.post-title a',
                '.movie-title a',
                'a[href]'
            ]
            
            for selector in link_selectors:
                link_tag = entry.select_one(selector)
                if link_tag and link_tag.get('href'):
                    detail_url = link_tag['href']
                    if detail_url.startswith('/'):
                        detail_url = urljoin(BASE_URL, detail_url)
                    detail_urls.append(detail_url)
                    break

        # Find next page URL
        next_page_selectors = [
            'a.next.page-numbers',
            '.next-page a',
            '.pagination .next',
            'a[rel="next"]'
        ]
        
        next_page_url = None
        for selector in next_page_selectors:
            next_tag = soup.select_one(selector)
            if next_tag and next_tag.get('href'):
                next_page_url = next_tag['href']
                if next_page_url.startswith('/'):
                    next_page_url = urljoin(BASE_URL, next_page_url)
                break

        logger.info(f"Found {len(detail_urls)} entries on listing page")
        return detail_urls, next_page_url

    def run(self):
        """Main scraping function."""
        logger.info("Starting Malayalam Subtitle Scraper...")
        logger.info(f"Configuration: MAX_PAGES={MAX_PAGES}, DELAY={REQUEST_DELAY}s")
        
        all_results = []
        current_page_url = RELEASES_URL
        processed_pages = 0

        try:
            for page_num in range(1, MAX_PAGES + 1):
                if not current_page_url:
                    break
                
                logger.info(f"\n--- Scraping page {page_num}/{MAX_PAGES} ---")
                detail_urls, next_page_url = self.scrape_listing_page(current_page_url)
                
                if not detail_urls:
                    logger.warning("No detail URLs found. Stopping.")
                    break
                
                # Scrape each detail page
                for i, detail_url in enumerate(detail_urls, 1):
                    logger.info(f"Processing detail page {i}/{len(detail_urls)}")
                    post_details = self.scrape_detail_page(detail_url)
                    
                    if post_details:
                        all_results.append(post_details)
                    
                    # Respectful delay
                    if i < len(detail_urls):
                        time.sleep(REQUEST_DELAY)
                
                processed_pages += 1
                current_page_url = next_page_url
                
                # Longer delay between pages
                if page_num < MAX_PAGES and current_page_url:
                    time.sleep(REQUEST_DELAY * 2)

        except KeyboardInterrupt:
            logger.info("Scraping interrupted by user")
        except Exception as e:
            logger.error(f"Unexpected error during scraping: {e}")

        logger.info(f"\nScraping completed:")
        logger.info(f"- Pages processed: {processed_pages}")
        logger.info(f"- Total entries scraped: {len(all_results)}")
        logger.info(f"- Failed URLs: {len(self.failed_urls)}")

        # Create database
        return self.create_database(all_results)

    def create_database(self, results):
        """Creates the database from scraped results."""
        logger.info("Creating database...")
        
        final_db = {}
        skipped_no_imdb = 0
        duplicates = 0
        
        for result in results:
            imdb_id = self.extract_imdb_id(result.get('imdbURL'))
            
            if imdb_id:
                if imdb_id in final_db:
                    duplicates += 1
                    logger.debug(f"Duplicate IMDb ID {imdb_id}: {result.get('title')}")
                else:
                    final_db[imdb_id] = result
            else:
                skipped_no_imdb += 1
                logger.warning(f"No IMDb ID found for: {result.get('title')}")

        logger.info(f"Database creation completed:")
        logger.info(f"- Unique entries: {len(final_db)}")
        logger.info(f"- Duplicates skipped: {duplicates}")
        logger.info(f"- No IMDb ID: {skipped_no_imdb}")

        # Write to file
        try:
            # Create backup of existing file
            db_path = Path(DB_FILE)
            if db_path.exists():
                backup_path = Path(f"{DB_FILE}.backup")
                db_path.rename(backup_path)
                logger.info(f"Created backup: {backup_path}")

            with open(DB_FILE, 'w', encoding='utf-8') as f:
                json.dump(final_db, f, ensure_ascii=False, indent=2)
            
            logger.info(f"Successfully created {DB_FILE} with {len(final_db)} entries")
            
            # Verify file was written correctly
            file_size = db_path.stat().st_size
            logger.info(f"Database file size: {file_size:,} bytes")
            
            return True
            
        except Exception as e:
            logger.error(f"Error writing database file: {e}")
            return False

def main():
    """Main function."""
    try:
        scraper = MalayalamSubtitleScraper()
        success = scraper.run()
        
        if success:
            logger.info("✅ Scraping completed successfully!")
            sys.exit(0)
        else:
            logger.error("❌ Scraping failed!")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
