import os
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

# =========================================================
# CONFIGURACIÓN
# =========================================================

API_KEY = os.environ.get("ODDSPAPI_KEY", "").strip()

ODDS_URL = "https://api.oddspapi.io/v4/odds-by-tournaments"
MARKETS_URL = "https://api.oddspapi.io/v4/markets"


# =========================================================
# FUNCIÓN GENERAL PARA LLAMAR A ODDSPAPI
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
# OBTENER CUOTAS WINAMAX
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

        if isinstance(
            data.get("fixtures"),
            list
        ):
            return data["fixtures"]

        return [data]

    return []


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
            "debug": "/debug",
            "markets": "/markets",
            "simple": "/simple"
        }
    })


# =========================================================
# SCAN
# DEVUELVE LOS DATOS CRUDOS DE WINAMAX
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
# DEBUG
# ORDENA LOS MERCADOS SIN INTERPRETARLOS
# =========================================================

@app.route("/debug")
def debug():

    data, error = get_odds()

    if error:
        return jsonify({
            "error": error
        }), 500

    fixtures = normalize_fixtures(data)

    diagnostic = []

    for fixture_index, fixture in enumerate(fixtures):

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

        fixture_result = {

            "fixture_index":
                fixture_index,

            "fixtureId":
                fixture.get(
                    "fixtureId"
                ),

            "participant1Name":
                fixture.get(
                    "participant1Name"
                ),

            "participant2Name":
                fixture.get(
                    "participant2Name"
                ),

            "bookmakerFixtureId":
                winamax.get(
                    "bookmakerFixtureId"
                ),

            "markets": []
        }

        for market_id, market in markets.items():

            market_result = {

                "market_id":
                    market_id,

                "marketActive":
                    market.get(
                        "marketActive"
                    ),

                "outcomes": []
            }

            outcomes = market.get(
                "outcomes",
                {}
            )

            for outcome_id, outcome in outcomes.items():

                players = outcome.get(
                    "players",
                    {}
                )

                for player_id, player in players.items():

                    market_result[
                        "outcomes"
                    ].append({

                        "outcome_id":
                            outcome_id,

                        "player_id":
                            player_id,

                        "active":
                            player.get(
                                "active"
                            ),

                        "mainLine":
                            player.get(
                                "mainLine"
                            ),

                        "playerName":
                            player.get(
                                "playerName"
                            ),

                        "price":
                            player.get(
                                "price"
                            ),

                        "changedAt":
                            player.get(
                                "changedAt"
                            )
                    })

            fixture_result[
                "markets"
            ].append(
                market_result
            )

        diagnostic.append(
            fixture_result
        )

    return jsonify({
        "status": "ok",
        "fixtures": diagnostic
    })


# =========================================================
# CATÁLOGO OFICIAL DE MERCADOS ODDSPAPI
# ESTA ES LA RUTA QUE QUEREMOS PROBAR AHORA
# =========================================================

@app.route("/markets")
def markets_catalog():

    if not API_KEY:

        return jsonify({
            "error": "ODDSPAPI_KEY no configurada"
        }), 500

    params = {
        "language": "es",
        "apiKey": API_KEY
    }

    data, error = api_get(
        MARKETS_URL,
        params
    )

    if error:

        return jsonify({
            "error": error
        }), 500

    return jsonify(data)


# =========================================================
# SIMPLE
#
# DE MOMENTO NO TRADUCIMOS LOS IDS.
#
# PRIMERO VAMOS A IDENTIFICAR CORRECTAMENTE:
# 1X2
# AMBOS MARCAN
# OVER/UNDER 1.5
# OVER/UNDER 2.5
# OVER/UNDER 3.5
#
# UTILIZANDO /markets
# =========================================================

@app.route("/simple")
def simple():

    data, error = get_odds()

    if error:

        return jsonify({
            "error": error
        }), 500

    fixtures = normalize_fixtures(
        data
    )

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

            "partido":
                (
                    f"{fixture.get('participant1Name', 'Local')}"
                    " - "
                    f"{fixture.get('participant2Name', 'Visitante')}"
                ),

            "fixtureId":
                fixture.get(
                    "fixtureId"
                ),

            "numero_mercados":
                len(markets),

            "mercados":
                []
        }

        for market_id, market in markets.items():

            market_data = {

                "market_id":
                    market_id,

                "outcomes":
                    []
            }

            outcomes = market.get(
                "outcomes",
                {}
            )

            for outcome_id, outcome in outcomes.items():

                for player_id, player in outcome.get(
                    "players",
                    {}
                ).items():

                    if (
                        player.get("active")
                        and
                        player.get("price") is not None
                    ):

                        market_data[
                            "outcomes"
                        ].append({

                            "outcome_id":
                                outcome_id,

                            "price":
                                player.get(
                                    "price"
                                ),

                            "mainLine":
                                player.get(
                                    "mainLine"
                                )
                        })

            partido[
                "mercados"
            ].append(
                market_data
            )

        results.append(
            partido
        )

    return jsonify({
        "status": "ok",
        "bookmaker": "winamax.es",
        "partidos": results
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
