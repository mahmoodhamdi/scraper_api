import sqlite3
import re
import os
import logging
import shutil
import requests
import json
from bs4 import BeautifulSoup
from datetime import datetime, time
from flask import Flask, jsonify, request
from flasgger import Swagger
from urllib.parse import urlparse
from werkzeug.utils import secure_filename
from flask_cors import CORS
from scraper.liquipedia_scraper import fetch_tournaments, get_matches_by_status
from collections import defaultdict
from datetime import datetime as dt

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Configure upload folder
UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def init_db():
    """Initialize the SQLite database with news, teams, events, and ewc_info tables"""
    conn = sqlite3.connect('news.db')
    cursor = conn.cursor()
    
    # Create uploads directory if it doesn't exist
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)
    
    # Check and create news table
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='news'")
    table_exists = cursor.fetchone()
    
    if table_exists:
        cursor.execute("PRAGMA table_info(news)")
        columns = {col[1]: col for col in cursor.fetchall()}
        
        if 'thumbnail_url' not in columns:
            cursor.execute('ALTER TABLE news ADD COLUMN thumbnail_url TEXT')
        
        if 'id' not in columns or 'AUTOINCREMENT' not in cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='news'").fetchone()[0]:
            cursor.execute('''
                CREATE TABLE news_temp (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    description TEXT,
                    writer TEXT NOT NULL,
                    thumbnail_url TEXT,
                    news_link TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                INSERT INTO news_temp (id, title, description, writer, thumbnail_url, news_link, created_at, updated_at)
                SELECT id, title, description, writer, thumbnail_url, news_link, created_at, updated_at
                FROM news
            ''')
            cursor.execute('DROP TABLE news')
            cursor.execute('ALTER TABLE news_temp RENAME TO news')
    else:
        cursor.execute('''
            CREATE TABLE news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                writer TEXT NOT NULL,
                thumbnail_url TEXT,
                news_link TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
    # Create prize_distribution table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS prize_distribution (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            place TEXT NOT NULL,
            place_logo TEXT,
            prize TEXT NOT NULL,
            participants TEXT,
            logo_team TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Create teams table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_name TEXT NOT NULL,
            logo_url TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Create events table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            link TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Create games table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_name TEXT NOT NULL,
            logo_url TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Create ewc_info table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ewc_info (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            header TEXT,
            series TEXT,
            organizers TEXT,
            location TEXT,
            prize_pool TEXT,
            start_date TEXT,
            end_date TEXT,
            liquipedia_tier TEXT,
            logo_light TEXT,
            logo_dark TEXT,
            location_logo TEXT,
            social_links TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()
    conn.close()

def reset_db_sequence():
    """Reset the SQLite sequence for all tables"""
    try:
        conn = sqlite3.connect('news.db')
        cursor = conn.cursor()
        cursor.execute("DELETE FROM sqlite_sequence WHERE name IN ('news', 'teams', 'events', 'ewc_info')")
        conn.commit()
        logger.debug("Reset SQLite sequence for tables")
    except sqlite3.Error as e:
        logger.error(f"Failed to reset SQLite sequence: {str(e)}")
        raise
    finally:
        conn.close()

def is_valid_url(url):
    """Validate URL format"""
    if not url:
        return True
    regex = re.compile(
        r'^https?://'
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'
        r'localhost|'
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'
        r'(?::\d+)?'
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)
    return bool(regex.match(url))

def is_valid_thumbnail(url):
    """Validate thumbnail URL (image or link)"""
    if not url:
        return True
    image_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.webp')
    parsed = urlparse(url)
    return is_valid_url(url) and (
        parsed.path.lower().endswith(image_extensions) or 
        parsed.scheme in ('http', 'https')
    )

def is_valid_date(date_str):
    """Validate date format (YYYY-MM-DD)"""
    try:
        dt.strptime(date_str, '%Y-%m-%d')
        return True
    except ValueError:
        return False

def parse_match_datetime(match_time):
    """Parse match time string to datetime for sorting by date and time"""
    try:
        if ' - ' in match_time:
            date_str, time_str = match_time.split(' - ')
            date_str = ' '.join(date_str.split()[:3])  # Take first three words (e.g., "July 8, 2025")
            time_str = time_str.split()[0]  # Take time part (e.g., "12:00")
            parsed_datetime = dt.strptime(f"{date_str} {time_str}", '%B %d, %Y %H:%M')
            return parsed_datetime
        else:
            # Handle cases like "August 2, 2025" without time
            date_str = ' '.join(match_time.split()[:3])
            parsed_datetime = dt.strptime(date_str, '%B %d, %Y')
            return parsed_datetime
    except (IndexError, ValueError):
        return dt.max

def get_ewc_information(live=False):
    """Fetch Esports World Cup 2025 information from Liquipedia or database"""
    if not live:
        try:
            conn = sqlite3.connect('news.db')
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM ewc_info ORDER BY updated_at DESC LIMIT 1')
            row = cursor.fetchone()
            conn.close()
            
            if row:
                info_data = {
                    'header': row[1],
                    'series': row[2],
                    'organizers': row[3],
                    'location': row[4],
                    'prize_pool': row[5],
                    'start_date': row[6],
                    'end_date': row[7],
                    'liquipedia_tier': row[8],
                    'logo_light': row[9],
                    'logo_dark': row[10],
                    'location_logo': row[11],
                    'social_links': json.loads(row[12]) if row[12] else [],
                    'updated_at': row[13]
                }
                logger.debug("Retrieved EWC info from database")
                return info_data
        except sqlite3.Error as e:
            logger.error(f"Database error while fetching EWC info: {str(e)}")
    
    # Fetch from Liquipedia if live=True or no data in database
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    }
    BASE_URL = "https://liquipedia.net"
    URL = "https://liquipedia.net/esports/Esports_World_Cup/2025"
    
    try:
        response = requests.get(URL, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        info_data = soup.select_one('div.fo-nttax-infobox')
        
        data = {}
        
        if not info_data:
            logger.error("No info data found on Liquipedia")
            return {}
            
        header = info_data.select_one('div.infobox-header.wiki-backgroundcolor-light')
        if header:
            data['header'] = header.text.strip()
        
        for information in info_data.select('div.infobox-cell-2.infobox-description'):
            key = information.text.strip().rstrip(':')
            value = information.find_next_sibling()
            if value:
                data[key.lower().replace(' ', '_')] = value.text.strip()
        
        logo_light = info_data.select_one('.infobox-image.lightmode img')
        if logo_light:
            data['logo_light'] = BASE_URL + logo_light['src']
            
        logo_dark = info_data.select_one('.infobox-image.darkmode img')
        if logo_dark:
            data['logo_dark'] = BASE_URL + logo_dark['src']
        
        location_logo = info_data.select_one('div.infobox-cell-2.infobox-description:contains("Location") + div span.flag img')
        if location_logo:
            data['location_logo'] = BASE_URL + location_logo['src']
        
        social_links = []
        links = info_data.select('div.infobox-center.infobox-icons a.external.text')
        for link in links:
            href = link.get('href')
            icon_class = link.select_one('i')
            if href and icon_class:
                icon_type = icon_class['class'][-1].replace('lp-', '')
                social_links.append({
                    "platform": icon_type,
                    "link": href
                })
        data['social_links'] = social_links
        
        # Store in database
        try:
            conn = sqlite3.connect('news.db')
            cursor = conn.cursor()
            cursor.execute('DELETE FROM ewc_info')  # Clear existing info
            cursor.execute('''
                INSERT INTO ewc_info (
                    header, series, organizers, location, prize_pool, 
                    start_date, end_date, liquipedia_tier, logo_light, 
                    logo_dark, location_logo, social_links
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                data.get('header'),
                data.get('series'),
                data.get('organizers'),
                data.get('location'),
                data.get('prize_pool'),
                data.get('start_date'),
                data.get('end_date'),
                data.get('liquipedia_tier'),
                data.get('logo_light'),
                data.get('logo_dark'),
                data.get('location_logo'),
                json.dumps(data.get('social_links'))
            ))
            conn.commit()
            logger.debug("Stored EWC info in database")
        except sqlite3.Error as e:
            logger.error(f"Database error while storing EWC info: {str(e)}")
        finally:
            conn.close()
        
        return data
    
    except requests.RequestException as e:
        logger.error(f"Error fetching EWC info from Liquipedia: {str(e)}")
        return {}
    except Exception as e:
        logger.error(f"Error processing EWC info: {str(e)}")
        return {}

