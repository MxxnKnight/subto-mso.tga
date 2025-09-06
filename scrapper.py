import requests
from bs4 import BeautifulSoup
import json
import time
import re

BASE_URL = "https://malayalamsubtitles.org"
RELEASES_URL = f"{BASE_URL}/releases/"
MAX_PAGES = 3 # Limit pagination for development

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

def get_soup(url):
    """Fetches a URL and returns a BeautifulSoup object."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        return BeautifulSoup(response.text, 'html.parser')
    except requests.RequestException as e:
        print(f"Error fetching {url}: {e}")
        return None

def extract_imdb_id(imdb_url):
    """Extracts IMDb ID from a URL."""
    if not imdb_url:
        return None
    match = re.search(r'tt\d+', imdb_url)
    return match.group(0) if match else None

def scrape_detail_page(url):
    """Scrapes the details from a single movie/series page."""
    print(f"  Scraping detail page: {url}")
    soup = get_soup(url)
    if not soup:
        return None

    details = {}
    
    # Title
    title_tag = soup.select_one('h1#release-title')
    details['title'] = title_tag.get_text(separator=' ', strip=True) if title_tag else "N/A"

    # Poster
    poster_tag = soup.select_one('figure#release-poster img')
    details['posterMalayalam'] = poster_tag['src'] if poster_tag else None

    # Description
    desc_tag = soup.select_one('div#synopsis')
    details['descriptionMalayalam'] = desc_tag.get_text(strip=True) if desc_tag else "N/A"

    # MSone Release Number
    release_no_tag = soup.select_one('h4#release-number')
    details['msoneReleaseNumber'] = release_no_tag.get_text(strip=True) if release_no_tag else "N/A"

    # IMDb URL
    imdb_tag = soup.select_one('a#imdb-button')
    details['imdbURL'] = imdb_tag['href'] if imdb_tag else None

    # SRT URL
    srt_tag = soup.select_one('a#download-button')
    details['srtURL'] = srt_tag.get('data-downloadurl') or srt_tag.get('href') if srt_tag else None

    # --- Table Data ---
    details['language'] = "N/A"
    details['production'] = "N/A"
    details['genre'] = "N/A"
    details['translatedBy'] = {'name': 'N/A', 'url': None}

    try:
        table = soup.select_one('table#release-details-table')
        if table:
            rows = table.find_all('tr')
            for row in rows:
                cells = row.find_all('td')
                if len(cells) == 2:
                    key = cells[0].get_text(strip=True)
                    value_cell = cells[1]

                    if 'ഭാഷ' in key:
                        details['language'] = value_cell.get_text(strip=True)
                    if 'നിർമ്മാണം' in key:
                        details['production'] = value_cell.get_text(strip=True)
                    if 'ജോണർ' in key:
                        details['genre'] = value_cell.get_text(strip=True)
                    if 'പരിഭാഷ' in key:
                        translator_tag = value_cell.find('a')
                        if translator_tag:
                            details['translatedBy'] = {
                                'name': translator_tag.get_text(strip=True),
                                'url': translator_tag['href']
                            }
                        else:
                            details['translatedBy'] = {'name': value_cell.get_text(strip=True), 'url': None}
    except Exception as e:
        print(f"    Error parsing table: {e}")
        pass

    return details


def main():
    """Main scraping function."""
    print("Starting scraper...")
    all_results = []
    current_page_url = RELEASES_URL

    for page_num in range(1, MAX_PAGES + 1):
        print(f"\nScraping page {page_num}: {current_page_url}")
        
        list_soup = get_soup(current_page_url)
        if not list_soup:
            break

        entries = list_soup.select('.loop-entry .entry-title a')
        if not entries:
            # Try another selector for the archive pages
            entries = list_soup.select('h2.entry-title a')

        if not entries:
            print("No more entries found. Stopping.")
            break
            
        for link_tag in entries:
            if link_tag and link_tag.has_attr('href'):
                detail_url = link_tag['href']
                post_details = scrape_detail_page(detail_url)
                if post_details:
                    all_results.append(post_details)
                time.sleep(0.5) # Be polite

        # Find next page
        next_page_tag = list_soup.select_one('a.next.page-numbers')
        if next_page_tag and next_page_tag.has_attr('href'):
            current_page_url = next_page_tag['href']
        else:
            print("No next page found. Stopping pagination.")
            break

    print(f"\nScraped {len(all_results)} total entries.")

    # --- Mapper Logic ---
    print("Mapping results to db.json format...")
    final_db = {}
    for result in all_results:
        imdb_id = extract_imdb_id(result.get('imdbURL'))
        if imdb_id:
            if imdb_id not in final_db: # Avoid duplicates
                final_db[imdb_id] = result
        else:
            print(f"Could not extract IMDb ID for: {result.get('title')}")

    # Write to db.json
    with open('db.json', 'w', encoding='utf-8') as f:
        json.dump(final_db, f, ensure_ascii=False, indent=4)

    print(f"Successfully created db.json with {len(final_db)} unique entries.")


if __name__ == "__main__":
    main()
