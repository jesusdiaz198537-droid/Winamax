import os
import time
import threading
import statistics
import requests

from datetime import datetime, timezone
from flask import Flask, jsonify, request


app = Flask(__name__)


# =========================================================
# CONFIGURACIÓN
# =========================================================

API_KEY = os.environ.get("ODDSPAPI_KEY", "").strip()

ODDS_URL = "https://api.oddspapi.io/v4/odds-by-tournaments"
ACCOUNT_URL = "https://api.oddspapi.io/v4/account"

TARGET_BOOKMAKER = "winamax.es"

REFERENCE_BOOKMAKERS = [
    "pinnacle",
    "singbet",
    "sbobet"
]

# OddsPapi documenta 1000 ms.
# Dejamos un pequeño margen.
ODDS_MIN_INTERVAL = 1.10

_last_request = 0.0
_request_lock = threading.Lock()


# =========================================================
# MERCADOS
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
# SEGURIDAD
# =========================================================

def sanitize(text):

    if text is None:
        return ""

    text = str(text)

    if API_KEY:
        text = text.replace(
            API_KEY,
            "***OCULTA***"
        )

    return text


# =========================================================
# RATE LIMIT
# =========================================================

def wait_request_slot():

    global _last_request

    with _request_lock:

        elapsed = (
            time.monotonic()
            - _last_request
        )

        wait_time = (
            ODDS_MIN_INTERVAL
            - elapsed
        )

        if wait_time > 0:
            time.sleep(wait_time)

        _last_request = (
            time.monotonic()
        )


# =========================================================
# RETRY 429
# =========================================================

def get_retry_seconds(response):

    try:

        data = response.json()

        retry_ms = (
            data
            .get("error", {})
            .get("retryMs")
        )

        if retry_ms is not None:
            return float(retry_ms) / 1000

    except Exception:
        pass

    return 1.1


# =========================================================
# API REQUEST
# =========================================================

def api_request(url, params, retries=3):

    for attempt in range(retries + 1):

        try:

            if url == ODDS_URL:
                wait_request_slot()

            response = requests.get(
                url,
                params=params,
                timeout=30
            )

            if response.status_code == 429:

                if attempt < retries:

                    wait = (
                        get_retry_seconds(response)
                        + 0.30
                    )

                    time.sleep(wait)

                    continue

            response.raise_for_status()

            return response.json(), None


        except requests.exceptions.RequestException as e:

            try:
                detail = response.text[:1000]
            except Exception:
                detail = ""

            return None, {
                "mensaje": sanitize(str(e)),
                "respuesta_api": sanitize(detail)
            }


        except ValueError as e:

            return None, {
                "mensaje": "JSON no válido",
                "detalle": sanitize(str(e))
            }

    return None, {
        "mensaje": "Reintentos agotados"
    }


# =========================================================
# OBTENER CUOTAS
# =========================================================

def get_bookmaker_odds(bookmaker):

    if not API_KEY:

        return None, {
            "mensaje":
                "ODDSPAPI_KEY no configurada"
        }

    tournament_id = request.args.get(
        "tournamentId",
        "7"
    )

    params = {

        "tournamentIds":
            tournament_id,

        "bookmaker":
            bookmaker,

        "language":
            "es",

        "verbosity":
            3,

        "oddsFormat":
            "decimal",

        "apiKey":
            API_KEY
    }

    return api_request(
        ODDS_URL,
        params
    )


# =========================================================
# FIXTURES
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


def index_fixtures(data):

    result = {}

    for fixture in normalize_fixtures(data):

        fixture_id = fixture.get(
            "fixtureId"
        )

        if fixture_id:
            result[fixture_id] = fixture

    return result


# =========================================================
# FECHAS
# =========================================================

def parse_timestamp(value):

    if not value:
        return None

    try:

        return datetime.fromisoformat(
            value.replace(
                "Z",
                "+00:00"
            )
        )

    except Exception:
        return None


def age_minutes(value):

    dt = parse_timestamp(value)

    if not dt:
        return None

    now = datetime.now(
        timezone.utc
    )

    seconds = (
        now - dt
    ).total_seconds()

    return round(
        seconds / 60,
        1
    )


# =========================================================
# BOOKMAKER VÁLIDO
# =========================================================

def get_bookmaker_markets(
    fixture,
    bookmaker
):

    bookmaker_data = (
        fixture
        .get("bookmakerOdds", {})
        .get(bookmaker)
    )

    if not bookmaker_data:
        return None

    if (
        bookmaker_data.get(
            "bookmakerIsActive"
        ) is False
    ):
        return None

    if (
        bookmaker_data.get(
            "suspended"
        ) is True
    ):
        return None

    return bookmaker_data.get(
        "markets",
        {}
    )


