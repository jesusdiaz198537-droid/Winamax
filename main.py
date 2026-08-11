import os
import requests
from flask import Flask, jsonify

app = Flask(__name__)

API_KEY = os.environ.get("ODDSAPI_KEY")


@app.route("/")
def home():
    return jsonify({
        "status": "ok",
        "message": "Winamax Odds API funcionando"
    })


@app.route("/scan")
def scan():
    if not API_KEY:
        return jsonify({
            "error": "ODDSAPI_KEY no configurada"
        }), 500

    url = "https://api.odds-api.io/v3/odds"

    params = {
        "apiKey": API_KEY,
        "bookmakers": "Winamax"
    }

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        return jsonify(response.json())

    except requests.RequestException as e:
        return jsonify({
            "error": str(e)
        }), 500


@app.route("/debug")
def debug():
    if not API_KEY:
        return jsonify({
            "error": "ODDSAPI_KEY no configurada"
        }), 500

    url = "https://api.odds-api.io/v3/odds"

    params = {
        "apiKey": API_KEY,
        "bookmakers": "Winamax"
    }

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        diagnostic = []

        # Recorremos los partidos recibidos
        fixtures = data if isinstance(data, list) else [data]

        for fixture_index, fixture in enumerate(fixtures):

            bookmaker_odds = fixture.get("bookmakerOdds", {})
            winamax = bookmaker_odds.get("winamax.es", {})
            markets = winamax.get("markets", {})

            fixture_result = {
                "fixture_index": fixture_index,
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

    except requests.RequestException as e:
        return jsonify({
            "error": str(e)
        }), 500

    except Exception as e:
        return jsonify({
            "error": "Error procesando datos",
            "detail": str(e)
        }), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
