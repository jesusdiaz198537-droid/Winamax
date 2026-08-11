import os
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

API_KEY = os.environ.get("ODDSPAPI_KEY")

@app.route("/")
def home():
    return jsonify({
        "status": "ok",
        "message": "Winamax Odds API funcionando"
    })

@app.route("/scan")
def scan():
    if not API_KEY:
        return jsonify({"error": "ODDSPAPI_KEY no configurada"}), 500

    tournament_id = request.args.get("tournamentId", "7")

    url = "https://api.oddspapi.io/v4/odds-by-tournaments"

    params = {
        "tournamentIds": tournament_id,
        "bookmakers": "winamax.es",
        "language": "es",
        "verbosity": 3,
        "apiKey": API_KEY
    }

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
