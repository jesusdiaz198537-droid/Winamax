import os
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

API_KEY = os.environ.get("ODDSPAPI_KEY", "").strip()

ODDS_URL = "https://api.oddspapi.io/v4/odds-by-tournaments"


# =========================================================
# MERCADOS QUE VAMOS A ANALIZAR
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
# LLAMADA A ODDSPAPI
# =========================================================

def api_get(params):

    try:

        response = requests.get(
            ODDS_URL,
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
# OBTENER UNA CASA
# IMPORTANTE: UNA CASA POR PETICIÓN
# =========================================================

def get_bookmaker_odds(bookmaker):

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

        # Singular: una casa por petición
        "bookmaker": bookmaker,

        "language": "es",
        "verbosity": 3,
        "oddsFormat": "decimal",
        "apiKey": API_KEY
    }

    return api_get(params)


# =========================================================
# NORMALIZAR FIXTURES
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
# EXTRAER PRECIO
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

                price = float(
                    player.get("price")
                )

                if price > 1:
                    return price

            except Exception:
                pass

    return None


# =========================================================
# EXTRAER MERCADO COMPLETO
# =========================================================

def extract_market(markets, config):

    prices = {}

    for name, outcome_id in config["outcomes"].items():

        price = get_price(
            markets,
            config["market_id"],
            outcome_id
        )

        if price is None:
            return None

        prices[name] = price

    return prices


# =========================================================
# QUITAR MARGEN
# =========================================================

def remove_vig(prices):

    if not prices:
        return None

    probabilities = {}

    total = 0.0

    for selection, price in prices.items():

        if not price or price <= 1:
            return None

        p = 1.0 / price

        probabilities[selection] = p

        total += p

    if total <= 0:
        return None

    result = {}

    for selection, probability in probabilities.items():

        fair_probability = (
            probability / total
        )

        fair_odds = (
            1.0 / fair_probability
        )

        result[selection] = {

            "probability":
                fair_probability,

            "fair_odds":
                fair_odds
        }

    return result


# =========================================================
# CLASIFICACIÓN PRELIMINAR
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

        "message":
            "Winamax Value Scanner",

        "rutas": {
            "simple": "/simple",
            "value": "/value",
            "winamax_raw": "/scan"
        }
    })


# =========================================================
# SCAN WINAMAX RAW
# =========================================================

@app.route("/scan")
def scan():

    data, error = get_bookmaker_odds(
        "winamax.es"
    )

    if error:

        return jsonify({
            "error": error
        }), 500

    return jsonify(data)


# =========================================================
# SIMPLE WINAMAX
# =========================================================

@app.route("/simple")
def simple():

    data, error = get_bookmaker_odds(
        "winamax.es"
    )

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

            "fixtureId":
                fixture.get("fixtureId"),

            "partido": (
                f"{fixture.get('participant1Name', 'Local')} - "
                f"{fixture.get('participant2Name', 'Visitante')}"
            )
        }

        for market_name, config in MARKETS.items():

            partido[
                market_name
            ] = extract_market(
                markets,
                config
            )

        results.append(partido)

    return jsonify({

        "status": "ok",

        "bookmaker":
            "winamax.es",

        "numero_partidos":
            len(results),

        "partidos":
            results
    })


# =========================================================
# VALUE
# WINAMAX VS PINNACLE
# =========================================================