# =========================================================
# OBTENER SELECCIÓN
# =========================================================

def get_selection(
    markets,
    market_id,
    outcome_id
):

    market = markets.get(
        str(market_id),
        {}
    )

    if not market:
        return None

    if (
        market.get("marketActive")
        is False
    ):
        return None

    outcome = (
        market
        .get("outcomes", {})
        .get(
            str(outcome_id),
            {}
        )
    )

    players = outcome.get(
        "players",
        {}
    )

    for player in players.values():

        # Permitimos null.
        # Solo rechazamos explícitamente false.
        if (
            player.get("active")
            is False
        ):
            continue

        price = player.get(
            "price"
        )

        if price is None:
            continue

        try:
            price = float(price)
        except Exception:
            continue

        if price <= 1:
            continue

        return {

            "price":
                price,

            "changedAt":
                player.get(
                    "changedAt"
                ),

            "bookmakerChangedAt":
                player.get(
                    "bookmakerChangedAt"
                ),

            "mainLine":
                player.get(
                    "mainLine"
                )
        }

    return None


# =========================================================
# MERCADO COMPLETO
# =========================================================

def extract_market(
    markets,
    config
):

    result = {}

    for (
        name,
        outcome_id
    ) in config["outcomes"].items():

        selection = get_selection(
            markets,
            config["market_id"],
            outcome_id
        )

        if not selection:
            return None

        result[name] = selection

    return result


# =========================================================
# QUITAR MARGEN
# =========================================================

def remove_vig(market):

    implied = {}
    total = 0.0

    for selection, data in market.items():

        p = (
            1.0
            /
            data["price"]
        )

        implied[selection] = p

        total += p

    if total <= 0:
        return None

    fair = {}

    for selection, probability in implied.items():

        p_fair = (
            probability
            /
            total
        )

        fair[selection] = {

            "probability":
                p_fair,

            "fair_odds":
                1.0 / p_fair
        }

    return fair


# =========================================================
# SCORE
# =========================================================

def calculate_score(
    edge,
    refs,
    confirmations,
    dispersion,
    win_price
):

    # Cuotas > 6:
    # requieren revisión manual
    high_odds = (
        win_price > 6
    )


    if (
        edge >= 8
        and refs >= 3
        and confirmations == refs
        and dispersion <= 8
        and not high_odds
    ):

        return "A+", "APTO"


    if (
        edge >= 6
        and refs >= 2
        and confirmations == refs
        and dispersion <= 10
        and not high_odds
    ):

        return "A", "APTO"


    if (
        edge >= 4
        and refs >= 2
        and confirmations >= 2
        and dispersion <= 15
    ):

        return "B", "REVISAR"


    return "PASS", "PASS"


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():

    return jsonify({

        "status":
            "ok",

        "message":
            "Winamax Value Scanner V3",

        "rutas": {
            "value_v3":
                "/value-v3",

            "simple":
                "/simple",

            "quota":
                "/quota"
        }
    })


# =========================================================
# QUOTA
# =========================================================

@app.route("/quota")
def quota():

    data, error = api_request(
        ACCOUNT_URL,
        {
            "apiKey":
                API_KEY
        }
    )

    if error:

        return jsonify({
            "error": error
        }), 500

    if isinstance(data, dict):

        data.pop(
            "api_key",
            None
        )

        data.pop(
            "apiKey",
            None
        )

    return jsonify(data)


# =========================================================
# SIMPLE
# =========================================================

@app.route("/simple")
def simple():

    raw, error = get_bookmaker_odds(
        TARGET_BOOKMAKER
    )

    if error:

        return jsonify({
            "error": error
        }), 500


    fixtures = normalize_fixtures(
        raw
    )

    results = []


    for fixture in fixtures:

        markets = get_bookmaker_markets(
            fixture,
            TARGET_BOOKMAKER
        )

        if markets is None:
            continue


        partido = {

            "fixtureId":
                fixture.get(
                    "fixtureId"
                ),

            "startTime":
                fixture.get(
                    "startTime"
                ),

            "partido": (
                f"{fixture.get('participant1Name', 'Local')} - "
                f"{fixture.get('participant2Name', 'Visitante')}"
            )
        }


        for market_name, config in MARKETS.items():

            extracted = extract_market(
                markets,
                config
            )

            if not extracted:

                partido[
                    market_name
                ] = None

            else:

                partido[
                    market_name
                ] = {

                    selection:
                        data["price"]

                    for (
                        selection,
                        data
                    ) in extracted.items()
                }


        results.append(partido)


    return jsonify({

        "status":
            "ok",

        "numero_partidos":
            len(results),

        "partidos":
            results
    })


# =========================================================
# VALUE V3
# =========================================================

