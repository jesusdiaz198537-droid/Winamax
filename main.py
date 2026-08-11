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


def normalize_fixtures(data):
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        if isinstance(data.get("fixtures"), list):
            return data["fixtures"]
        return [data]

    return []


def get_price(markets, market_id, outcome_id):
    market = markets.get(str(market_id), {})
    outcome = market.get("outcomes", {}).get(str(outcome_id), {})
    players = outcome.get("players", {})

    for player in players.values():
        if player.get("active") and player.get("price") is not None:
            return player.get("price")

    return None


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

    fixtures = normalize_fixtures(data)
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

            for outcome_id, outcome in market.get("outcomes", {}).items():
                for player_id, player in outcome.get("players", {}).items():
                    market_result["outcomes"].append({
                        "outcome_id": outcome_id,
                        "player_id": player_id,
                        "active": player.get("active"),
                        "mainLine": player.get("mainLine"),
                        "price": player.get("price")
                    })

            fixture_result["markets"].append(market_result)

        diagnostic.append(fixture_result)

    return jsonify({
        "status": "ok",
        "fixtures": diagnostic
    })


@app.route("/simple")
def simple():
    data, error = get_odds()

    if error:
        return jsonify({"error": error}), 500

    fixtures = normalize_fixtures(data)
    results = []

    for fixture in fixtures:
        bookmaker_odds = fixture.get("bookmakerOdds", {})
        winamax = bookmaker_odds.get("winamax.es", {})
        markets = winamax.get("markets", {})

        partido = {
            "partido": (
                f"{fixture.get('participant1Name', 'Local')} - "
                f"{fixture.get('participant2Name', 'Visitante')}"
            ),

            "1X2": {
                "Local": get_price(markets, "101", "101"),
                "Empate": get_price(markets, "101", "102"),
                "Visitante": get_price(markets, "101", "103")
            },

            "Ambos marcan": {
                "Si": get_price(markets, "104", "104"),
                "No": get_price(markets, "104", "105")
            },

            "Over Under 1.5": {
                "Over 1.5": get_price(markets, "1012", "1012"),
                "Under 1.5": get_price(markets, "1012", "1013")
            },

            "Over Under 2.5": {
                "Over 2.5": get_price(markets, "1010", "1010"),
                "Under 2.5": get_price(markets, "1010", "1011")
            },

            "Over Under 3.5": {
                "Over 3.5": get_price(markets, "1014", "1014"),
                "Under 3.5": get_price(markets, "1014", "1015")
            }
        }

        results.append(partido)

    return jsonify({
        "status": "ok",
        "bookmaker": "winamax.es",
        "partidos": results
    })


if __name__ == "__main__":
    @app.route("/markets")
def markets_catalog():
    if not API_KEY:
        return jsonify({"error": "ODDSPAPI_KEY no configurada"}), 500

    try:
        response = requests.get(
            "https://api.oddspapi.io/v4/markets",
            params={
                "language": "es",
                "apiKey": API_KEY
            },
            timeout=30
        )

        response.raise_for_status()
        data = response.json()

        seleccionados = []

        for market in data:
            if market.get("sportId") != 10:
                continue

            # 1X2 y Ambos Marcan
            if market.get("marketId") in [101, 104]:
                seleccionados.append(market)
                continue

            # Totales de goles FT: 1.5, 2.5 y 3.5
            if (
                market.get("period") == "fulltime"
                and market.get("marketType") == "totals"
                and market.get("playerProp") is False
                and market.get("handicap") in [1.5, 2.5, 3.5]
            ):
                seleccionados.append(market)

        return jsonify({
            "status": "ok",
            "markets": seleccionados
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500
