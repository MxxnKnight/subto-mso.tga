import requests
from bs4 import BeautifulSoup
import json
import time
import re
import logging
import os
from urllib.parse import urljoin

# --- Setup ---
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BASE_URL = "https://malayalamsubtitles.org"
RELEASES_URL = f"{BASE_URL}/releases/"
# Maximum number of pages to scrape (default: 300)
# This represents how many pages to check starting from page 1 (newest)
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

    # This is the fix. Use the reliable split method to get the base name.
    series_name = re.split(r'\s+Season\s+\d|\s+സീസൺ\s+\d', title, 1, re.IGNORECASE)[0].strip()

    return {'is_series': True, 'season_number': season_number, 'series_name': series_name}

def scrape_detail_page(url):
    """Scrapes comprehensive details from a movie/series page."""
    soup = get_soup(url)
    if not soup: return None

    try:
        details = {'source_url': url, 'scraped_at': time.strftime('%Y-%m-%d %H:%M:%S')}
        
        # Log the URL being scraped
        logger.debug(f"Scraping details from: {url}")

        title_tag = soup.select_one('h1.entry-title, h1#release-title')
        details['title'] = clean_text(title_tag.get_text()) if title_tag else "Unknown Title"
        details['year'] = extract_year(details['title'])
        details.update(extract_season_info(details['title']))
        
        # Log the title and season info
        logger.debug(f"Title: {details['title']}, Year: {details['year']}, Is Series: {details.get('is_series', False)}")

        srt_tag = soup.select_one('a#download-button')
        details['srtURL'] = (srt_tag.get('data-downloadurl') or srt_tag.get('href')) if srt_tag else None
        
        # Try multiple selectors for poster image
        poster_tag = soup.select_one('figure#release-poster img, .entry-content figure img')
        if poster_tag and poster_tag.get('src'):
            details['posterMalayalam'] = urljoin(BASE_URL, poster_tag['src'])
            logger.debug(f"Found poster: {details['posterMalayalam']}")
        
        # Try multiple selectors for IMDB link
        imdb_tag = soup.select_one('a#imdb-button, a[href*="imdb.com"]')
        if imdb_tag:
            details['imdbURL'] = imdb_tag['href']
            logger.debug(f"Found IMDB URL: {details['imdbURL']}")

        # Try multiple selectors for description
        desc_tag = soup.select_one('div#synopsis, .entry-content p')
        if desc_tag:
            details['descriptionMalayalam'] = clean_text(desc_tag.get_text(separator='\n', strip=True))
            logger.debug(f"Found description: {details['descriptionMalayalam'][:50]}...")

        details_table = soup.select_one('#release-details-table tbody')
        table_data = {}
        if details_table:
            logger.debug(f"Found details table with {len(details_table.select('tr'))} rows")
            # Log the entire table HTML for debugging
            logger.debug(f"Table HTML: {details_table}")
            for row in details_table.select('tr'):
                cells = row.select('td')
                if len(cells) >= 2:
                    label = clean_text(cells[0].get_text()).lower().strip().replace(':', '')
                    table_data[label] = cells[1]
                    logger.debug(f"Found table row with label: '{label}'")
                    logger.debug(f"Cell content: {clean_text(cells[1].get_text())}")
        else:
            logger.debug("Could not find details table with selector '#release-details-table tbody'")

            # Try alternative selectors for the details table
            alt_tables = soup.select('.entry-content table')
            if alt_tables:
                logger.debug(f"Found {len(alt_tables)} alternative tables")
                for table_idx, alt_table in enumerate(alt_tables):
                    logger.debug(f"Checking alternative table {table_idx+1}")
                    # Log the alternative table HTML
                    logger.debug(f"Alternative table HTML: {alt_table}")
                    for row in alt_table.select('tr'):
                        cells = row.select('td')
                        if len(cells) >= 2:
                            label = clean_text(cells[0].get_text()).lower().strip().replace(':', '')
                            table_data[label] = cells[1]
                            logger.debug(f"Found table row with label: '{label}' in alternative table")
                            logger.debug(f"Cell content: {clean_text(cells[1].get_text())}")
            else:
                logger.debug("No alternative tables found")

        def get_field_data(labels):
            for label in labels:
                if label in table_data:
                    cell = table_data[label]
                    text = clean_text(cell.get_text())
                    link = cell.select_one('a')
                    url = urljoin(BASE_URL, link['href']) if link and link.has_attr('href') else None
                    # Debug logging to see what's being extracted
                    logger.debug(f"Found {label}: {text}, URL: {url}")
                    return {'name': text, 'url': url}
            # Debug which labels were not found
            logger.debug(f"Could not find any of these labels: {labels}")
            return None

        # Extract all fields with better error handling
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
                logger.debug(f"Found {field_name}: {field_value}")
            else:
                logger.debug(f"Could not find {field_name} with labels: {labels}")

        # Try multiple selectors for poster credit
        poster_credit_tag = soup.select_one('figure#release-poster figcaption a, figure figcaption a')
        if poster_credit_tag:
            details['poster_maker'] = {'name': clean_text(poster_credit_tag.get_text()), 'url': poster_credit_tag.get('href')}
            logger.debug(f"Found poster maker: {details['poster_maker']}")
        else:
            # Try to find poster credit in any figcaption
            figcaptions = soup.select('figcaption')
            for figcaption in figcaptions:
                if figcaption.get_text().strip():
                    link = figcaption.select_one('a')
                    if link:
                        details['poster_maker'] = {'name': clean_text(figcaption.get_text()), 'url': link.get('href')}
                        logger.debug(f"Found poster maker in alternative figcaption: {details['poster_maker']}")
                        break

            # If still not found, look for poster credit in paragraphs
            if 'poster_maker' not in details:
                for p in soup.select('.entry-content p'):
                    text = p.get_text().strip().lower()
                    if 'poster' in text and len(text) < 100:
                        details['poster_maker'] = {'name': clean_text(p.get_text()), 'url': None}
                        logger.debug(f"Found poster maker from paragraph: {details['poster_maker']}")
                        break

        # Try to extract IMDB rating from text if not found in table
        if 'imdb_rating' not in details:
            # Look for IMDb rating in the content
            content_text = soup.get_text() if soup else ''
            imdb_rating_patterns = [
                r'IMDb\s*:?\s*(\d+\.?\d*)/10',
                r'IMDb\s*:?\s*(\d+\.?\d*)',
                r'Rating\s*:?\s*(\d+\.?\d*)/10',
                r'ഐ.എം.ഡി.ബി\s*:?\s*(\d+\.?\d*)',
            ]

            for pattern in imdb_rating_patterns:
                rating_match = re.search(pattern, content_text, re.IGNORECASE)
                if rating_match:
                    details['imdb_rating'] = {'name': rating_match.group(1) + '/10', 'url': details.get('imdbURL')}
                    logger.debug(f"Extracted IMDb rating from text: {details['imdb_rating']}")
                    break

        # Try to extract MSOne release number from text if not found in table
        if 'msone_release' not in details:
            content_text = soup.get_text() if soup else ''
            msone_patterns = [
                r'MSOne\s*:?\s*(\w*\d+)',
                r'MS One\s*:?\s*(\w*\d+)',
                r'Release\s*:?\s*(\w*\d+)',
                r'Release No\s*:?\s*(\w*\d+)',
                r'റിലീസ് നം\s*:?\s*(\w*\d+)',
                r'MSOne\s*(?:Release)?\s*(?:Number|No|#)?\s*[:-]?\s*(\w*\d+)',
                r'MS\s*(?:Release)?\s*(?:Number|No|#)?\s*[:-]?\s*(\w*\d+)',
                r'Release\s*(?:Number|No|#)?\s*[:-]?\s*(\w*\d+)',
                r'#(\d+)',  # Hash followed by number
                r'No[:.\s]*(\d+)',  # No. followed by number
                r'Number[:.\s]*(\d+)',  # Number followed by number
                r'\b(MS\d+)\b',  # MS followed directly by digits
                r'\b(MSO\d+)\b',  # MSO followed directly by digits
            ]

            for pattern in msone_patterns:
                msone_match = re.search(pattern, content_text, re.IGNORECASE)
                if msone_match:
                    details['msone_release'] = {'name': msone_match.group(1), 'url': None}
                    logger.debug(f"Extracted MSOne release from text: {details['msone_release']}")
                    break

            # If still not found, look for any number after MSOne or Release
            if 'msone_release' not in details:
                # Find paragraphs that mention MSOne or Release
                for p in soup.select('p'):
                    text = p.get_text().strip()
                    if 'MSOne' in text or 'MS One' in text or 'Release' in text or 'റിലീസ്' in text or 'MS' in text or '#' in text:
                        # Look for any number in this paragraph
                        number_match = re.search(r'\b(\d+)\b', text)
                        if number_match:
                            details['msone_release'] = {'name': number_match.group(1), 'url': None}
                            logger.debug(f"Extracted MSOne release from paragraph with number: {details['msone_release']}")
                            break

            # Last resort: look for any standalone number in the page title or first paragraph
            if 'msone_release' not in details:
                title_text = details.get('title', '')
                number_match = re.search(r'#(\d+)', title_text)
                if number_match:
                    details['msone_release'] = {'name': number_match.group(1), 'url': None}
                    logger.debug(f"Extracted MSOne release from title: {details['msone_release']}")
                else:
                    # Try first paragraph
                    first_p = soup.select_one('p')
                    if first_p:
                        text = first_p.get_text().strip()
                        number_match = re.search(r'\b(\d+)\b', text)
                        if number_match:
                            details['msone_release'] = {'name': number_match.group(1), 'url': None}
                            logger.debug(f"Extracted MSOne release from first paragraph: {details['msone_release']}")

        # Try to extract certification if not found in table
        if 'certification' not in details:
            content_text = soup.get_text() if soup else ''
            cert_patterns = [
                r'Certification\s*:?\s*([A-Za-z0-9\-+]+)',
                r'Rated\s*:?\s*([A-Za-z0-9\-+]+)',
                r'Certificate\s*:?\s*([A-Za-z0-9\-+]+)',
                r'സെർട്ടിഫിക്കേഷൻ\s*:?\s*([A-Za-z0-9\-+]+)',
                r'\bRated\s+([A-Za-z0-9\-+]+)',
                r'\b([PG|G|R|NC|U|A]+[-]?\d*\+?)\b',  # Common rating patterns like PG-13, R, U/A, etc.
                r'\bRated\s*:\s*([A-Za-z0-9\-+]+)',  # Rated: followed by value
                r'\b(PG-13|PG|R|G|NC-17|U/A|U|A|12\+|16\+|18\+)\b'  # Common rating patterns
            ]

            for pattern in cert_patterns:
                cert_match = re.search(pattern, content_text, re.IGNORECASE)
                if cert_match:
                    details['certification'] = {'name': cert_match.group(1), 'url': None}
                    logger.debug(f"Extracted certification from text: {details['certification']}")
                    break

            # If still not found, look for common certification values
            if 'certification' not in details:
                common_certs = ['PG-13', 'PG', 'R', 'G', 'NC-17', 'U/A', 'U', 'A', '12+', '16+', '18+', 'TV-MA', 'TV-14', 'TV-PG', 'TV-G', 'TV-Y7', 'TV-Y']
                for cert in common_certs:
                    if cert in content_text:
                        details['certification'] = {'name': cert, 'url': None}
                        logger.debug(f"Found certification by keyword match: {details['certification']}")
                        break

            # Last resort: check if any paragraph contains certification-related keywords
            if 'certification' not in details:
                cert_keywords = ['rating', 'rated', 'certification', 'certificate', 'age', 'audience']
                for p in soup.select('p'):
                    text = p.get_text().strip().lower()
                    if any(keyword in text for keyword in cert_keywords):
                        # Look for common certification patterns in this paragraph
                        for cert in common_certs:
                            if cert.lower() in text.lower():
                                details['certification'] = {'name': cert, 'url': None}
                                logger.debug(f"Found certification in paragraph with keyword: {details['certification']}")
                                break
                        if 'certification' in details:
                            break

        # Try to extract genre if not found in table
        if 'genre' not in details:
            # Look for genre mentions in paragraphs
            content_text = soup.get_text() if soup else ''
            genre_patterns = [
                r'Genre\s*:?\s*([\w\s,]+)',
                r'ജോണർ\s*:?\s*([\w\s,]+)',
                r'Category\s*:?\s*([\w\s,]+)',
                r'Type\s*:?\s*([\w\s,]+)',
                r'വിഭാഗം\s*:?\s*([\w\s,]+)',
            ]

            for pattern in genre_patterns:
                genre_match = re.search(pattern, content_text, re.IGNORECASE)
                if genre_match:
                    details['genre'] = {'name': genre_match.group(1).strip(), 'url': None}
                    logger.debug(f"Extracted genre from text: {details['genre']}")
                    break

            # Also check for genre in tags
            if 'genre' not in details:
                genre_tags = soup.select('a[href*="/genre/"], a[href*="/category/"], a[href*="/tag/"]')
                if genre_tags:
                    genres = []
                    for tag in genre_tags[:3]:  # Limit to first 3 genre tags
                        genre_name = tag.get_text().strip()
                        genre_url = urljoin(BASE_URL, tag.get('href', ''))
                        genres.append({'name': genre_name, 'url': genre_url})

                    if genres:
                        details['genre'] = genres[0] if len(genres) == 1 else genres
                        logger.debug(f"Extracted genre from tags: {details['genre']}")

            # Look for common genre keywords in the description or content
            if 'genre' not in details:
                common_genres = ['Action', 'Adventure', 'Animation', 'Biography', 'Comedy', 'Crime', 'Documentary', 'Drama', 'Family', 'Fantasy', 'Film-Noir', 'History', 'Horror', 'Music', 'Musical', 'Mystery', 'Romance', 'Sci-Fi', 'Sport', 'Thriller', 'War', 'Western']
                description = details.get('descriptionMalayalam', '')
                found_genres = []

                for genre in common_genres:
                    if genre.lower() in content_text.lower() or genre.lower() in description.lower():
                        found_genres.append(genre)

                if found_genres:
                    details['genre'] = {'name': found_genres[0], 'url': None}
                    logger.debug(f"Found genre by keyword match in content: {details['genre']}")

            # Try to extract from first paragraph if still not found
            if 'genre' not in details:
                first_p = soup.select_one('p')
                if first_p:
                    text = first_p.get_text().strip().lower()
                    for genre in common_genres:
                        if genre.lower() in text:
                            details['genre'] = {'name': genre, 'url': None}
                            logger.debug(f"Found genre in first paragraph: {details['genre']}")
                            break

        # Try to extract director if not found in table
        if 'director' not in details:
            # Look for director mentions in paragraphs
            content_text = soup.get_text() if soup else ''
            director_patterns = [
                r'Director\s*:?\s*([\w\s\.]+)',
                r'Directed by\s*:?\s*([\w\s\.]+)',
                r'സംവിധായകൻ\s*:?\s*([\w\s\.]+)',
                r'സംവിധാനം\s*:?\s*([\w\s\.]+)',
                r'Direction\s*:?\s*([\w\s\.]+)',
                r'Director\s*-\s*([\w\s\.]+)',
                r'Directed\s*-\s*([\w\s\.]+)',
                r'By\s*:?\s*([\w\s\.]+)',
            ]

            for pattern in director_patterns:
                director_match = re.search(pattern, content_text, re.IGNORECASE)
                if director_match:
                    details['director'] = {'name': director_match.group(1).strip(), 'url': None}
                    logger.debug(f"Extracted director from text: {details['director']}")
                    break

            # Check for director in tags
            if 'director' not in details:
                director_tags = soup.select('a[href*="/director/"], a[href*="/directed-by/"], a[href*="/tag/"]')
                if director_tags:
                    for tag in director_tags:
                        tag_text = tag.get_text().strip()
                        tag_url = tag.get('href', '')
                        # Skip tags that are likely not directors
                        if any(x in tag_text.lower() for x in ['genre', 'category', 'language', 'year']):
                            continue
                        details['director'] = {'name': tag_text, 'url': urljoin(BASE_URL, tag_url)}
                        logger.debug(f"Extracted director from tag: {details['director']}")
                        break

            # Try to find director in title or description
            if 'director' not in details:
                title = details.get('title', '')
                description = details.get('descriptionMalayalam', '')

                # Look for "by [Name]" pattern in title or description
                by_pattern = r'by\s+([A-Z][a-z]+\s+[A-Z][a-z]+)'
                by_match = re.search(by_pattern, title + ' ' + description, re.IGNORECASE)
                if by_match:
                    details['director'] = {'name': by_match.group(1).strip(), 'url': None}
                    logger.debug(f"Extracted director from title/description 'by' pattern: {details['director']}")

            # Last resort: check if any paragraph contains director-like information
            if 'director' not in details:
                paragraphs = soup.select('p')
                for p in paragraphs[:5]:  # Check first 5 paragraphs only
                    p_text = p.get_text().strip()
                    if any(term in p_text.lower() for term in ['direct', 'സംവിധാനം', 'സംവിധായകൻ', 'direction']):
                        # Try to find a name pattern (capitalized words)
                        name_pattern = r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})'
                        name_match = re.search(name_pattern, p_text)
                        if name_match:
                            details['director'] = {'name': name_match.group(1).strip(), 'url': None}
                            logger.debug(f"Extracted director from paragraph with name pattern: {details['director']}")
                            break

            # This section is now handled above with more comprehensive tag selection
        
        return details
    except Exception as e:
        logger.exception(f"Error scraping detail page {url}")
        return None