def get_teams_ewc(live=False):
    """Fetch Esports World Cup 2025 teams from Liquipedia or database"""
    if not live:
        try:
            conn = sqlite3.connect('news.db')
            cursor = conn.cursor()
            cursor.execute('SELECT team_name, logo_url FROM teams')
            teams_data = [{'team_name': row[0], 'logo_url': row[1]} for row in cursor.fetchall()]
            conn.close()
            
            if teams_data:
                logger.debug("Retrieved teams data from database")
                return teams_data
        except sqlite3.Error as e:
            logger.error(f"Database error while fetching teams: {str(e)}")
        
    # Fetch from Liquipedia if live=True or no data in database
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    }
    BASE_URL = "https://liquipedia.net"
    url = 'https://liquipedia.net/esports/Esports_World_Cup/2025'
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        teams_data = []
        all_tables = soup.select('div.table-responsive table.wikitable.sortable')

        target_table = None
        for table in all_tables:
            headers_row = table.select_one('tr')
            headers_ths = headers_row.select('th') if headers_row else []
            if headers_ths and 'Team Name' in headers_ths[0].text:
                target_table = table
                break

        if not target_table:
            logger.error("Could not find the teams table")
            return []

        rows = target_table.select('tr')[1:]
        for row in rows:
            cols = row.select('td')
            if len(cols) >= 1:
                team_name = cols[0].text.strip()
                logo_tag = cols[0].select_one('img')
                logo_url = BASE_URL + logo_tag['src'] if logo_tag else None

                teams_data.append({
                    'team_name': team_name,
                    'logo_url': logo_url
                })

        # Store in database
        try:
            conn = sqlite3.connect('news.db')
            cursor = conn.cursor()
            cursor.execute('DELETE FROM teams')  # Clear existing teams
            for team in teams_data:
                cursor.execute('''
                    INSERT INTO teams (team_name, logo_url)
                    VALUES (?, ?)
                ''', (team['team_name'], team['logo_url']))
            conn.commit()
            logger.debug("Stored teams data in database")
        except sqlite3.Error as e:
            logger.error(f"Database error while storing teams: {str(e)}")
        finally:
            conn.close()

        return teams_data
    
    except requests.RequestException as e:
        logger.error(f"Error fetching teams data: {str(e)}")
        return []
    except Exception as e:
        logger.error(f"Error processing teams data: {str(e)}")
        return []

def get_events_ewc(live=False):
    """Fetch Esports World Cup 2025 events from Liquipedia or database"""
    if not live:
        try:
            conn = sqlite3.connect('news.db')
            cursor = conn.cursor()
            cursor.execute('SELECT name, link FROM events')
            events_data = [{'name': row[0], 'link': row[1]} for row in cursor.fetchall()]
            conn.close()
            
            if events_data:
                logger.debug("Retrieved events data from database")
                return events_data
        except sqlite3.Error as e:
            logger.error(f"Database error while fetching events: {str(e)}")
        
    # Fetch from Liquipedia if live=True or no data in database
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    }
    BASE_URL = "https://liquipedia.net"
    url = 'https://liquipedia.net/esports/Esports_World_Cup/2025'
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        events_data = []
        events_headers = soup.select_one('div.esports-team-game-list')

        if not events_headers:
            logger.error("Could not find the events section")
            return []
        
        for span in events_headers.select('span > a'):
            name = span.text.strip()
            link = span['href'].strip()
            full_link = link if link.startswith('http') else BASE_URL + link

            events_data.append({
                "name": name,
                "link": full_link
            })
        
        # Store in database
        try:
            conn = sqlite3.connect('news.db')
            cursor = conn.cursor()
            cursor.execute('DELETE FROM events')  # Clear existing events
            for event in events_data:
                cursor.execute('''
                    INSERT INTO events (name, link)
                    VALUES (?, ?)
                ''', (event['name'], event['link']))
            conn.commit()
            logger.debug("Stored events data in database")
        except sqlite3.Error as e:
            logger.error(f"Database error while storing events: {str(e)}")
        finally:
            conn.close()

        return events_data
    
    except requests.RequestException as e:
        logger.error(f"Error fetching events data: {str(e)}")
        return []
    except Exception as e:
        logger.error(f"Error processing events data: {str(e)}")
        return []
