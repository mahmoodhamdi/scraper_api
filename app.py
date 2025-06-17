from flask import Flask, request, jsonify
from scraper.liquipedia_scraper import fetch_tournaments

app = Flask(__name__)

@app.route('/')
def home():
    return jsonify({"message": "Welcome to Liquipedia Scraper API"})

@app.route('/api/tournaments', methods=['POST'])
def get_tournaments():
    data = request.get_json()
    game_slug = data.get("game")

    if not game_slug:
        return jsonify({"error": "Missing 'game' in request body"}), 400

    result = fetch_tournaments(game_slug)
    return jsonify(result)

if __name__ == '__main__':
    app.run(debug=True)