@app.route("/value")
def value():

    # ---------------------------------
    # PETICIÓN 1: WINAMAX
    # ---------------------------------

    winamax_raw, error_winamax = (
        get_bookmaker_odds(
            "winamax.es"
        )
    )

    if error_winamax:

        return jsonify({
            "error": "Error consultando Winamax",
            "detalle": error_winamax
        }), 500


    # ---------------------------------
    # PETICIÓN 2: PINNACLE
    # ---------------------------------

    pinnacle_raw, error_pinnacle = (
        get_bookmaker_odds(
            "pinnacle"
        )
    )

    if error_pinnacle:

        return jsonify({
            "error": "Error consultando Pinnacle",
            "detalle": error_pinnacle
        }), 500


    winamax_fixtures = normalize_fixtures(
        winamax_raw
    )

    pinnacle_fixtures = normalize_fixtures(
        pinnacle_raw
    )


    # ---------------------------------
    # INDEXAMOS PINNACLE POR FIXTURE ID
    # ---------------------------------

    pinnacle_index = {}

    for fixture in pinnacle_fixtures:

        fixture_id = fixture.get(
            "fixtureId"
        )

        if fixture_id:

            pinnacle_index[
                fixture_id
            ] = fixture


    oportunidades = []

    partidos_emparejados = 0
    mercados_comparados = 0

    sin_pinnacle = []


    # ---------------------------------
    # RECORRER WINAMAX
    # ---------------------------------

    for win_fixture in winamax_fixtures:

        fixture_id = win_fixture.get(
            "fixtureId"
        )

        match_name = (
            f"{win_fixture.get('participant1Name', 'Local')} - "
            f"{win_fixture.get('participant2Name', 'Visitante')}"
        )


        # Buscar mismo partido en Pinnacle

        pin_fixture = pinnacle_index.get(
            fixture_id
        )

        if not pin_fixture:

            sin_pinnacle.append(
                match_name
            )

            continue


        partidos_emparejados += 1


        # Datos Winamax

        win_bookmakers = win_fixture.get(
            "bookmakerOdds",
            {}
        )

        win_data = win_bookmakers.get(
            "winamax.es",
            {}
        )


        # Datos Pinnacle

        pin_bookmakers = pin_fixture.get(
            "bookmakerOdds",
            {}
        )

        pin_data = pin_bookmakers.get(
            "pinnacle",
            {}
        )


        if not win_data or not pin_data:
            continue


        win_markets = win_data.get(
            "markets",
            {}
        )

        pin_markets = pin_data.get(
            "markets",
            {}
        )


        # ---------------------------------
        # COMPARAR MERCADOS
        # ---------------------------------

        for market_name, config in MARKETS.items():

            win_prices = extract_market(
                win_markets,
                config
            )

            pin_prices = extract_market(
                pin_markets,
                config
            )


            if not win_prices:
                continue

            if not pin_prices:
                continue


            fair = remove_vig(
                pin_prices
            )

            if not fair:
                continue


            mercados_comparados += 1


            # ---------------------------------
            # COMPARAR CADA SELECCIÓN
            # ---------------------------------

            for selection, win_price in win_prices.items():

                if selection not in fair:
                    continue


                probability = (
                    fair[
                        selection
                    ][
                        "probability"
                    ]
                )


                fair_odds = (
                    fair[
                        selection
                    ][
                        "fair_odds"
                    ]
                )


                pinnacle_price = (
                    pin_prices[
                        selection
                    ]
                )


                # EV / Edge contra probabilidad justa

                edge = (
                    (
                        win_price
                        * probability
                    )
                    - 1
                ) * 100


                score = value_score(
                    edge
                )


                # Solo mostramos >= 3%

                if edge < 3:
                    continue


                oportunidades.append({

                    "partido":
                        match_name,

                    "mercado":
                        market_name,

                    "seleccion":
                        selection,

                    "cuota_winamax":
                        round(
                            win_price,
                            3
                        ),

                    "cuota_pinnacle":
                        round(
                            pinnacle_price,
                            3
                        ),

                    "probabilidad_referencia_pct":
                        round(
                            probability * 100,
                            2
                        ),

                    "cuota_justa_pinnacle":
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

                    "decision_preliminar":
                        (
                            "CANDIDATO"
                            if edge >= 5
                            else
                            "REVISAR"
                        )
                })


    # Ordenar de mayor edge a menor

    oportunidades.sort(
        key=lambda x: x["edge_pct"],
        reverse=True
    )


    return jsonify({

        "status":
            "ok",

        "metodo":
            "Winamax vs Pinnacle sin margen",

        "peticiones_api_usadas":
            2,

        "partidos_winamax":
            len(winamax_fixtures),

        "partidos_pinnacle":
            len(pinnacle_fixtures),

        "partidos_emparejados":
            partidos_emparejados,

        "mercados_comparados":
            mercados_comparados,

        "numero_oportunidades":
            len(oportunidades),

        "filtro_edge_minimo_pct":
            3,

        "sin_datos_pinnacle":
            sin_pinnacle,

        "oportunidades":
            oportunidades
    })


# =========================================================
# ARRANCAR
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
