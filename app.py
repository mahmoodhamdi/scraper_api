import sqlite3
import re
import os
import logging
import shutil
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from flask import Flask, jsonify, request
from flasgger import Swagger
from urllib.parse import urlparse
from werkzeug.utils import secure_filename
from flask_cors import CORS
from scraper.liquipedia_scraper import fetch_tournaments, get_matches_by_status
from collections import defaultdict

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
    """Initialize the SQLite database and ensure news table has correct schema"""
    conn = sqlite3.connect('news.db')
    cursor = conn.cursor()
    
    # Create uploads directory if it doesn't exist
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)
    
    # Check if the news table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='news'")
    table_exists = cursor.fetchone()
    
    if table_exists:
        # Check the schema to ensure id is AUTOINCREMENT and thumbnail_url exists
        cursor.execute("PRAGMA table_info(news)")
        columns = {col[1]: col for col in cursor.fetchall()}
        
        # If thumbnail_url doesn't exist, add it
        if 'thumbnail_url' not in columns:
            cursor.execute('ALTER TABLE news ADD COLUMN thumbnail_url TEXT')
        
        # If id is not properly set up, migrate the table
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
            
            # Copy data from old table to new table
            cursor.execute('''
                INSERT INTO news_temp (id, title, description, writer, thumbnail_url, news_link, created_at, updated_at)
                SELECT id, title, description, writer, thumbnail_url, news_link, created_at, updated_at
                FROM news
            ''')
            
            # Drop old table and rename new one
            cursor.execute('DROP TABLE news')
            cursor.execute('ALTER TABLE news_temp RENAME TO news')
            
            conn.commit()
    else:
        # Create the news table if it doesn't exist
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
        conn.commit()
    
    conn.close()

def reset_db_sequence():
    """Reset the SQLite sequence for the news table"""
    try:
        conn = sqlite3.connect('news.db')
        cursor = conn.cursor()
        cursor.execute("DELETE FROM sqlite_sequence WHERE name='news'")
        conn.commit()
        logger.debug("Reset SQLite sequence for 'news' table")
    except sqlite3.Error as e:
        logger.error(f"Failed to reset SQLite sequence: {str(e)}")
        raise
    finally:
        conn.close()

def is_valid_url(url):
    """Validate URL format"""
    if not url:
        return True  # Allow empty URLs
    regex = re.compile(
        r'^https?://'  # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # domain
        r'localhost|'  # localhost
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # IP
        r'(?::\d+)?'  # optional port
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)
    return bool(regex.match(url))

def is_valid_thumbnail(url):
    """Validate thumbnail URL (image or link)"""
    if not url:
        return True  # Allow empty thumbnail
    # Check if URL points to an image
    image_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.webp')
    parsed = urlparse(url)
    return is_valid_url(url) and (
        parsed.path.lower().endswith(image_extensions) or 
        parsed.scheme in ('http', 'https')
    )

def setup_routes(app):
    """Setup news-related API routes"""
    
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
        
        # Required fields validation
        if not title or not writer:
            return jsonify({"error": "Title and writer are required"}), 400
            
        # Handle thumbnail (either URL or file)
        final_thumbnail_url = ''
        if 'thumbnail_file' in request.files and request.files['thumbnail_file']:
            file = request.files['thumbnail_file']
            logger.debug(f"Received file: {file.filename if file else 'None'}")
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                # Create unique filename with timestamp
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
            
        # URL validation for news_link
        if news_link and not is_valid_url(news_link):
            return jsonify({"error": "Invalid news link URL"}), 400
            
        # Input sanitization
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
            
            # Verify the inserted data
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
        
        # Validate sort parameter
        if sort not in ('created_at', 'title'):
            sort = 'created_at'
            
        try:
            conn = sqlite3.connect('news.db')
            cursor = conn.cursor()
            
            # Build query
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
            
            # Log the retrieved thumbnail_urls
            logger.debug(f"Retrieved news items: {[item['thumbnail_url'] for item in news_items]}")
            
            # Get total count for pagination
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
        # Check if news item exists
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
        
        # Handle thumbnail (either URL or file)
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
            
        # URL validation for news_link
        if news_link and not is_valid_url(news_link):
            return jsonify({"error": "Invalid news link URL"}), 400
            
        # Prepare update data
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
            # Build update query
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
            
            # Check if news item exists
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
            
            # Reset the ID sequence
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
        Reset the database by deleting all news items, resetting ID sequence, and optionally clearing uploads
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
            
            # Delete all news items
            cursor.execute('DELETE FROM news')
            conn.commit()
            
            # Reset the ID sequence
            reset_db_sequence()
            
            # Optionally clear the uploads folder
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

        # Sanitize game slug to prevent injection
        game_slug = re.sub(r'[^a-z0-9]', '', game_slug.lower())

        try:
            # Define the URL and headers for scraping
            url = f'https://liquipedia.net/{game_slug}/Esports_World_Cup/2025/Group_Stage'
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36'
            }

            # Fetch the webpage
            logger.debug(f"Fetching data from {url}")
            response = requests.get(url, headers=headers)
            response.raise_for_status()  # Raise an exception for bad status codes

            # Parse the HTML
            soup = BeautifulSoup(response.text, 'html.parser')
            data = {}

            # Extract group data
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
        Get Esports World Cup 2025 match data for a specified game, grouped by day and sorted by group
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
            description: Successfully retrieved match data grouped by day
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

        # Sanitize game slug to prevent injection
        game_slug = re.sub(r'[^a-z0-9]', '', game_slug.lower())

        try:
            # Define the URL and headers for scraping
            url = f'https://liquipedia.net/{game_slug}/Esports_World_Cup/2025/Group_Stage'
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36'
            }

            # Fetch the webpage
            logger.debug(f"Fetching data from {url}")
            response = requests.get(url, headers=headers)
            response.raise_for_status()  # Raise an exception for bad status codes

            # Parse the HTML
            soup = BeautifulSoup(response.text, 'html.parser')
            matches_by_day = defaultdict(lambda: defaultdict(list))

            # Extract group data
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

                    # Extract date from MatchTime (e.g., "July 8, 2025" from "July 8, 2025 - 12:00 AST")
                    try:
                        match_date = ' '.join(match_time.split(' - ')[0].split()[:3])
                    except IndexError:
                        match_date = "Unknown Date"

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

                    matches_by_day[match_date][group_name].append(match_info)

            # Convert defaultdict to regular dict and sort groups alphabetically
            formatted_data = {}
            for date in sorted(matches_by_day.keys()):
                formatted_data[date] = {
                    group: matches_by_day[date][group]
                    for group in sorted(matches_by_day[date].keys())
                }

            logger.debug(f"Successfully retrieved EWC match data for {game_slug} grouped by day")
            return jsonify({
                "message": f"Match data retrieved successfully for {game_slug}",
                "data": formatted_data
            })

        except requests.RequestException as e:
            logger.error(f"Error fetching data from Liquipedia for {game_slug}: {str(e)}")
            return jsonify({"error": f"Failed to fetch data: {str(e)}"}), 500
        except Exception as e:
            logger.error(f"Server error while processing match data for {game_slug}: {str(e)}")
            return jsonify({"error": f"Server error: {str(e)}"}), 500

def create_app():
    """Create and configure the Flask app"""
    app = Flask(__name__)
    Swagger(app)
    CORS(app, resources={r"/api/*": {"origins": "*"}})
    
    # Configure upload folder
    app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
    
    # Initialize database
    init_db()
    
    # Setup existing routes
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
    
    # Setup news routes
    setup_routes(app)
    
    return app

app = create_app()

if __name__ == '__main__':
    app.run()