from flask import Flask, request, jsonify
from flasgger import Swagger
from scraper.liquipedia_scraper import fetch_tournaments, get_matches_by_status

app = Flask(__name__)
swagger = Swagger(app)

@app.route('/')
def home():
    return jsonify({"message": "Welcome to Liquipedia Scraper API"})

@app.route('/api/tournaments', methods=['POST'])
def get_tournaments():
    """
    Get tournaments for a specific game from Liquipedia
    ---
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - game
          properties:
            game:
              type: string
              example: worldoftanks
            force:
              type: boolean
              example: false
    responses:
      200:
        description: A list of tournaments grouped by status
    """
    data = request.get_json()
    game_slug = data.get("game")
    force = data.get("force", False)

    if not game_slug:
        return jsonify({"error": "Missing 'game' in request body"}), 400

    result = fetch_tournaments(game_slug, force=force)
    return jsonify(result)

@app.route('/api/matches', methods=['POST'])
def get_matches():
    """
    Get matches (upcoming and completed) from Liquipedia for a game
    ---
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - game
          properties:
            game:
              type: string
              example: worldoftanks
            force:
              type: boolean
              example: true
    responses:
      200:
        description: Matches grouped by tournament
    """
    data = request.get_json()
    game_slug = data.get("game")
    force = data.get("force", False)

    if not game_slug:
        return jsonify({"error": "Missing 'game' in request body"}), 400

    result = get_matches_by_status(game_slug, force=force)
    return jsonify(result)

if __name__ == '__main__':
    app.run()
