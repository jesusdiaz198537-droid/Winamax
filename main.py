import os
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

API_KEY = os.environ.get("ODDSPAPI_KEY")

BASE_URL = "https://api.oddspapi.io/v4/odds-by-tournaments"


@app.route("/")
def home():
    return jsonify({
        "status": "ok",
        "message": "Winamax Odds API funcionando"
    })


def get_odds():
    if not API_KEY:
        return None, "ODDSPAPI_KEY no configurada"

    tournament_id = request.args.get("tournamentId", "7")

    params = {
        "tournamentIds": tournament_id,
        "bookmakers": "winamax.es",
        "language": "es",
        "verbosity": 3,
        "apiKey": API_KEY
    }

    try:
        response = requests.get(BASE_URL, params=params, timeout=30)
        response.raise_for_status()
        return response.json(), None

    except Exception as e:
        return None, str(e)


@app.route("/scan")
def scan():
    data, error = get_odds()

    if error:
        return jsonify({"error": error}), 500

    return jsonify(data)


@app.route("/debug")
def debug():
    data, error = get_odds()

    if error:
        return jsonify({"error": error}), 500

    fixtures = data if isinstance(data, list) else [data]

    diagnostic = []

    for fixture_index, fixture in enumerate(fixtures):

        bookmaker_odds = fixture.get("bookmakerOdds", {})
        winamax = bookmaker_odds.get("winamax.es", {})
        markets = winamax.get("markets", {})

        fixture_result = {
            "fixture_index": fixture_index,
            "fixtureId": fixture.get("fixtureId"),
            "participant1Name": fixture.get("participant1Name"),
            "participant2Name": fixture.get("participant2Name"),
            "bookmakerFixtureId": winamax.get("bookmakerFixtureId"),
            "markets": []
        }

        for market_id, market in markets.items():

            market_result = {
                "market_id": market_id,
                "marketActive": market.get("marketActive"),
                "outcomes": []
            }

            outcomes = market.get("outcomes", {})

            for outcome_id, outcome in outcomes.items():

                players = outcome.get("players", {})

                for player_id, player in players.items():

                    market_result["outcomes"].append({
                        "outcome_id": outcome_id,
                        "player_id": player_id,
                        "active": player.get("active"),
                        "mainLine": player.get("mainLine"),
                        "playerName": player.get("playerName"),
                        "price": player.get("price"),
                        "changedAt": player.get("changedAt")
                    })

            fixture_result["markets"].append(market_result)

        diagnostic.append(fixture_result)

    return jsonify({
        "status": "ok",
        "fixtures": diagnostic
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
