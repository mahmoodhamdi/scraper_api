import sqlite3
import re
import os
import logging
import shutil
import requests
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
    """Initialize the SQLite database with news, teams, and events tables"""
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

    conn.commit()
    conn.close()

def reset_db_sequence():
    """Reset the SQLite sequence for all tables"""
    try:
        conn = sqlite3.connect('news.db')
        cursor = conn.cursor()
        cursor.execute("DELETE FROM sqlite_sequence WHERE name IN ('news', 'teams', 'events')")
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

def parse_match_time(match_time):
    """Parse match time string to datetime for sorting"""
    try:
        time_str = match_time.split(' - ')[1] if ' - ' in match_time else match_time
        time_str = time_str.split()[0]
        parsed_time = dt.strptime(time_str, '%H:%M').time()
        return parsed_time
    except (IndexError, ValueError):
        return time.max

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
        Reset the database by deleting all news, teams, events items, resetting ID sequence, and optionally clearing uploads
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
            return jsonify({"error": "Missing 'game' in request body"}), 400

        if filter_date and not is_valid_date(filter_date):
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
            matches_by_day = defaultdict(lambda: defaultdict(list))
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
                        "_sort_time": parse_match_time(match_time)
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

                    if match_date != filter_date:
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
                        "_sort_time": parse_match_time(match_time)
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
                "message": f"Match data retrieved successfully for {game_slug} on {filter_date}",
                "data": formatted_data
            })

        except requests.RequestException as e:
            logger.error(f"Error fetching data from Liquipedia for {game_slug}: {str(e)}")
            return jsonify({"error": f"Failed to fetch data: {str(e)}"}), 500
        except Exception as e:
            logger.error(f"Server error while processing match data for {game_slug}: {str(e)}")
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