def get_prize_distribution(live=False):
    """Fetch Esports World Cup 2025 prize distribution from Liquipedia or database"""
    if not live:
        try:
            conn = sqlite3.connect('news.db')
            cursor = conn.cursor()
            cursor.execute('SELECT place, place_logo, prize, participants, logo_team FROM prize_distribution')
            prize_data = [
                {
                    'place': row[0],
                    'place_logo': row[1],
                    'prize': row[2],
                    'participants': row[3],
                    'logo_team': row[4]
                } for row in cursor.fetchall()
            ]
            conn.close()
            
            if prize_data:
                logger.debug("Retrieved prize distribution data from database")
                return prize_data
        except sqlite3.Error as e:
            logger.error(f"Database error while fetching prize distribution: {str(e)}")
    
    # Fetch from Liquipedia if live=True or no data in database
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    }
    BASE_URL = "https://liquipedia.net"
    URL = "https://liquipedia.net/esports/Esports_World_Cup/2025"
    
    try:
        response = requests.get(URL, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        prize_table = soup.select_one('div.prizepool-section-tables .csstable-widget')
        prize_data = []

        if not prize_table:
            logger.error("No prize distribution table found on Liquipedia")
            return []

        rows = prize_table.select('div.csstable-widget-row')[1:]  
        for row in rows:
            cell = row.select('div.csstable-widget-cell')
            if len(cell) >= 3:
                place_cell = cell[0]
                place = place_cell.get_text(strip=True)

                place_img = place_cell.select_one('img')
                place_logo = BASE_URL + place_img['src'] if place_img else None

                prize = cell[1].get_text(strip=True)

                participant_cell = cell[2]
                participants = participant_cell.get_text(strip=True)

                logo_tag = participant_cell.select_one('.team-template-lightmode img')
                logo_team = BASE_URL + logo_tag['src'] if logo_tag else None

                prize_data.append({
                    'place': place,
                    'place_logo': place_logo,
                    'prize': prize,
                    'participants': participants,
                    'logo_team': logo_team
                })

        # Store in database
        try:
            conn = sqlite3.connect('news.db')
            cursor = conn.cursor()
            cursor.execute('DELETE FROM prize_distribution')  # Clear existing prize data
            for prize in prize_data:
                cursor.execute('''
                    INSERT INTO prize_distribution (place, place_logo, prize, participants, logo_team)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    prize['place'],
                    prize['place_logo'],
                    prize['prize'],
                    prize['participants'],
                    prize['logo_team']
                ))
            conn.commit()
            logger.debug("Stored prize distribution data in database")
        except sqlite3.Error as e:
            logger.error(f"Database error while storing prize distribution: {str(e)}")
        finally:
            conn.close()

        return prize_data
    
    except requests.RequestException as e:
        logger.error(f"Error fetching prize distribution from Liquipedia: {str(e)}")
        return []
    except Exception as e:
        logger.error(f"Error processing prize distribution: {str(e)}")
        return []

def get_group_stage_url(main_link, soup=None):
    """Helper function to get group stage URL from main link"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    }
    BASE_URL = "https://liquipedia.net"
    
    if not soup:
        try:
            response = requests.get(main_link, headers=headers)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
        except requests.RequestException as e:
            logger.error(f"Error fetching main link {main_link}: {str(e)}")
            return main_link.rstrip('/') + '/Group_Stage'

    detailed_link = soup.find('a', string=lambda x: x and 'click HERE' in x)
    if detailed_link:
        full_url = BASE_URL + detailed_link['href']
        if main_link.split('/')[2] in full_url:
            return full_url

    return main_link.rstrip('/') + '/Group_Stage'

def scrape_group_stage(game_name, link):
    """Scrape group stage matches for a given game"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    }
    BASE_URL = "https://liquipedia.net"
    
    url = get_group_stage_url(link)
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        group_boxes = soup.select('div.template-box')

        if not group_boxes:
            return {"message": "Matches have not been added yet."}

        data = {}
        for group in group_boxes:
            group_name_tag = group.select_one('.brkts-matchlist-title')
            group_name = group_name_tag.text.strip() if group_name_tag else 'Unknown Group'
            matches = []

            for match in group.select('.brkts-matchlist-match'):
                teams = match.select('.brkts-matchlist-opponent')
                if len(teams) == 2:
                    team1 = teams[0].get('aria-label', 'N/A')
                    logo1 = BASE_URL + teams[0].select_one('img')['src'] if teams[0].select_one('img') else 'N/A'
                    team2 = teams[1].get('aria-label', 'N/A')
                    logo2 = BASE_URL + teams[1].select_one('img')['src'] if teams[1].select_one('img') else 'N/A'
                else:
                    team1 = team2 = logo1 = logo2 = 'N/A'

                match_time = match.select_one('span.timer-object')
                time_text = match_time.text.strip() if match_time else 'N/A'

                score_tag = match.select_one('.brkts-matchlist-score')
                score = score_tag.text.strip() if score_tag else 'N/A'

                matches.append({
                    "Team1": {"Name": team1, "Logo": logo1},
                    "Team2": {"Name": team2, "Logo": logo2},
                    "MatchTime": time_text,
                    "Score": score
                })

            data[group_name] = matches

        return data
    except requests.RequestException as e:
        logger.error(f"Error fetching group stage data for {game_name}: {str(e)}")
        return {"message": f"Failed to fetch data: {str(e)}"}
    except Exception as e:
        logger.error(f"Error processing group stage data for {game_name}: {str(e)}")
        return {"message": f"Server error: {str(e)}"}


def get_ewc_games(live=False):
    """Fetch Esports World Cup 2025 games from Liquipedia or database"""
    if not live:
        try:
            conn = sqlite3.connect('news.db')
            cursor = conn.cursor()
            cursor.execute('SELECT game_name, logo_url FROM games')
            games_data = [{'game_name': row[0], 'logo_url': row[1]} for row in cursor.fetchall()]
            conn.close()
            
            if games_data:
                logger.debug("Retrieved games data from database")
                return games_data
        except sqlite3.Error as e:
            logger.error(f"Database error while fetching games: {str(e)}")
        
    # Fetch from Liquipedia if live=True or no data in database
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    }
    BASE_URL = "https://liquipedia.net"
    url = 'https://liquipedia.net/esports/Esports_World_Cup/2025'
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        games_data = []
        target_table = None
        for th in soup.select('th[colspan="8"]'):
            if 'List of Tournaments' in th.text:
                target_table = th.find_parent('table')
                break

        if not target_table:
            logger.error("Could not find the games table")
            return []

        rows = target_table.select('tr')[1:]
        for row in rows:
            cols = row.select('td')
            if len(cols) >= 4:
                game_name = cols[0].text.strip()
                if not game_name:
                    continue
                logo_game = cols[0].select_one('img')
                logo_url = BASE_URL + logo_game['src'] if logo_game else None

                games_data.append({
                    'game_name': game_name,
                    'logo_url': logo_url
                })

        # Store in database
        try:
            conn = sqlite3.connect('news.db')
            cursor = conn.cursor()
            cursor.execute('DELETE FROM games')  # Clear existing games
            for game in games_data:
                cursor.execute('''
                    INSERT INTO games (game_name, logo_url)
                    VALUES (?, ?)
                ''', (game['game_name'], game['logo_url']))
            conn.commit()
            logger.debug("Stored games data in database")
        except sqlite3.Error as e:
            logger.error(f"Database error while storing games: {str(e)}")
        finally:
            conn.close()

        return games_data
    
    except requests.RequestException as e:
        logger.error(f"Error fetching games data: {str(e)}")
        return []
    except Exception as e:
        logger.error(f"Error processing games data: {str(e)}")
        return []

def setup_routes(app):
    """Setup API routes"""
    
    @app.route('/api/news', methods=['POST'])
    def create_news():
        """
        Create a new news item
        ---
        consumes:
          - multipart/form-data
          - application/json
        parameters:
          - name: title
            in: formData
            type: string
            required: true
          - name: writer
            in: formData
            type: string
            required: true
          - name: description
            in: formData
            type: string
          - name: thumbnail_url
            in: formData
            type: string
          - name: thumbnail_file
            in: formData
            type: file
          - name: news_link
            in: formData
            type: string
        responses:
          201:
            description: News item created successfully
          400:
            description: Invalid input data
        """
        title = request.form.get('title', '').strip()
        writer = request.form.get('writer', '').strip()
        description = request.form.get('description', '').strip()
        thumbnail_url = request.form.get('thumbnail_url', '').strip()
        news_link = request.form.get('news_link', '').strip()
        
        if not title or not writer:
            return jsonify({"error": "Title and writer are required"}), 400
            
        final_thumbnail_url = ''
        if 'thumbnail_file' in request.files and request.files['thumbnail_file']:
            file = request.files['thumbnail_file']
            logger.debug(f"Received file: {file.filename if file else 'None'}")
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                timestamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')
                unique_filename = f"{timestamp}_{filename}"
                file_path = os.path.join(UPLOAD_FOLDER, unique_filename)
                logger.debug(f"Saving file to: {file_path}")
                file.save(file_path)
                final_thumbnail_url = f"/{UPLOAD_FOLDER}/{unique_filename}"
                logger.debug(f"Set final_thumbnail_url to: {final_thumbnail_url}")
            else:
                return jsonify({"error": "Invalid file type. Allowed: png, jpg, jpeg, gif, webp"}), 400
        elif thumbnail_url:
            logger.debug(f"Received thumbnail_url: {thumbnail_url}")
            if not is_valid_thumbnail(thumbnail_url):
                return jsonify({"error": "Invalid thumbnail URL"}), 400
            final_thumbnail_url = thumbnail_url
            logger.debug(f"Set final_thumbnail_url to: {final_thumbnail_url}")
            
        if news_link and not is_valid_url(news_link):
            return jsonify({"error": "Invalid news link URL"}), 400
            
        title = title[:255]
        writer = writer[:100]
        description = description[:2000]
        
        try:
            conn = sqlite3.connect('news.db')
            cursor = conn.cursor()
            
            logger.debug(f"Inserting news item with thumbnail_url: {final_thumbnail_url}")
            cursor.execute('''
                INSERT INTO news (title, description, writer, thumbnail_url, news_link)
                VALUES (?, ?, ?, ?, ?)
            ''', (title, description, writer, final_thumbnail_url, news_link))
            
            conn.commit()
            news_id = cursor.lastrowid
            
            cursor.execute('SELECT thumbnail_url FROM news WHERE id = ?', (news_id,))
            saved_thumbnail_url = cursor.fetchone()[0]
            logger.debug(f"Saved thumbnail_url in DB: {saved_thumbnail_url}")
            
            return jsonify({
                "message": "News created successfully",
                "id": news_id
            }), 201
            
        except sqlite3.Error as e:
            logger.error(f"Database error: {str(e)}")
            return jsonify({"error": f"Database error: {str(e)}"}), 500
        finally:
            conn.close()

    @app.route('/api/news', methods=['GET'])
    def get_news():
        """
        Get news items with pagination and filtering
        ---
        parameters:
          - name: page
            in: query
            type: integer
            default: 1
          - name: per_page
            in: query
            type: integer
            default: 10
          - name: writer
            in: query
            type: string
          - name: search
            in: query
            type: string
          - name: sort
            in: query
            type: string
            enum: [created_at, title]
            default: created_at
        responses:
          200:
            description: List of news items
        """
        page = max(1, request.args.get('page', 1, type=int))
        per_page = max(1, min(100, request.args.get('per_page', 10, type=int)))
        writer = request.args.get('writer', '').strip()
        search = request.args.get('search', '').strip()
        sort = request.args.get('sort', 'created_at').strip()
        
        if sort not in ('created_at', 'title'):
            sort = 'created_at'
            
        try:
            conn = sqlite3.connect('news.db')
            cursor = conn.cursor()
            
            query = 'SELECT id, title, description, writer, thumbnail_url, news_link, created_at, updated_at FROM news WHERE 1=1'
            params = []
            
            if writer:
                query += ' AND writer LIKE ?'
                params.append(f'%{writer}%')
                
            if search:
                query += ' AND (title LIKE ? OR description LIKE ?)'
                params.extend([f'%{search}%', f'%{search}%'])
                
            query += f' ORDER BY {sort} DESC'
            query += ' LIMIT ? OFFSET ?'
            params.extend([per_page, (page - 1) * per_page])
            
            cursor.execute(query, params)
            news_items = [
                {
                    'id': row[0],
                    'title': row[1],
                    'description': row[2],
                    'writer': row[3],
                    'thumbnail_url': row[4] or '',
                    'news_link': row[5],
                    'created_at': row[6],
                    'updated_at': row[7]
                } for row in cursor.fetchall()
            ]
            
            logger.debug(f"Retrieved news items: {[item['thumbnail_url'] for item in news_items]}")
            
            count_query = 'SELECT COUNT(*) FROM news WHERE 1=1'
            count_params = []
            
            if writer:
                count_query += ' AND writer LIKE ?'
                count_params.append(f'%{writer}%')
                
            if search:
                count_query += ' AND (title LIKE ? OR description LIKE ?)'
                count_params.extend([f'%{search}%', f'%{search}%'])
                
            cursor.execute(count_query, count_params)
            total = cursor.fetchone()[0]
            
            return jsonify({
                'news': news_items,
                'pagination': {
                    'page': page,
                    'per_page': per_page,
                    'total': total,
                    'pages': (total + per_page - 1) // per_page
                }
            })
            
        except sqlite3.Error as e:
            logger.error(f"Database error: {str(e)}")
            return jsonify({"error": f"Database error: {str(e)}"}), 500
        finally:
            conn.close()

    @app.route('/api/news/<int:id>', methods=['PUT'])
    def update_news(id):
        """
        Update an existing news item
        ---
        consumes:
          - multipart/form-data
          - application/json
        parameters:
          - name: id
            in: path
            type: integer
            required: true
          - name: title
            in: formData
            type: string
          - name: description
            in: formData
            type: string
          - name: writer
            in: formData
            type: string
          - name: thumbnail_url
            in: formData
            type: string
          - name: thumbnail_file
            in: formData
            type: file
          - name: news_link
            in: formData
            type: string
        responses:
          200:
            description: News item updated successfully
          404:
            description: News item not found
        """
        try:
            conn = sqlite3.connect('news.db')
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM news WHERE id = ?', (id,))
            if not cursor.fetchone():
                conn.close()
                return jsonify({"error": "News item not found"}), 404
        except sqlite3.Error as e:
            conn.close()
            return jsonify({"error": f"Database error: {str(e)}"}), 500
            
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        writer = request.form.get('writer', '').strip()
        thumbnail_url = request.form.get('thumbnail_url', '').strip()
        news_link = request.form.get('news_link', '').strip()
        
        final_thumbnail_url = None
        if 'thumbnail_file' in request.files and request.files['thumbnail_file']:
            file = request.files['thumbnail_file']
            logger.debug(f"Received file for update: {file.filename if file else 'None'}")
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                timestamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')
                unique_filename = f"{timestamp}_{filename}"
                file_path = os.path.join(UPLOAD_FOLDER, unique_filename)
                logger.debug(f"Saving update file to: {file_path}")
                file.save(file_path)
                final_thumbnail_url = f"/{UPLOAD_FOLDER}/{unique_filename}"
                logger.debug(f"Set final_thumbnail_url for update to: {final_thumbnail_url}")
            else:
                return jsonify({"error": "Invalid file type. Allowed: png, jpg, jpeg, gif, webp"}), 400
        elif thumbnail_url:
            logger.debug(f"Received thumbnail_url for update: {thumbnail_url}")
            if not is_valid_thumbnail(thumbnail_url):
                return jsonify({"error": "Invalid thumbnail URL"}), 400
            final_thumbnail_url = thumbnail_url
            logger.debug(f"Set final_thumbnail_url for update to: {final_thumbnail_url}")
            
        if news_link and not is_valid_url(news_link):
            return jsonify({"error": "Invalid news link URL"}), 400
            
        update_data = {}
        if title:
            update_data['title'] = title[:255]
        if description:
            update_data['description'] = description[:2000]
        if writer:
            update_data['writer'] = writer[:100]
        if final_thumbnail_url is not None:
            update_data['thumbnail_url'] = final_thumbnail_url
        if news_link:
            update_data['news_link'] = news_link
            
        if not update_data:
            conn.close()
            return jsonify({"error": "No data provided to update"}), 400
            
        update_data['updated_at'] = datetime.utcnow().isoformat()
        
        try:
            set_clause = ', '.join(f'{key} = ?' for key in update_data.keys())
            query = f'UPDATE news SET {set_clause} WHERE id = ?'
            params = list(update_data.values()) + [id]
            
            cursor.execute(query, params)
            conn.commit()
            
            return jsonify({"message": "News updated successfully"})
            
        except sqlite3.Error as e:
            logger.error(f"Database error: {str(e)}")
            return jsonify({"error": f"Database error: {str(e)}"}), 500
        finally:
            conn.close()

    @app.route('/api/news/<int:id>', methods=['DELETE'])
    def delete_news(id):
        """
        Delete a news item
        ---
        parameters:
          - name: id
            in: path
            type: integer
            required: true
        responses:
          200:
            description: News item deleted successfully
          404:
            description: News item not found
        """
        try:
            conn = sqlite3.connect('news.db')
            cursor = conn.cursor()
            
            cursor.execute('SELECT id FROM news WHERE id = ?', (id,))
            if not cursor.fetchone():
                return jsonify({"error": "News item not found"}), 404
                
            cursor.execute('DELETE FROM news WHERE id = ?', (id,))
            conn.commit()
            
            return jsonify({"message": "News deleted successfully"})
            
        except sqlite3.Error as e:
            logger.error(f"Database error: {str(e)}")
            return jsonify({"error": f"Database error: {str(e)}"}), 500
        finally:
            conn.close()

    @app.route('/api/news', methods=['DELETE'])
    def delete_all_news():
        """
        Delete all news items and reset ID sequence
        ---
        responses:
          200:
            description: All news items deleted successfully
          500:
            description: Database error
        """
        try:
            conn = sqlite3.connect('news.db')
            cursor = conn.cursor()
            
            cursor.execute('DELETE FROM news')
            conn.commit()
            
            reset_db_sequence()
            
            return jsonify({"message": "All news items deleted successfully and ID sequence reset"})
            
        except sqlite3.Error as e:
            logger.error(f"Database error: {str(e)}")
            return jsonify({"error": f"Database error: {str(e)}"}), 500
        finally:
            conn.close()

    @app.route('/api/reset_db', methods=['POST'])
    def reset_db():
        """
        Reset the database by deleting all news, teams, events, ewc_info items, resetting ID sequence, and optionally clearing uploads
        ---
        consumes:
          - application/json
        parameters:
          - name: clear_uploads
            in: body
            type: boolean
            required: false
            description: Whether to delete all uploaded files in the uploads folder
        responses:
          200:
            description: Database reset successfully
          500:
            description: Database error
        """
        data = request.get_json() or {}
        clear_uploads = data.get('clear_uploads', False)
        
        try:
            conn = sqlite3.connect('news.db')
            cursor = conn.cursor()
            
            cursor.execute('DELETE FROM news')
            cursor.execute('DELETE FROM teams')
            cursor.execute('DELETE FROM events')
            cursor.execute('DELETE FROM ewc_info')
            conn.commit()
            
            reset_db_sequence()
            
            if clear_uploads:
                if os.path.exists(UPLOAD_FOLDER):
                    for filename in os.listdir(UPLOAD_FOLDER):
                        file_path = os.path.join(UPLOAD_FOLDER, filename)
                        try:
                            if os.path.isfile(file_path):
                                os.unlink(file_path)
                            elif os.path.isdir(file_path):
                                shutil.rmtree(file_path)
                            logger.debug(f"Deleted file: {file_path}")
                        except Exception as e:
                            logger.error(f"Failed to delete file {file_path}: {str(e)}")
                    logger.debug("Cleared uploads folder")
            
            return jsonify({"message": "Database reset successfully"})
            
        except sqlite3.Error as e:
            logger.error(f"Database error: {str(e)}")
            return jsonify({"error": f"Database error: {str(e)}"}), 500
        finally:
            conn.close()

    @app.route('/api/ewc_matches', methods=['POST'])
    def get_ewc_matches():
        """
        Get Esports World Cup 2025 match data for a specified game
        ---
        consumes:
          - application/json
        parameters:
          - name: game
            in: body
            type: string
            required: true
            description: The game slug (e.g., 'dota2', 'csgo', 'lol')
        responses:
          200:
            description: Successfully retrieved match data
          400:
            description: Missing or invalid game parameter
          500:
            description: Server error while fetching match data
        """
        data = request.get_json()
        game_slug = data.get('game')

        if not game_slug:
            logger.error("Missing 'game' in request body")
            return jsonify({"error": "Missing 'game' in request body"}), 400

        game_slug = re.sub(r'[^a-z0-9]', '', game_slug.lower())

        try:
            url = f'https://liquipedia.net/{game_slug}/Esports_World_Cup/2025/Group_Stage'
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36'
            }
            logger.debug(f"Fetching data from {url}")
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            data = {}
            group_boxes = soup.select('div.template-box')
            for group in group_boxes:
                group_name_tag = group.select_one('.brkts-matchlist-title')
                group_name = group_name_tag.text.strip() if group_name_tag else 'Unknown Group'

                group_matches = []
                matches_by_day = group.select('.brkts-matchlist-match')

                for match in matches_by_day:
                    teams = match.select('.brkts-matchlist-opponent')

                    if len(teams) == 2:
                        team1_name = teams[0].get('aria-label', 'N/A')
                        logo1_tag = teams[0].select_one('img')
                        logo1 = f"https://liquipedia.net{logo1_tag['src']}" if logo1_tag else "N/A"

                        team2_name = teams[1].get('aria-label', 'N/A')
                        logo2_tag = teams[1].select_one('img')
                        logo2 = f"https://liquipedia.net{logo2_tag['src']}" if logo2_tag else "N/A"
                    else:
                        team1_name, logo1 = "N/A", "N/A"
                        team2_name, logo2 = "N/A", "N/A"

                    time_tag = match.select_one('span.timer-object')
                    match_time = time_tag.text.strip() if time_tag else "N/A"

                    score_tag = match.select_one('.brkts-matchlist-score')
                    score = score_tag.text.strip() if score_tag else "N/A"

                    match_info = {
                        "Team1": {
                            "Name": team1_name,
                            "Logo": logo1
                        },
                        "Team2": {
                            "Name": team2_name,
                            "Logo": logo2
                        },
                        "MatchTime": match_time,
                        "Score": score
                    }

                    group_matches.append(match_info)

                data[group_name] = group_matches

            logger.debug(f"Successfully retrieved EWC match data for {game_slug}")
            return jsonify({
                "message": f"Match data retrieved successfully for {game_slug}",
                "data": data
            })

        except requests.RequestException as e:
            logger.error(f"Error fetching data from Liquipedia for {game_slug}: {str(e)}")
            return jsonify({"error": f"Failed to fetch data: {str(e)}"}), 500
        except Exception as e:
            logger.error(f"Server error while processing match data for {game_slug}: {str(e)}")
            return jsonify({"error": f"Server error: {str(e)}"}), 500

    @app.route('/api/ewc_matches_by_day', methods=['POST'])
    def get_ewc_matches_by_day():
        """
        Get Esports World Cup 2025 match data for a specified game, grouped by day and sorted by group and time
        ---
        consumes:
          - application/json
        parameters:
          - name: game
            in: body
            type: string
            required: true
            description: The game slug (e.g., 'dota2', 'csgo', 'lol')
          - name: date
            in: body
            type: string
            required: false
            description: Filter matches by specific date (YYYY-MM-DD, e.g., '2025-07-08')
        responses:
          200:
            description: Successfully retrieved match data grouped by day
          400:
            description: Missing or invalid game parameter or invalid date format
          500:
            description: Server error while fetching match data
        """
        data = request.get_json()
        game_slug = data.get('game')
        filter_date = data.get('date')

        if not game_slug:
            logger.error("Missing 'game' in request body")
            return jsonify({"error": "Missing 'game' error"})
        return jsonify({"error": "Missing 'game' in request body"}), 400

        if filter_date and not is_valid_date(filter_date):
            logger.error(f"Invalid date format: {filter_date}. Expected YYYY-MM-DD")
            return jsonify({"error": "Invalid date format. Expected YYYY-MM-DD"}), 400

        game_slug = re.sub(r'[^a-z0-0-9]', '', game_slug.lower())

        try:
            url = f'https://liquipedia.net/{game_slug}/Esports_World_Cup/2025/Group_Stage'
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36'
            }
            logger.debug(f"Fetching data from {url}")
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            matches_by_day = defaultdict(lambda: defaultdict(list))
            group_boxes = soup.select('div.template-box')
            for group in group_boxes:
                group_name_tag_name_tag = group.select_one('.brkts-matchlist-title')
                group_name = group_name_tag.text.strip() if group_name_tag else 'Unknown Group'

                matches_by_day_group = group.select('.brkts-matchlist-match')

                for match in matches_by_day_group:
                    teams = match.select('.brkts-matchlist-opponent')

                    if len(teams) == 2:
                        team1_name = teams[0].get('aria-label', 'N/A')
                        logo1_tag = teams[0].select_one('img')
                        logo1 = f"https://liquipedia.net{logo1_tag['src']}" if logo1_tag else "N/A"

                        team2_name = teams[1].get('aria-label', 'N/A')
                        logo2_tag = teams[1].select_one('img')
                        logo2 = f"https://liquipedia.net{logo2_tag['src']}" if logo2_tag else "N/A"
                    else:
                        team1_name, logo1 = "N/A", "N/A"
                        team2_name, logo2 = "N/A", "N/A"

                    time_tag = match.select_one('span.timer-object')
                    match_time = time_tag.text.strip() if time_tag else "N/A"

                    score_tag = match.select_one('.brkts-matchlist-score')
                    score = score_tag.text.strip() if score_tag else "N/A"

                    try:
                        date_str = ' '.join(match_time.split(' - ')[0].split()[:3])
                        parsed_date = dt.strptime(date_str, '%B %d, %Y')
                        match_date = parsed_date.strftime('%Y-%m-%d')
                    except (IndexError, ValueError):
                        match_date = "Unknown Date"

                    if filter_date and match_date != filter_date:
                        continue

                    match_info = {
                        "Team1": {
                            "Name": team1_name,
                            "Logo": logo1
                        },
                        "Team2": {
                            "Name": team2_name,
                            "Logo": logo2
                        },
                        "MatchTime": match_time,
                        "Score": score,
                        "_sort_time": parse_match_datetime(match_time)
                    }

                    matches_by_day[match_date][group_name].append(match_info)

            formatted_data = {}
            date_groups = []
            for date in matches_by_day.keys():
                try:
                    parsed_date = dt.strptime(date, '%Y-%m-%d') if date != "Unknown Date" else dt.max
                    groups = {}
                    for group in sorted(matches_by_day[date].keys()):
                        sorted_matches = sorted(
                            matches_by_day[date][group],
                            key=lambda x: x['_sort_time']
                        )
                        groups[group] = [{k: v for k, v in match.items() if k != '_sort_time'} for match in sorted_matches]
                    date_groups.append((parsed_date, date, groups))
                except ValueError:
                    date_groups.append((dt.max, date, {
                        group: [
                            {k: v for k, v in match.items() if k != '_sort_time'}
                            for match in sorted(
                                matches_by_day[date][group],
                                key=lambda x: x['_sort_time']
                            )
                        ]
                        for group in sorted(matches_by_day[date].keys())
                    }))

            date_groups.sort(key=lambda x: x[0])
            for _, date_str, groups in date_groups:
                formatted_data[date_str] = groups

            logger.debug(f"Successfully retrieved EWC match data for {game_slug}{' on ' + filter_date if filter_date else ''}")
            return jsonify({
                "message": f"Match data retrieved successfully for {game_slug}{' on ' + filter_date if filter_date else ''}",
                "data": formatted_data
            })

        except requests.RequestException as e:
            logger.error(f"Error fetching data from Liquipedia for {game_slug}: {str(e)}")
            return jsonify({"error": f"Failed to fetch data: {str(e)}"}), 500
        except Exception as e:
            logger.error(f"Server error while processing match data for {game_slug}: {str(e)}")
            return jsonify({"error": f"Server error: {str(e)}"}), 500

    @app.route('/api/ewc_matches_by_date', methods=['POST'])
    def get_ewc_matches_by_date():
        """
        Get Esports World Cup 2025 match data for a specified game and date, sorted by group and time
        ---
        consumes:
          - application/json
        parameters:
          - name: game
            in: body
            type: string
            required: true
            description: The game slug (e.g., 'dota2', 'csgo', 'lol')
          - name: date
            in: body
            type: string
            required: true
            description: The specific date to filter matches (YYYY-MM-DD, e.g., '2025-07-08')
        responses:
          200:
            description: Successfully retrieved match data for the specified date
          400:
            description: Missing or invalid game or date parameter
          500:
            description: Server error while fetching match data
        """
        data = request.get_json()
        game_slug = data.get('game')
        filter_date = data.get('date')

        if not game_slug:
            logger.error("Missing 'game' in request body")
            return jsonify({"error": "Missing 'game' in request body"}), 400

        if not filter_date:
            logger.error("Missing 'date' in request body")
            return jsonify({"error": "Missing 'date' in request body"}), 400

        if not is_valid_date(filter_date):
            logger.error(f"Invalid date format: {filter_date}. Expected YYYY-MM-DD")
            return jsonify({"error": "Invalid date format. Expected YYYY-MM-DD"}), 400

        game_slug = re.sub(r'[^a-z0-9]', '', game_slug.lower())

        try:
            url = f'https://liquipedia.net/{game_slug}/Esports_World_Cup/2025/Group_Stage'
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36'
            }
            logger.debug(f"Fetching data from {url}")
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            matches_by_group = defaultdict(list)
            group_boxes = soup.select('div.template-box')
            for group in group_boxes:
                group_name_tag = group.select_one('.brkts-matchlist-title')
                group_name = group_name_tag.text.strip() if group_name_tag else 'Unknown Group'

                matches_by_day_group = group.select('.brkts-matchlist-match')

                for match in matches_by_day_group:
                    teams = match.select('.brkts-matchlist-opponent')

                    if len(teams) == 2:
                        team1_name = teams[0].get('aria-label', 'N/A')
                        logo1_tag = teams[0].select_one('img')
                        logo1 = f"https://liquipedia.net{logo1_tag['src']}" if logo1_tag else 'N/A'

                        team2_name = teams[1].get('aria-label', 'N/A')
                        logo2_tag = teams[1].select_one('img')
                        logo2 = f"https://liquipedia.net{logo2_tag['src']}" if logo2_tag else 'N/A'
                    else:
                        team1_name, logo1 = 'N/A', 'N/A'
                        team2_name, logo2 = 'N/A', 'N/A'

                    time_tag = match.select_one('span.timer-object')
                    match_time = time_tag.text.strip() if time_tag else 'N/A'

                    score_tag = match.select_one('.brkts-matchlist-score')
                    score = score_tag.text.strip() if score_tag else 'N/A'

                    try:
                        date_str = ' '.join(match_time.split(' - ')[0].split()[:3])
                        parsed_date = dt.strptime(date_str, '%B %d, %Y')
                        match_date = parsed_date.strftime('%Y-%m-%d')
                    except (IndexError, ValueError):
                        match_date = 'Unknown Date'

                    if match_date != filter_date:
                        continue

                    match_info = {
                        'Team1': {
                            'Name': team1_name,
                            'Logo': logo1
                        },
                        'Team2': {
                            'Name': team2_name,
                            'Logo': logo2
                        },
                        'MatchTime': match_time,
                        'Score': score,
                        '_sort_time': parse_match_datetime(match_time)
                    }

                    matches_by_group[group_name].append(match_info)

            formatted_data = {}
            for group in sorted(matches_by_group.keys()):
                sorted_matches = sorted(
                    matches_by_group[group],
                    key=lambda x: x['_sort_time']
                )
                formatted_data[group] = [
                    {k: v for k, v in match.items() if k != '_sort_time'}
                    for match in sorted_matches
                ]

            logger.debug(f"Successfully retrieved EWC match data for {game_slug} on {filter_date}")
            return jsonify({
                'message': f'Match data retrieved successfully for {game_slug} on {filter_date}',
                'data': formatted_data
            })

        except requests.RequestException as e:
            logger.error(f"Error fetching data from Liquipedia for {game_slug}: {str(e)}")
            return jsonify({'error': f'Failed to fetch data: {str(e)}'}), 500
        except Exception as e:
            logger.error(f"Server error while processing match data for {game_slug}: {str(e)}")
            return jsonify({'error': f'Server error: {str(e)}'}), 500

    @app.route('/api/ewc_info', methods=['GET'])
    def get_ewc_info():
        """
        Get Esports World Cup 2025 information
        ---
        parameters:
          - name: live
            in: query
            type: boolean
            required: false
            description: Fetch live data from Liquipedia if true, otherwise use cached database data
        responses:
          200:
            description: Successfully retrieved EWC information
          500:
            description: Server error while fetching EWC information
        """
        live = request.args.get('live', 'false').lower() == 'true'
        
        try:
            info_data = get_ewc_information(live=live)
            if not info_data:
                logger.warning("No EWC information retrieved")
                return jsonify({
                    "message": "No information found",
                    "data": {}
                }), 200

            logger.debug(f"Successfully retrieved EWC info {'from Liquipedia' if live else 'from database'}")
            return jsonify({
                "message": "EWC information retrieved successfully",
                "data": info_data
            })

        except Exception as e:
            logger.error(f"Server error while processing EWC info: {str(e)}")
            return jsonify({"error": f"Server error: {str(e)}"}), 500

    @app.route('/api/ewc_teams', methods=['GET'])
    def get_ewc_teams():
        """
        Get Esports World Cup 2025 teams data
        ---
        parameters:
          - name: live
            in: query
            type: boolean
            required: false
            description: Fetch live data from Liquipedia if true, otherwise use cached data
        responses:
          200:
            description: Successfully retrieved teams data
          500:
            description: Server error while fetching teams data
        """
        live = request.args.get('live', 'false').lower() == 'true'
        
        try:
            teams_data = get_teams_ewc(live=live)
            if not teams_data:
                logger.warning("No teams data retrieved")
                return jsonify({
                    "message": "No teams data found",
                    "data": []
                }), 200

            logger.debug(f"Successfully retrieved EWC teams data {'from Liquipedia' if live else 'from database'}")
            return jsonify({
                "message": "Teams data retrieved successfully",
                "data": teams_data
            })

        except Exception as e:
            logger.error(f"Server error while processing teams data: {str(e)}")
            return jsonify({"error": f"Server error: {str(e)}"}), 500

    @app.route('/api/ewc_events', methods=['GET'])
    def get_ewc_events():
        """
        Get Esports World Cup 2025 events data
        ---
        parameters:
          - name: live
            in: query
            type: boolean
            required: false
            description: Fetch live data from Liquipedia if true, otherwise use cached data
        responses:
          200:
            description: Successfully retrieved events data
          500:
            description: Server error while fetching events data
        """
        live = request.args.get('live', 'false').lower() == 'true'
        
        try:
            events_data = get_events_ewc(live=live)
            if not events_data:
                logger.warning("No events data retrieved")
                return jsonify({
                    "message": "No events data found",
                    "data": []
                }), 200

            logger.debug(f"Successfully retrieved EWC events data {'from Liquipedia' if live else 'from database'}")
            return jsonify({
                "message": "Events data retrieved successfully",
                "data": events_data
            })

        except Exception as e:
            logger.error(f"Server error while processing events data: {str(e)}")
            return jsonify({"error": f"Server error: {str(e)}"}), 500

    @app.route('/api/ewc_all_matches', methods=['GET'])
    def get_ewc_all_matches():
        """
        Get all Esports World Cup 2025 match data for all games with pagination and filtering, sorted by match day and time
        ---
        parameters:
          - name: live
            in: query
            type: boolean
            required: false
            description: Fetch live data from Liquipedia if true, otherwise use cached JSON data
          - name: page
            in: query
            type: integer
            default: 1
            description: Page number for pagination
          - name: per_page
            in: query
            type: integer
            default: 100
            description: Number of matches per page
          - name: game
            in: query
            type: string
            required: false
            description: Filter by game name (e.g., 'Dota 2')
          - name: group
            in: query
            type: string
            required: false
            description: Filter by group name (e.g., 'Group A')
          - name: date
            in: query
            type: string
            required: false
            description: Filter by date (YYYY-MM-DD, e.g., '2025-07-08')
        responses:
          200:
            description: Successfully retrieved all match data
          400:
            description: Invalid date format
          500:
            description: Server error while fetching match data
        """
        live = request.args.get('live', 'false').lower() == 'true'
        page = max(1, request.args.get('page', 1, type=int))
        per_page = max(1, min(100, request.args.get('per_page', 10, type=int)))
        filter_game = request.args.get('game', '').strip()
        filter_group = request.args.get('group', '').strip()
        filter_date = request.args.get('date', '').strip()

        if filter_date and not is_valid_date(filter_date):
            logger.error(f"Invalid date format: {filter_date}. Expected YYYY-MM-DD")
            return jsonify({"error": "Invalid date format. Expected YYYY-MM-DD"}), 400

        try:
            if not live:
                # Try to load from cached JSON file
                try:
                    with open('all_matches_EWC.json', 'r', encoding='utf-8') as f:
                        all_matches = json.load(f)
                    logger.debug("Successfully retrieved all EWC match data from cached JSON")
                except FileNotFoundError:
                    logger.warning("Cached all_matches_EWC.json not found")
                    all_matches = None
                except json.JSONDecodeError as e:
                    logger.error(f"Error decoding all_matches_EWC.json: {str(e)}")
                    return jsonify({"error": f"Error decoding cached data: {str(e)}"}), 500
            else:
                all_matches = None

            # Fetch events data (from JSON or Liquipedia)
            try:
                with open('events_ewc.json', 'r', encoding='utf-8') as f:
                    games = json.load(f)
                logger.debug("Retrieved events data from events_ewc.json")
            except FileNotFoundError:
                logger.warning("events_ewc.json not found, fetching events from Liquipedia")
                games = get_events_ewc(live=True)
                if not games:
                    logger.error("No events data available from Liquipedia")
                    return jsonify({"error": "No events data available"}), 500
                # Save fetched events to JSON file for future caching
                try:
                    with open('events_ewc.json', 'w', encoding='utf-8') as f:
                        json.dump(games, f, ensure_ascii=False, indent=2)
                    logger.debug("Saved events data to events_ewc.json")
                except Exception as e:
                    logger.error(f"Error saving events_ewc.json: {str(e)}")
            except json.JSONDecodeError as e:
                logger.error(f"Error decoding events_ewc.json: {str(e)}")
                return jsonify({"error": f"Error decoding events data: {str(e)}"}), 500

            if all_matches:
                all_matches = {}
                failed_games = []
                for game in games:
                    game_name = game['name']
                    game_link = game['link']
                    if filter_game and game_name.lower() != filter_game.lower():
                        continue
                    try:
                        logger.debug(f"Fetching matches for {game_name}")
                        match_data = scrape_group_stage(game_name, game_link)
                        if isinstance(match_data, dict) and "message" in match_data and "404 Client Error" in match_data["message"]:
                            failed_games.append(game_name)
                            continue
                        all_matches[game_name] = match_data
                    except Exception as e:
                        logger.error(f"Error fetching matches for {game_name}: {str(e)}")
                        failed_games.append(game_name)
                        continue

                # Save to JSON file for caching
                try:
                    with open('all_matches_EWC.json', 'w', encoding='utf-8') as f:
                        json.dump(all_matches, f, ensure_ascii=False, indent=2)
                    logger.debug("Saved all matches to all_matches_EWC.json")
                except Exception as e:
                    logger.error(f"Error saving all_matches_EWC.json: {str(e)}")
            else:
                failed_games = []
                # Filter out 404 errors from cached data
                games_to_remove = []
                for game_name, match_data in all_matches.items():
                    if isinstance(match_data, dict) and "message" in match_data and "404 Client Error" in match_data["message"]:
                        failed_games.append(game_name)
                        games_to_remove.append(game_name)
                for game_name in games_to_remove:
                    del all_matches[game_name]

            # Collect all matches for sorting
            all_matches_list = []
            for game_name, game_data in all_matches.items():
                if filter_game and game_name.lower() != filter_game.lower():
                    continue
                if isinstance(game_data, dict) and "message" in game_data:
                    continue  # Skip games with error messages

                for group_name, matches in game_data.items():
                    if filter_group and group_name.lower() != filter_group.lower():
                        continue
                    for match in matches:
                        match_time = match.get('MatchTime', 'N/A')
                        try:
                            date_str = ' '.join(match_time.split(' - ')[0].split()[:3]) if ' - ' in match_time else match_time
                            parsed_date = dt.strptime(date_str, '%B %d, %Y')
                            match_date = parsed_date.strftime('%Y-%m-%d')
                        except (IndexError, ValueError):
                            match_date = "Unknown Date"

                        if filter_date and match_date != filter_date:
                            continue

                        all_matches_list.append({
                            "game": game_name,
                            "group": group_name,
                            "match": match,
                            "_sort_datetime": parse_match_datetime(match_time)
                        })

            # Sort matches by datetime
            sorted_matches = sorted(
                all_matches_list,
                key=lambda x: x['_sort_datetime']
            )

            # Apply pagination
            total_matches = len(sorted_matches)
            start_idx = (page - 1) * per_page
            end_idx = start_idx + per_page
            paginated_matches = sorted_matches[start_idx:end_idx]

            # Reconstruct filtered and paginated matches into the original structure
            filtered_matches = {}
            for match_item in paginated_matches:
                game_name = match_item['game']
                group_name = match_item['group']
                match = match_item['match']
                if game_name not in filtered_matches:
                    filtered_matches[game_name] = {}
                if group_name not in filtered_matches[game_name]:
                    filtered_matches[game_name][group_name] = []
                filtered_matches[game_name][group_name].append(match)

            # Include games with messages (e.g., "Matches have not been added yet")
            for game_name, game_data in all_matches.items():
                if filter_game and game_name.lower() != filter_game.lower():
                    continue
                if isinstance(game_data, dict) and "message" in game_data:
                    filtered_matches[game_name] = game_data

            # Prepare response
            response = {
                "message": "All match data retrieved successfully",
                "data": filtered_matches,
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": total_matches,
                    "pages": (total_matches + per_page - 1) // per_page
                },
                "failed_games": failed_games
            }

            logger.debug("Successfully retrieved all EWC match data with filters, pagination, and sorted by match day and time")
            return jsonify(response)

        except Exception as e:
            logger.error(f"Server error while processing all match data: {str(e)}")
            return jsonify({"error": f"Server error: {str(e)}"}), 500
    @app.route('/api/ewc_prize_distribution', methods=['GET'])
    def get_ewc_prize_distribution():
        """
        Get Esports World Cup 2025 prize distribution data
        ---
        parameters:
        - name: live
            in: query
            type: boolean
            required: false
            description: Fetch live data from Liquipedia if true, otherwise use cached database data
        - name: page
            in: query
            type: integer
            default: 1
            description: Page number for pagination
        - name: per_page
            in: query
            type: integer
            default: 10
            description: Number of prize entries per page
        - name: filter
            in: query
            type: string
            required: false
            description: Filter by place or prize (e.g., '1st' or '50000')
        responses:
        200:
            description: Successfully retrieved prize distribution data
        500:
            description: Server error while fetching prize distribution data
        """
        live = request.args.get('live', 'false').lower() == 'true'
        page = max(1, request.args.get('page', 1, type=int))
        per_page = max(1, min(100, request.args.get('per_page', 10, type=int)))
        filter_query = request.args.get('filter', '').strip()

        try:
            # Fetch prize distribution data
            prize_data = get_prize_distribution(live=live)
            if not prize_data:
                logger.warning("No prize distribution data retrieved")
                return jsonify({
                    "message": "No prize distribution data found",
                    "data": [],
                    "pagination": {
                        "page": page,
                        "per_page": per_page,
                        "total": 0,
                        "pages": 0
                    }
                }), 200

            # Apply filtering
            if filter_query:
                filtered_data = [
                    item for item in prize_data
                    if filter_query.lower() in item['place'].lower() or
                    filter_query.lower() in item['prize'].lower()
                ]
            else:
                filtered_data = prize_data

            # Apply pagination
            total = len(filtered_data)
            start_idx = (page - 1) * per_page
            end_idx = start_idx + per_page
            paginated_data = filtered_data[start_idx:end_idx]

            logger.debug(f"Successfully retrieved EWC prize distribution data {'from Liquipedia' if live else 'from database'}")
            return jsonify({
                "message": "Prize distribution data retrieved successfully",
                "data": paginated_data,
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": total,
                    "pages": (total + per_page - 1) // per_page
                }
            })

        except Exception as e:
            logger.error(f"Server error while processing prize distribution data: {str(e)}")
            return jsonify({"error": f"Server error: {str(e)}"}), 500
    @app.route('/api/ewc_all_matches_by_day', methods=['POST'])
    def get_ewc_all_matches_by_day():
        """
        Get all Esports World Cup 2025 match data for all games, grouped by day and sorted by game, group, and time
        ---
        consumes:
        - application/json
        parameters:
        - name: date
            in: body
            type: string
            required: false
            description: Filter matches by specific date (YYYY-MM-DD, e.g., '2025-07-08')
        responses:
        200:
            description: Successfully retrieved all match data grouped by day
        400:
            description: Invalid date format
        500:
            description: Server error while fetching match data
        """
        data = request.get_json() or {}
        filter_date = data.get('date')

        if filter_date and not is_valid_date(filter_date):
            logger.error(f"Invalid date format: {filter_date}. Expected YYYY-MM-DD")
            return jsonify({"error": "Invalid date format. Expected YYYY-MM-DD"}), 400

        try:
            # Try to load from cached JSON file
            try:
                with open('all_matches_EWC.json', 'r', encoding='utf-8') as f:
                    all_matches = json.load(f)
                logger.debug("Retrieved all matches from all_matches_EWC.json")
            except FileNotFoundError:
                logger.warning("Cached all_matches_EWC.json not found, fetching live data")
                try:
                    with open('events_ewc.json', 'r', encoding='utf-8') as f:
                        games = json.load(f)
                    logger.debug("Retrieved events data from events_ewc.json")
                except FileNotFoundError:
                    logger.warning("events_ewc.json not found, fetching events from Liquipedia")
                    games = get_events_ewc(live=True)
                    if not games:
                        logger.error("No events data available from Liquipedia")
                        return jsonify({"error": "No events data available"}), 500
                    # Save fetched events to JSON file for future caching
                    try:
                        with open('events_ewc.json', 'w', encoding='utf-8') as f:
                            json.dump(games, f, ensure_ascii=False, indent=2)
                        logger.debug("Saved events data to events_ewc.json")
                    except Exception as e:
                        logger.error(f"Error saving events_ewc.json: {str(e)}")
                except json.JSONDecodeError as e:
                    logger.error(f"Error decoding events_ewc.json: {str(e)}")
                    return jsonify({"error": f"Error decoding events data: {str(e)}"}), 500

                all_matches = {}
                for game in games:
                    game_name = game['name']
                    game_link = game['link']
                    logger.debug(f"Fetching matches for: {game_name}")
                    all_matches[game_name] = scrape_group_stage(game_name, game_link)
                
                # Save to JSON file for caching
                try:
                    with open('all_matches_EWC.json', 'w', encoding='utf-8') as f:
                        json.dump(all_matches, f, ensure_ascii=False, indent=2)
                    logger.debug("Saved all matches to all_matches_EWC.json")
                except Exception as e:
                    logger.error(f"Error saving all_matches_EWC.json: {str(e)}")
            except json.JSONDecodeError as e:
                logger.error(f"Error decoding all_matches_EWC.json: {str(e)}")
                return jsonify({"error": f"Error decoding cached data: {str(e)}"}), 500

            matches_by_day = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
            for game_name, game_data in all_matches.items():
                if isinstance(game_data, dict) and "message" in game_data:
                    matches_by_day["Unknown Date"][game_name]["Unknown Group"] = game_data
                    continue

                for group_name, matches in game_data.items():
                    for match in matches:
                        match_time = match.get('MatchTime', 'N/A')
                        try:
                            date_str = ' '.join(match_time.split(' - ')[0].split()[:3]) if ' - ' in match_time else match_time
                            parsed_date = dt.strptime(date_str, '%B %d, %Y')
                            match_date = parsed_date.strftime('%Y-%m-%d')
                        except (IndexError, ValueError):
                            match_date = "Unknown Date"

                        if filter_date and match_date != filter_date:
                            continue

                        match_info = {
                            "Team1": match.get("Team1", {"Name": "N/A", "Logo": "N/A"}),
                            "Team2": match.get("Team2", {"Name": "N/A", "Logo": "N/A"}),
                            "MatchTime": match_time,
                            "Score": match.get("Score", "N/A"),
                            "_sort_time": parse_match_datetime(match_time)
                        }

                        matches_by_day[match_date][game_name][group_name].append(match_info)

            formatted_data = {}
            date_groups = []
            for date in matches_by_day.keys():
                try:
                    parsed_date = dt.strptime(date, '%Y-%m-%d') if date != "Unknown Date" else dt.max
                    games = {}
                    for game_name in sorted(matches_by_day[date].keys()):
                        groups = {}
                        for group_name in sorted(matches_by_day[date][game_name].keys()):
                            if isinstance(matches_by_day[date][game_name][group_name], dict):
                                groups[group_name] = matches_by_day[date][game_name][group_name]
                            else:
                                sorted_matches = sorted(
                                    matches_by_day[date][game_name][group_name],
                                    key=lambda x: x['_sort_time']
                                )
                                groups[group_name] = [
                                    {k: v for k, v in match.items() if k != '_sort_time'}
                                    for match in sorted_matches
                                ]
                        games[game_name] = groups
                    date_groups.append((parsed_date, date, games))
                except ValueError:
                    games = {}
                    for game_name in sorted(matches_by_day[date].keys()):
                        groups = {}
                        for group_name in sorted(matches_by_day[date][game_name].keys()):
                            if isinstance(matches_by_day[date][game_name][group_name], dict):
                                groups[group_name] = matches_by_day[date][game_name][group_name]
                            else:
                                sorted_matches = sorted(
                                    matches_by_day[date][game_name][group_name],
                                    key=lambda x: x['_sort_time']
                                )
                                groups[group_name] = [
                                    {k: v for k, v in match.items() if k != '_sort_time'}
                                    for match in sorted_matches
                                ]
                        games[game_name] = groups
                    date_groups.append((dt.max, date, games))

            date_groups.sort(key=lambda x: x[0])
            for _, date_str, games in date_groups:
                formatted_data[date_str] = games

            logger.debug(f"Successfully retrieved all EWC match data by day{' on ' + filter_date if filter_date else ''}")
            return jsonify({
                "message": f"All match data retrieved successfully{' on ' + filter_date if filter_date else ''}",
                "data": formatted_data
            })

        except Exception as e:
            logger.error(f"Server error while processing all match data by day: {str(e)}")
            return jsonify({"error": f"Server error: {str(e)}"}), 500
        
    @app.route('/api/ewc_games', methods=['GET'])
    def get_ewc_games_endpoint():
        """
        Get Esports World Cup 2025 games data
        ---
        parameters:
        - name: live
            in: query
            type: boolean
            required: false
            description: Fetch live data from Liquipedia if true, otherwise use cached database data
        responses:
        200:
            description: Successfully retrieved games data
        500:
            description: Server error while fetching games data
        """
        live = request.args.get('live', 'false').lower() == 'true'
        
        try:
            games_data = get_ewc_games(live=live)
            if not games_data:
                logger.warning("No games data retrieved")
                return jsonify({
                    "message": "No games data found",
                    "data": []
                }), 200

            logger.debug(f"Successfully retrieved EWC games data {'from Liquipedia' if live else 'from database'}")
            return jsonify({
                "message": "Games data retrieved successfully",
                "data": games_data
            })

        except Exception as e:
            logger.error(f"Server error while processing games data: {str(e)}")
            return jsonify({"error": f"Server error: {str(e)}"}), 500

def create_app():
    """Create and configure the Flask app"""
    app = Flask(__name__)
    Swagger(app)
    CORS(app, resources={r"/api/*": {"origins": "*"}})
    
    app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
    
    init_db()
    
    @app.route('/')
    def home():
        return jsonify({"message": "Welcome to Liquipedia Scraper API"})

    @app.route('/api/tournaments', methods=['POST'])
    def get_tournaments():
        data = request.get_json()
        game_slug = data.get("game")
        force = data.get("force", False)

        if not game_slug:
            return jsonify({"error": "Missing 'game' in request body"}), 400

        result = fetch_tournaments(game_slug, force=force)
        return jsonify(result)

    @app.route('/api/matches', methods=['POST'])
    def get_matches():
        data = request.get_json()
        game_slug = data.get("game")
        force = data.get("force", False)

        if not game_slug:
            return jsonify({"error": "Missing 'game' in request body"}), 400

        result = get_matches_by_status(game_slug, force=force)
        return jsonify(result)
    
    setup_routes(app)
    
    return app

app = create_app()

if __name__ == '__main__':
    app.run()