@app.route("/value-v3")
def value_v3():

    # =====================================================
    # WINAMAX
    # =====================================================

    win_raw, error = get_bookmaker_odds(
        TARGET_BOOKMAKER
    )

    if error:

        return jsonify({
            "error":
                "Error Winamax",

            "detalle":
                error
        }), 500


    win_index = index_fixtures(
        win_raw
    )


    # =====================================================
    # REFERENCIAS
    # =====================================================

    reference_indexes = {}

    reference_errors = {}


    for bookmaker in REFERENCE_BOOKMAKERS:

        raw, error = get_bookmaker_odds(
            bookmaker
        )

        if error:

            reference_errors[
                bookmaker
            ] = error

        else:

            reference_indexes[
                bookmaker
            ] = index_fixtures(
                raw
            )


    if len(reference_indexes) < 2:

        return jsonify({

            "status":
                "error",

            "mensaje":
                "Menos de 2 referencias disponibles",

            "errores":
                reference_errors

        }), 500


    candidatos = []

    mercados_analizados = 0


    # =====================================================
    # PARTIDOS
    # =====================================================

    for (
        fixture_id,
        win_fixture
    ) in win_index.items():


        partido_nombre = (
            f"{win_fixture.get('participant1Name', 'Local')} - "
            f"{win_fixture.get('participant2Name', 'Visitante')}"
        )


        win_markets = get_bookmaker_markets(
            win_fixture,
            TARGET_BOOKMAKER
        )


        if not win_markets:
            continue


        # =================================================
        # MERCADOS
        # =================================================

        for market_name, config in MARKETS.items():


            win_market = extract_market(
                win_markets,
                config
            )


            if not win_market:
                continue


            ref_raw = {}
            ref_fair = {}


            for (
                bookmaker,
                fixtures
            ) in reference_indexes.items():


                fixture = fixtures.get(
                    fixture_id
                )

                if not fixture:
                    continue


                markets = get_bookmaker_markets(
                    fixture,
                    bookmaker
                )

                if not markets:
                    continue


                raw_market = extract_market(
                    markets,
                    config
                )

                if not raw_market:
                    continue


                fair_market = remove_vig(
                    raw_market
                )

                if not fair_market:
                    continue


                ref_raw[
                    bookmaker
                ] = raw_market

                ref_fair[
                    bookmaker
                ] = fair_market


            if len(ref_fair) < 2:
                continue


            mercados_analizados += 1


            # =================================================
            # SELECCIONES
            # =================================================

            for (
                selection,
                win_selection
            ) in win_market.items():


                win_price = (
                    win_selection["price"]
                )


                probabilities = []

                references = {}

                confirmations = 0


                for (
                    bookmaker,
                    fair_market
                ) in ref_fair.items():


                    if selection not in fair_market:
                        continue


                    probability = (
                        fair_market[
                            selection
                        ][
                            "probability"
                        ]
                    )


                    fair_odds = (
                        fair_market[
                            selection
                        ][
                            "fair_odds"
                        ]
                    )


                    raw_selection = (
                        ref_raw[
                            bookmaker
                        ][
                            selection
                        ]
                    )


                    probabilities.append(
                        probability
                    )


                    if win_price > fair_odds:

                        confirmations += 1


                    references[
                        bookmaker
                    ] = {

                        "cuota":
                            round(
                                raw_selection[
                                    "price"
                                ],
                                3
                            ),

                        "cuota_justa":
                            round(
                                fair_odds,
                                3
                            ),

                        "probabilidad_justa_pct":
                            round(
                                probability
                                * 100,
                                2
                            )
                    }


                refs_count = len(
                    probabilities
                )


                if refs_count < 2:
                    continue


                # -----------------------------------------
                # CONSENSO
                # -----------------------------------------

                consensus_probability = (
                    statistics.median(
                        probabilities
                    )
                )


                consensus_odds = (
                    1.0
                    /
                    consensus_probability
                )


                # -----------------------------------------
                # DISPERSIÓN
                # -----------------------------------------

                dispersion = (

                    (
                        max(probabilities)
                        -
                        min(probabilities)
                    )

                    /

                    consensus_probability

                ) * 100


                # -----------------------------------------
                # EDGE
                # -----------------------------------------

                edge = (

                    (
                        win_price
                        *
                        consensus_probability
                    )

                    - 1

                ) * 100


                if edge < 3:
                    continue


                # -----------------------------------------
                # SCORE
                # -----------------------------------------

                score, decision = (
                    calculate_score(

                        edge,

                        refs_count,

                        confirmations,

                        dispersion,

                        win_price
                    )
                )


                # -----------------------------------------
                # INFO DE CAMBIOS
                # NO SE USA PARA DESCARTAR
                # -----------------------------------------

                win_change_age = age_minutes(
                    win_selection.get(
                        "changedAt"
                    )
                )


                reference_ages = {

                    bookmaker:
                        age_minutes(
                            ref_raw[
                                bookmaker
                            ][
                                selection
                            ].get(
                                "changedAt"
                            )
                        )

                    for bookmaker
                    in references.keys()
                }


                # -----------------------------------------
                # ALERTAS
                # -----------------------------------------

                alertas = []


                if dispersion > 10:

                    alertas.append(
                        "Dispersion alta"
                    )


                if win_price > 6:

                    alertas.append(
                        "Cuota superior a 6"
                    )


                if confirmations < refs_count:

                    alertas.append(
                        "No todas las referencias confirman"
                    )


                # Detectamos movimiento MUY reciente
                # de las referencias.
                refs_recent = sum(

                    1
                    for age
                    in reference_ages.values()

                    if (
                        age is not None
                        and
                        0 <= age <= 15
                    )
                )


                if (
                    win_change_age is not None
                    and win_change_age > 30
                    and refs_recent >= 2
                ):

                    alertas.append(
                        "Referencias se movieron recientemente: confirmar que Winamax mantiene la cuota"
                    )


                candidatos.append({

                    "fixtureId":
                        fixture_id,

                    "partido":
                        partido_nombre,

                    "mercado":
                        market_name,

                    "seleccion":
                        selection,

                    "cuota_winamax":
                        round(
                            win_price,
                            3
                        ),

                    "probabilidad_consenso_pct":
                        round(
                            consensus_probability
                            * 100,
                            2
                        ),

                    "cuota_justa_consenso":
                        round(
                            consensus_odds,
                            3
                        ),

                    "edge_pct":
                        round(
                            edge,
                            2
                        ),

                    "referencias_disponibles":
                        refs_count,

                    "referencias_confirmando":
                        confirmations,

                    "dispersion_pct":
                        round(
                            dispersion,
                            2
                        ),

                    "value_score":
                        score,

                    "decision":
                        decision,

                    "ultima_modificacion_winamax_minutos":
                        win_change_age,

                    "alertas":
                        alertas,

                    "referencias":
                        references
                })


    # =====================================================
    # ORDENAR
    # =====================================================

    score_rank = {
        "A+": 3,
        "A": 2,
        "B": 1,
        "PASS": 0
    }


    candidatos.sort(

        key=lambda x: (

            score_rank.get(
                x["value_score"],
                0
            ),

            x["edge_pct"]

        ),

        reverse=True
    )


    # =====================================================
    # APTO TODOS
    # =====================================================

    aptos_todos = [

        item
        for item in candidatos

        if item[
            "decision"
        ] == "APTO"
    ]


    # =====================================================
    # SOLO EL MEJOR APTO POR PARTIDO
    # =====================================================

    mejores_por_partido = {}


    for item in aptos_todos:

        partido = item[
            "partido"
        ]

        actual = mejores_por_partido.get(
            partido
        )

        if actual is None:

            mejores_por_partido[
                partido
            ] = item

            continue


        actual_rank = (
            score_rank.get(
                actual["value_score"],
                0
            )
        )

        nuevo_rank = (
            score_rank.get(
                item["value_score"],
                0
            )
        )


        if (
            nuevo_rank > actual_rank
            or
            (
                nuevo_rank == actual_rank
                and
                item["edge_pct"]
                >
                actual["edge_pct"]
            )
        ):

            mejores_por_partido[
                partido
            ] = item


    aptos_finales = list(
        mejores_por_partido.values()
    )


    aptos_finales.sort(
        key=lambda x: (
            score_rank.get(
                x["value_score"],
                0
            ),
            x["edge_pct"]
        ),
        reverse=True
    )


    revisar = [

        item
        for item in candidatos

        if item[
            "decision"
        ] == "REVISAR"
    ]


    # =====================================================
    # RESULTADO
    # =====================================================

    return jsonify({

        "status":
            "ok",

        "version":
            "V3",

        "metodo":
            "Winamax vs consenso Pinnacle/SingBet/SBOBET sin margen",

        "referencias_funcionando":
            list(
                reference_indexes.keys()
            ),

        "errores_referencias":
            reference_errors,

        "intervalo_api_segundos":
            ODDS_MIN_INTERVAL,

        "mercados_analizados":
            mercados_analizados,

        "numero_aptos_finales":
            len(
                aptos_finales
            ),

        "aptos_finales":
            aptos_finales,

        "numero_aptos_sin_deduplicar":
            len(
                aptos_todos
            ),

        "numero_revisar":
            len(
                revisar
            ),

        "revisar":
            revisar
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
