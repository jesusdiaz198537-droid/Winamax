import os
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

API_KEY = os.environ.get("ODDSPAPI_KEY", "").strip()

ODDS_URL = "https://api.oddspapi.io/v4/odds-by-tournaments"
MARKETS_URL = "https://api.oddspapi.io/v4/markets"


# =========================================================
# LLAMADA GENERAL A ODDSPAPI
# =========================================================

def api_get(url, params):
    try:
        response = requests.get(
            url,
            params=params,
            timeout=30
        )

        response.raise_for_status()

        return response.json(), None

    except requests.exceptions.RequestException as e:

        detalle = ""

        try:
            detalle = response.text[:1000]
        except Exception:
            pass

        return None, {
            "mensaje": str(e),
            "respuesta_api": detalle
        }

    except ValueError as e:

        return None, {
            "mensaje": "OddsPapi no devolvió JSON válido",
            "detalle": str(e)
        }


# =========================================================
# OBTENER CUOTAS DE WINAMAX
# =========================================================

def get_odds():

    if not API_KEY:
        return None, {
            "mensaje": "ODDSPAPI_KEY no configurada"
        }

    tournament_id = request.args.get(
        "tournamentId",
        "7"
    )

    params = {
        "tournamentIds": tournament_id,
        "bookmakers": "winamax.es",
        "language": "es",
        "verbosity": 3,
        "apiKey": API_KEY
    }

    return api_get(
        ODDS_URL,
        params
    )


# =========================================================
# NORMALIZAR PARTIDOS
# =========================================================

def normalize_fixtures(data):

    if isinstance(data, list):
        return data

    if isinstance(data, dict):

        if isinstance(data.get("fixtures"), list):
            return data["fixtures"]

        return [data]

    return []


# =========================================================
# OBTENER PRECIO
# =========================================================

def get_price(markets, market_id, outcome_id):

    market = markets.get(
        str(market_id),
        {}
    )

    if not market.get("marketActive", True):
        return None

    outcome = market.get(
        "outcomes",
        {}
    ).get(
        str(outcome_id),
        {}
    )

    players = outcome.get(
        "players",
        {}
    )

    for player in players.values():

        if (
            player.get("active")
            and
            player.get("price") is not None
        ):

            try:
                return float(
                    player.get("price")
                )
            except Exception:
                return player.get("price")

    return None


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():

    return jsonify({
        "status": "ok",
        "message": "Winamax Odds API funcionando",
        "rutas": {
            "scan": "/scan",
            "simple": "/simple",
            "debug": "/debug",
            "markets": "/markets"
        }
    })


# =========================================================
# SCAN - JSON ORIGINAL
# =========================================================

@app.route("/scan")
def scan():

    data, error = get_odds()

    if error:

        return jsonify({
            "error": error
        }), 500

    return jsonify(data)


# =========================================================
# CATÁLOGO DE MERCADOS
# =========================================================

@app.route("/markets")
def markets_catalog():

    if not API_KEY:

        return jsonify({
            "error": "ODDSPAPI_KEY no configurada"
        }), 500

    data, error = api_get(
        MARKETS_URL,
        {
            "language": "es",
            "apiKey": API_KEY
        }
    )

    if error:

        return jsonify({
            "error": error
        }), 500

    return jsonify(data)


# =========================================================
# SIMPLE
# MERCADOS PRINCIPALES DE WINAMAX
# =========================================================

@app.route("/simple")
def simple():

    data, error = get_odds()

    if error:

        return jsonify({
            "error": error
        }), 500

    fixtures = normalize_fixtures(data)

    results = []

    for fixture in fixtures:

        bookmaker_odds = fixture.get(
            "bookmakerOdds",
            {}
        )

        winamax = bookmaker_odds.get(
            "winamax.es",
            {}
        )

        markets = winamax.get(
            "markets",
            {}
        )

        partido = {

            "partido": (
                f"{fixture.get('participant1Name', 'Local')} - "
                f"{fixture.get('participant2Name', 'Visitante')}"
            ),

            "1X2": {

                "Local":
                    get_price(
                        markets,
                        101,
                        101
                    ),

                "Empate":
                    get_price(
                        markets,
                        101,
                        102
                    ),

                "Visitante":
                    get_price(
                        markets,
                        101,
                        103
                    )
            },

            "Ambos marcan": {

                "Si":
                    get_price(
                        markets,
                        104,
                        104
                    ),

                "No":
                    get_price(
                        markets,
                        104,
                        105
                    )
            },

            "Over Under 1.5": {

                "Over 1.5":
                    get_price(
                        markets,
                        108,
                        108
                    ),

                "Under 1.5":
                    get_price(
                        markets,
                        108,
                        109
                    )
            },

            "Over Under 2.5": {

                "Over 2.5":
                    get_price(
                        markets,
                        1010,
                        1010
                    ),

                "Under 2.5":
                    get_price(
                        markets,
                        1010,
                        1011
                    )
            },

            "Over Under 3.5": {

                "Over 3.5":
                    get_price(
                        markets,
                        1012,
                        1012
                    ),

                "Under 3.5":
                    get_price(
                        markets,
                        1012,
                        1013
                    )
            },

            "Over Under 4.5": {

                "Over 4.5":
                    get_price(
                        markets,
                        1014,
                        1014
                    ),

                "Under 4.5":
                    get_price(
                        markets,
                        1014,
                        1015
                    )
            }
        }

        results.append(partido)

    return jsonify({
        "status": "ok",
        "bookmaker": "winamax.es",
        "numero_partidos": len(results),
        "partidos": results
    })


# =========================================================
# DEBUG
# =========================================================

@app.route("/debug")
def debug():

    data, error = get_odds()

    if error:

        return jsonify({
            "error": error
        }), 500

    fixtures = normalize_fixtures(data)

    return jsonify({
        "status": "ok",
        "numero_partidos": len(fixtures),
        "fixtures": fixtures
    })


# =========================================================
# ARRANCAR SERVIDOR
# =========================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
