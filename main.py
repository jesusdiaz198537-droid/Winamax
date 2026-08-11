import os
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

API_KEY = os.environ.get("ODDSPAPI_KEY", "").strip()

ODDS_URL = "https://api.oddspapi.io/v4/odds-by-tournaments"
MARKETS_URL = "https://api.oddspapi.io/v4/markets"


# =========================================================
# CONFIGURACIÓN DE MERCADOS
# =========================================================

MARKETS = {
    "1X2": {
        "market_id": "101",
        "outcomes": {
            "Local": "101",
            "Empate": "102",
            "Visitante": "103"
        }
    },

    "Ambos marcan": {
        "market_id": "104",
        "outcomes": {
            "Si": "104",
            "No": "105"
        }
    },

    "Over Under 1.5": {
        "market_id": "108",
        "outcomes": {
            "Over 1.5": "108",
            "Under 1.5": "109"
        }
    },

    "Over Under 2.5": {
        "market_id": "1010",
        "outcomes": {
            "Over 2.5": "1010",
            "Under 2.5": "1011"
        }
    },

    "Over Under 3.5": {
        "market_id": "1012",
        "outcomes": {
            "Over 3.5": "1012",
            "Under 3.5": "1013"
        }
    },

    "Over Under 4.5": {
        "market_id": "1014",
        "outcomes": {
            "Over 4.5": "1014",
            "Under 4.5": "1015"
        }
    }
}


# =========================================================
# LLAMADA API
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

        try:
            detalle = response.text[:1000]
        except Exception:
            detalle = ""

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
# OBTENER CUOTAS
# =========================================================

def get_odds(bookmakers="winamax.es"):

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
        "bookmakers": bookmakers,
        "language": "es",
        "verbosity": 3,
        "oddsFormat": "decimal",
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
# OBTENER PRECIO DE UNA SELECCIÓN
# =========================================================

def get_price(markets, market_id, outcome_id):

    market = markets.get(
        str(market_id),
        {}
    )

    if not market:
        return None

    if not market.get(
        "marketActive",
        True
    ):
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
                return None

    return None


# =========================================================
# EXTRAER UN MERCADO COMPLETO
# =========================================================

def extract_market(markets, config):

    prices = {}

    market_id = config["market_id"]

    for name, outcome_id in config["outcomes"].items():

        price = get_price(
            markets,
            market_id,
            outcome_id
        )

        if price is None:
            return None

        prices[name] = price

    return prices


# =========================================================
# QUITAR MARGEN DE PINNACLE
# =========================================================

def remove_vig(prices):

    if not prices:
        return None

    implied = {}

    total = 0

    for selection, price in prices.items():

        if not price or price <= 1:
            return None

        probability = 1 / price

        implied[selection] = probability

        total += probability

    if total <= 0:
        return None

    fair = {}

    for selection, probability in implied.items():

        fair_probability = probability / total

        fair[selection] = {
            "probabilidad_justa":
                fair_probability,

            "cuota_justa":
                1 / fair_probability
        }

    return fair


# =========================================================
# VALUE SCORE
# =========================================================

def value_score(edge):

    if edge >= 10:
        return "A+"

    if edge >= 8:
        return "A"

    if edge >= 5:
        return "B"

    if edge >= 3:
        return "C"

    return "PASS"


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():

    return jsonify({
        "status": "ok",
        "message": "Winamax Value Scanner",
        "rutas": {
            "simple": "/simple",
            "value": "/value",
            "scan": "/scan",
            "markets": "/markets"
        }
    })


# =========================================================
# SCAN RAW
# =========================================================

@app.route("/scan")
def scan():

    data, error = get_odds(
        "winamax.es"
    )

    if error:

        return jsonify({
            "error": error
        }), 500

    return jsonify(data)


# =========================================================
# MARKETS
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
# =========================================================

@app.route("/simple")
def simple():

    data, error = get_odds(
        "winamax.es"
    )

    if error:

        return jsonify({
            "error": error
        }), 500

    fixtures = normalize_fixtures(
        data
    )

    results = []

    for fixture in fixtures:

        winamax = fixture.get(
            "bookmakerOdds",
            {}
        ).get(
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
            )
        }

        for market_name, config in MARKETS.items():

            partido[market_name] = extract_market(
                markets,
                config
            )

        results.append(
            partido
        )

    return jsonify({
        "status": "ok",
        "bookmaker": "winamax.es",
        "numero_partidos": len(results),
        "partidos": results
    })


# =========================================================
# VALUE SCANNER
# =========================================================

@app.route("/value")
def value():

    data, error = get_odds(
        "winamax.es,pinnacle"
    )

    if error:

        return jsonify({
            "error": error
        }), 500

    fixtures = normalize_fixtures(
        data
    )

    oportunidades = []

    partidos_analizados = 0

    for fixture in fixtures:

        bookmaker_odds = fixture.get(
            "bookmakerOdds",
            {}
        )

        winamax_data = bookmaker_odds.get(
            "winamax.es",
            {}
        )

        pinnacle_data = bookmaker_odds.get(
            "pinnacle",
            {}
        )

        if not winamax_data or not pinnacle_data:
            continue

        winamax_markets = winamax_data.get(
            "markets",
            {}
        )

        pinnacle_markets = pinnacle_data.get(
            "markets",
            {}
        )

        partidos_analizados += 1

        partido_name = (
            f"{fixture.get('participant1Name', 'Local')} - "
            f"{fixture.get('participant2Name', 'Visitante')}"
        )

        for market_name, config in MARKETS.items():

            winamax_prices = extract_market(
                winamax_markets,
                config
            )

            pinnacle_prices = extract_market(
                pinnacle_markets,
                config
            )

            if (
                not winamax_prices
                or
                not pinnacle_prices
            ):
                continue

            fair = remove_vig(
                pinnacle_prices
            )

            if not fair:
                continue

            for selection, winamax_price in winamax_prices.items():

                if selection not in fair:
                    continue

                probability = fair[
                    selection
                ][
                    "probabilidad_justa"
                ]

                fair_odds = fair[
                    selection
                ][
                    "cuota_justa"
                ]

                edge = (
                    (
                        winamax_price
                        * probability
                    )
                    - 1
                ) * 100

                score = value_score(
                    edge
                )

                if edge >= 3:

                    oportunidades.append({

                        "partido":
                            partido_name,

                        "mercado":
                            market_name,

                        "seleccion":
                            selection,

                        "cuota_winamax":
                            round(
                                winamax_price,
                                3
                            ),

                        "cuota_pinnacle":
                            round(
                                pinnacle_prices[
                                    selection
                                ],
                                3
                            ),

                        "probabilidad_justa_pct":
                            round(
                                probability * 100,
                                2
                            ),

                        "cuota_justa":
                            round(
                                fair_odds,
                                3
                            ),

                        "edge_pct":
                            round(
                                edge,
                                2
                            ),

                        "value_score":
                            score,

                        "decision":
                            (
                                "APTO"
                                if edge >= 5
                                else
                                "REVISAR"
                            )
                    })

    oportunidades.sort(
        key=lambda x: x["edge_pct"],
        reverse=True
    )

    return jsonify({

        "status":
            "ok",

        "referencia":
            "Pinnacle sin margen",

        "partidos_analizados":
            partidos_analizados,

        "numero_oportunidades":
            len(oportunidades),

        "filtro_minimo_edge_pct":
            3,

        "oportunidades":
            oportunidades
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