def main():
    """Main scraper function."""
    logger.info("Starting Malayalam subtitle scraper...")

    try:
        with open('db.json', 'r', encoding='utf-8') as f: final_db = json.load(f)
        logger.info(f"Loaded {len(final_db)} existing entries from db.json")
    except (FileNotFoundError, json.JSONDecodeError):
        final_db = {}
        logger.info("No existing db.json found. Starting fresh.")

    # Start from the latest page (page 1) and work backwards
    # Page 1 is the latest page, and higher numbers are older pages
    START_PAGE = 1
    MAX_PAGE_TO_CHECK = min(MAX_PAGES, 300)

    # First page is the base URL without page number
    current_page_url = RELEASES_URL
    newly_added_count = 0
    page_num = START_PAGE

    while page_num <= MAX_PAGE_TO_CHECK:
        logger.info(f"Scraping page {page_num}/{MAX_PAGE_TO_CHECK} (newest to oldest): {current_page_url}")
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
            if not imdb_id: continue

            unique_id = f"{imdb_id}-S{post_details['season_number']}" if post_details.get('is_series') else imdb_id

            if unique_id not in final_db:
                final_db[unique_id] = post_details
                newly_added_count += 1
                new_on_this_page += 1
                logger.info(f"NEW: {post_details['title']} ({unique_id})")
            else:
                # Update existing entries if they have missing fields
                updated = False
                for field in ['director', 'genre', 'msone_release', 'certification']:
                    if field not in final_db[unique_id] or final_db[unique_id][field] is None:
                        if field in post_details and post_details[field] is not None:
                            final_db[unique_id][field] = post_details[field]
                            updated = True
                            logger.info(f"UPDATED {field} for {final_db[unique_id]['title']} ({unique_id})")

                if updated:
                    newly_added_count += 1
                    new_on_this_page += 1

            time.sleep(0.1)

        # When scraping from newest to oldest, we should continue even if we don't find new entries
        # on the latest pages, as older pages might still have entries we haven't scraped yet
        # Only stop early if we're on a very old page (page > START_PAGE + 5) and find no new entries
        if new_on_this_page == 0 and page_num > (START_PAGE + 5):
             logger.info("Stopping early: No new entries found on older pages.")
             break

        # Look for the next page link instead of previous
        next_page_tag = list_soup.select_one('a.next.page-numbers')
        if next_page_tag and next_page_tag.get('href'):
            current_page_url = urljoin(BASE_URL, next_page_tag['href'])
            page_num += 1
        else:
            logger.info("No next page found or reached the last page.")
            break
        time.sleep(0.2)

    logger.info(f"Scraping finished. Added {newly_added_count} new entries. Total: {len(final_db)}")
    logger.info("Post-processing series information...")

    # --- New Series Post-Processing Logic ---

    # 1. Group all seasons by their base series name
    series_grouping = {}
    for unique_id, entry in final_db.items():
        if entry.get('is_series') and entry.get('series_name'):
            base_name = entry['series_name'] # This now comes correctly from extract_season_info
            if base_name not in series_grouping:
                series_grouping[base_name] = []
            series_grouping[base_name].append(unique_id)

    # 2. Update each entry with the correct total_seasons count
    for base_name, season_ids in series_grouping.items():
        total_seasons = len(season_ids)
        for unique_id in season_ids:
            if unique_id in final_db:
                final_db[unique_id]['total_seasons'] = total_seasons

    # 3. Create the clean series_db for series_db.json
    series_db = {}
    for base_name, season_ids in series_grouping.items():
        series_db[base_name] = {}
        for unique_id in season_ids:
            if unique_id in final_db:
                season_num = final_db[unique_id].get('season_number', 1)
                series_db[base_name][str(season_num)] = unique_id

    logger.info(f"Post-processing complete. Found {len(series_db)} unique series.")

    try:
        # Safely backup and save files
        for filename, data in [('db.json', final_db), ('series_db.json', series_db)]:
            backup_file = f"{filename}.backup"
            # Remove old backup if it exists
            if os.path.exists(backup_file):
                try:
                    os.remove(backup_file)
                    logger.debug(f"Removed old backup: {backup_file}")
                except Exception as e:
                    logger.warning(f"Could not remove old backup {backup_file}: {e}")

            # Create backup of current file
            if os.path.exists(filename):
                try:
                    os.rename(filename, backup_file)
                    logger.debug(f"Created backup: {backup_file}")
                except Exception as e:
                    logger.warning(f"Could not create backup for {filename}: {e}")

            # Save new data
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                logger.debug(f"Saved {filename}")
        
        logger.info("Successfully saved db.json and series_db.json")
    except Exception as e:
        logger.error(f"Error writing database: {e}")

if __name__ == "__main__":
    main()
