import sqlite3
import re
from datetime import datetime
from flask import Flask, jsonify, request
from flasgger import Swagger
from urllib.parse import urlparse
from scraper.liquipedia_scraper import fetch_tournaments, get_matches_by_status

def init_db():
    """Initialize the SQLite database and ensure news table has correct schema"""
    conn = sqlite3.connect('news.db')
    cursor = conn.cursor()
    
    # Check if the news table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='news'")
    table_exists = cursor.fetchone()
    
    if table_exists:
        # Check the schema to ensure id is AUTOINCREMENT
        cursor.execute("PRAGMA table_info(news)")
        columns = [col[1] for col in cursor.fetchall()]
        id_info = [col for col in cursor.fetchall() if col[1] == 'id']
        
        # If id is not properly set up, migrate the table
        if not id_info or 'AUTOINCREMENT' not in cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='news'").fetchone()[0]:
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
        parameters:
          - name: body
            in: body
            required: true
            schema:
              type: object
              required:
                - title
                - writer
              properties:
                title:
                  type: string
                  example: "New Tournament Announced"
                description:
                  type: string
                  example: "Details about the upcoming tournament"
                writer:
                  type: string
                  example: "John Doe"
                thumbnail_url:
                  type: string
                  example: "https://example.com/image.jpg"
                representatives:
                  type: string
                  example: "https://example.com/news"
        responses:
          201:
            description: News item created successfully
          400:
            description: Invalid input data
        """
        data = request.get_json()
        
        # Required fields validation
        if not data.get('title') or not data.get('writer'):
            return jsonify({"error": "Title and writer are required"}), 400
            
        # URL validations
        if data.get('thumbnail_url') and not is_valid_thumbnail(data.get('thumbnail_url')):
            return jsonify({"error": "Invalid thumbnail URL"}), 400
            
        if data.get('news_link') and not is_valid_url(data.get('news_link')):
            return jsonify({"error": "Invalid news link URL"}), 400
            
        # Input sanitization
        title = data.get('title').strip()[:255]
        writer = data.get('writer').strip()[:100]
        description = data.get('description', '').strip()[:2000]
        thumbnail_url = data.get('thumbnail_url', '').strip()
        news_link = data.get('news_link', '').strip()
        
        try:
            conn = sqlite3.connect('news.db')
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO news (title, description, writer, thumbnail_url, news_link)
                VALUES (?, ?, ?, ?, ?)
            ''', (title, description, writer, thumbnail_url, news_link))
            
            conn.commit()
            news_id = cursor.lastrowid
            
            return jsonify({
                "message": "News created successfully",
                "id": news_id
            }), 201
            
        except sqlite3.Error as e:
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
                    'thumbnail_url': row[4],
                    'news_link': row[5],
                    'created_at': row[6],
                    'updated_at': row[7]
                } for row in cursor.fetchall()
            ]
            
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
            return jsonify({"error": f"Database error: {str(e)}"}), 500
        finally:
            conn.close()

    @app.route('/api/news/<int:id>', methods=['PUT'])
    def update_news(id):
        """
        Update an existing news item
        ---
        parameters:
          - name: id
            in: path
            type: integer
            required: true
          - name: body
            in: body
            required: true
            schema:
              type: object
              properties:
                title:
                  type: string
                description:
                  type: string
                writer:
                  type: string
                thumbnail_url:
                  type: string
                news_link:
                  type: string
        responses:
          200:
            description: News item updated successfully
          404:
            description: News item not found
        """
        data = request.get_json()
        
        # Validate URLs if provided
        if 'thumbnail_url' in data and data['thumbnail_url'] and not is_valid_thumbnail(data['thumbnail_url']):
            return jsonify({"error": "Invalid thumbnail URL"}), 400
            
        if 'news_link' in data and data['news_link'] and not is_valid_url(data['news_link']):
            return jsonify({"error": "Invalid news link URL"}), 400
            
        try:
            conn = sqlite3.connect('news.db')
            cursor = conn.cursor()
            
            # Check if news item exists
            cursor.execute('SELECT id FROM news WHERE id = ?', (id,))
            if not cursor.fetchone():
                return jsonify({"error": "News item not found"}), 404
                
            # Prepare update data
            update_data = {}
            if 'title' in data:
                update_data['title'] = data['title'].strip()[:255]
            if 'description' in data:
                update_data['description'] = data['description'].strip()[:2000]
            if 'writer' in data:
                update_data['writer'] = data['writer'].strip()[:100]
            if 'thumbnail_url' in data:
                update_data['thumbnail_url'] = data['thumbnail_url'].strip()
            if 'news_link' in data:
                update_data['news_link'] = data['news_link'].strip()
                
            if not update_data:
                return jsonify({"error": "No data provided to update"}), 400
                
            update_data['updated_at'] = datetime.utcnow().isoformat()
            
            # Build update query
            set_clause = ', '.join(f'{key} = ?' for key in update_data.keys())
            query = f'UPDATE news SET {set_clause} WHERE id = ?'
            params = list(update_data.values()) + [id]
            
            cursor.execute(query, params)
            conn.commit()
            
            return jsonify({"message": "News updated successfully"})
            
        except sqlite3.Error as e:
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
            return jsonify({"error": f"Database error: {str(e)}"}), 500
        finally:
            conn.close()

def create_app():
    """Create and configure the Flask app"""
    app = Flask(__name__)
    Swagger(app)
    
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

if __name__ == '__main__':
    app = create_app()
    app.run()