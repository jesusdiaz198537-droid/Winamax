import os
import time
import threading
import statistics
import requests

from datetime import datetime
from flask import Flask, jsonify, request


app = Flask(__name__)


# =========================================================
# CONFIGURACIÓN
# =========================================================

API_KEY = os.environ.get(
    "ODDSPAPI_KEY",
    ""
).strip()

BASE_URL = "https://api.oddspapi.io/v4"

ODDS_URL = f"{BASE_URL}/odds-by-tournaments"
ACCOUNT_URL = f"{BASE_URL}/account"

TARGET_BOOKMAKER = "winamax.es"

REFERENCE_BOOKMAKERS = [
    "pinnacle",
    "singbet",
    "sbobet"
]


# Esperaremos un poco entre llamadas a odds-by-tournaments
ODDS_MIN_INTERVAL = 0.90

_last_odds_request = 0.0
_odds_lock = threading.Lock()


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
# OCULTAR API KEY EN ERRORES
# =========================================================

def sanitize_text(text):

    if not text:
        return ""

    text = str(text)

    if API_KEY:
        text = text.replace(
            API_KEY,
            "***OCULTA***"
        )

    return text


# =========================================================
# CONTROL DE VELOCIDAD
# =========================================================

def wait_for_odds_slot():

    global _last_odds_request

    with _odds_lock:

        now = time.monotonic()

        elapsed = (
            now
            - _last_odds_request
        )

        wait_time = (
            ODDS_MIN_INTERVAL
            - elapsed
        )

        if wait_time > 0:
            time.sleep(
                wait_time
            )

        _last_odds_request = (
            time.monotonic()
        )


# =========================================================
# LEER RETRY DEL ERROR 429
# =========================================================

def get_retry_seconds(response):

    try:

        data = response.json()

        error = data.get(
            "error",
            {}
        )

        retry_ms = error.get(
            "retryMs"
        )

        if retry_ms is not None:

            return (
                float(retry_ms)
                / 1000.0
            )

    except Exception:
        pass

    return 1.0


# =========================================================
# LLAMADA API
# CON REINTENTO AUTOMÁTICO PARA 429
# =========================================================

def api_request(
    url,
    params,
    retries=2
):

    for attempt in range(
        retries + 1
    ):

        try:

            if url == ODDS_URL:
                wait_for_odds_slot()

            response = requests.get(
                url,
                params=params,
                timeout=30
            )


            # ---------------------------------------------
            # RATE LIMIT
            # ---------------------------------------------

            if response.status_code == 429:

                retry_seconds = (
                    get_retry_seconds(
                        response
                    )
                )

                # Pequeño margen adicional
                retry_seconds += 0.25

                if attempt < retries:

                    time.sleep(
                        retry_seconds
                    )

                    continue


            response.raise_for_status()

            return (
                response.json(),
                None
            )


        except requests.exceptions.RequestException as e:

            try:
                detalle = (
                    response.text[:1000]
                )
            except Exception:
                detalle = ""

            return None, {

                "mensaje":
                    sanitize_text(
                        str(e)
                    ),

                "respuesta_api":
                    sanitize_text(
                        detalle
                    )
            }


        except ValueError as e:

            return None, {

                "mensaje":
                    "Respuesta JSON no válida",

                "detalle":
                    sanitize_text(
                        str(e)
                    )
            }


    return None, {
        "mensaje":
            "Se agotaron los reintentos"
    }


# =========================================================
# OBTENER CUOTAS DE UNA CASA
# =========================================================

def get_bookmaker_odds(
    bookmaker
):

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
# NORMALIZAR FIXTURES
# =========================================================

def normalize_fixtures(data):

    if isinstance(
        data,
        list
    ):
        return data

    if isinstance(
        data,
        dict
    ):

        if isinstance(
            data.get(
                "fixtures"
            ),
            list
        ):

            return data[
                "fixtures"
            ]

        return [data]

    return []


# =========================================================
# INDEXAR PARTIDOS
# =========================================================

def index_fixtures(data):

    fixtures = (
        normalize_fixtures(
            data
        )
    )

    result = {}

    for fixture in fixtures:

        fixture_id = (
            fixture.get(
                "fixtureId"
            )
        )

        if fixture_id:

            result[
                fixture_id
            ] = fixture

    return result


# =========================================================
# SELECCIÓN: PRECIO + TIMESTAMP
# =========================================================

def get_selection(
    markets,
    market_id,
    outcome_id
):

    market = markets.get(
        str(
            market_id
        ),
        {}
    )

    if not market:
        return None

    if not market.get(
        "marketActive",
        True
    ):
        return None

    outcome = (
        market
        .get(
            "outcomes",
            {}
        )
        .get(
            str(
                outcome_id
            ),
            {}
        )
    )

    players = outcome.get(
        "players",
        {}
    )

    for player in players.values():

        if not player.get(
            "active"
        ):
            continue

        price = player.get(
            "price"
        )

        if price is None:
            continue

        try:

            price = float(
                price
            )

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

            "mainLine":
                player.get(
                    "mainLine"
                )
        }

    return None


# =========================================================
# EXTRAER MERCADO
# =========================================================

def extract_market(
    markets,
    config
):

    result = {}

    for (
        selection_name,
        outcome_id
    ) in config[
        "outcomes"
    ].items():

        selection = get_selection(

            markets,

            config[
                "market_id"
            ],

            outcome_id
        )

        if not selection:
            return None

        result[
            selection_name
        ] = selection

    return result


# =========================================================
# QUITAR VIG
# =========================================================

def remove_vig(
    market
):

    probabilities = {}

    total = 0.0

    for (
        selection,
        data
    ) in market.items():

        price = data[
            "price"
        ]

        probability = (
            1.0
            / price
        )

        probabilities[
            selection
        ] = probability

        total += probability


    if total <= 0:
        return None


    fair = {}

    for (
        selection,
        probability
    ) in probabilities.items():

        fair_probability = (
            probability
            / total
        )

        fair[
            selection
        ] = {

            "probability":
                fair_probability,

            "fair_odds":
                (
                    1.0
                    /
                    fair_probability
                )
        }

    return fair


# =========================================================
# TIMESTAMP
# =========================================================

def parse_timestamp(
    value
):

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


# =========================================================
# DIFERENCIA TEMPORAL
# =========================================================

def timestamp_gap_hours(
    win_timestamp,
    reference_timestamps
):

    win_time = parse_timestamp(
        win_timestamp
    )

    refs = []

    for value in reference_timestamps:

        parsed = parse_timestamp(
            value
        )

        if parsed:
            refs.append(
                parsed
            )


    if (
        win_time is None
        or
        not refs
    ):
        return None


    newest_reference = max(
        refs
    )


    gap = (

        newest_reference
        - win_time

    ).total_seconds() / 3600


    return round(
        gap,
        2
    )


# =========================================================
# SCORE
# =========================================================

def calculate_score(
    edge,
    refs,
    confirmations,
    dispersion,
    timestamp_gap,
    price
):

    high_odds = (
        price > 6
    )

    stale = (

        timestamp_gap
        is not None

        and

        timestamp_gap > 6
    )


    # ---------------------------------------------
    # A+
    # ---------------------------------------------

    if (
        edge >= 8
        and refs >= 3
        and confirmations == refs
        and dispersion <= 8
        and not stale
        and not high_odds
    ):

        return (
            "A+",
            "APTO"
        )


    # ---------------------------------------------
    # A
    # ---------------------------------------------

    if (
        edge >= 6
        and refs >= 2
        and confirmations == refs
        and dispersion <= 10
        and not stale
        and not high_odds
    ):

        return (
            "A",
            "APTO"
        )


    # ---------------------------------------------
    # B
    # ---------------------------------------------

    if (
        edge >= 4
        and refs >= 2
        and confirmations >= 2
        and dispersion <= 15
    ):

        return (
            "B",
            "REVISAR"
        )


    return (
        "PASS",
        "PASS"
    )


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():

    return jsonify({

        "status":
            "ok",

        "message":
            "Winamax Value Scanner V2",

        "rutas": {

            "simple":
                "/simple",

            "value_v2":
                "/value-v2",

            "quota":
                "/quota"
        }
    })


# =========================================================
# QUOTA
# =========================================================

@app.route("/quota")
def quota():

    if not API_KEY:

        return jsonify({
            "error":
                "ODDSPAPI_KEY no configurada"
        }), 500


    data, error = api_request(

        ACCOUNT_URL,

        {
            "apiKey":
                API_KEY
        }
    )


    if error:

        return jsonify({
            "error":
                error
        }), 500


    if isinstance(
        data,
        dict
    ):

        data.pop(
            "api_key",
            None
        )

        data.pop(
            "apiKey",
            None
        )


    return jsonify(
        data
    )


# =========================================================
# SIMPLE
# =========================================================

@app.route("/simple")
def simple():

    raw, error = (
        get_bookmaker_odds(
            TARGET_BOOKMAKER
        )
    )

    if error:

        return jsonify({
            "error":
                error
        }), 500


    fixtures = (
        normalize_fixtures(
            raw
        )
    )

    results = []


    for fixture in fixtures:

        bookmaker_data = (

            fixture
            .get(
                "bookmakerOdds",
                {}
            )
            .get(
                TARGET_BOOKMAKER,
                {}
            )
        )


        markets = (
            bookmaker_data.get(
                "markets",
                {}
            )
        )


        partido = {

            "fixtureId":
                fixture.get(
                    "fixtureId"
                ),

            "partido": (
                f"{fixture.get('participant1Name', 'Local')} - "
                f"{fixture.get('participant2Name', 'Visitante')}"
            )
        }


        for (
            market_name,
            config
        ) in MARKETS.items():

            extracted = (
                extract_market(
                    markets,
                    config
                )
            )


            if not extracted:

                partido[
                    market_name
                ] = None

                continue


            partido[
                market_name
            ] = {

                selection:
                    item[
                        "price"
                    ]

                for (
                    selection,
                    item
                ) in extracted.items()
            }


        results.append(
            partido
        )


    return jsonify({

        "status":
            "ok",

        "bookmaker":
            TARGET_BOOKMAKER,

        "numero_partidos":
            len(
                results
            ),

        "partidos":
            results
    })


# =========================================================
# VALUE V2
# =========================================================

@app.route("/value-v2")
def value_v2():

    # ---------------------------------------------
    # WINAMAX
    # ---------------------------------------------

    win_raw, win_error = (
        get_bookmaker_odds(
            TARGET_BOOKMAKER
        )
    )


    if win_error:

        return jsonify({

            "error":
                "Error consultando Winamax",

            "detalle":
                win_error

        }), 500


    win_index = (
        index_fixtures(
            win_raw
        )
    )


    # ---------------------------------------------
    # REFERENCIAS
    # ---------------------------------------------

    reference_indexes = {}

    reference_errors = {}


    for bookmaker in (
        REFERENCE_BOOKMAKERS
    ):

        raw, error = (
            get_bookmaker_odds(
                bookmaker
            )
        )


        if error:

            reference_errors[
                bookmaker
            ] = error

            continue


        reference_indexes[
            bookmaker
        ] = (
            index_fixtures(
                raw
            )
        )


    # ---------------------------------------------
    # MÍNIMO 2 REFERENCIAS
    # ---------------------------------------------

    if len(
        reference_indexes
    ) < 2:

        return jsonify({

            "status":
                "error",

            "mensaje":
                "No hay suficientes casas de referencia disponibles",

            "referencias_ok":
                list(
                    reference_indexes.keys()
                ),

            "errores":
                reference_errors

        }), 500


    oportunidades = []

    mercados_analizados = 0

    partidos_consenso = 0


    # =====================================================
    # RECORRER PARTIDOS
    # =====================================================

    for (
        fixture_id,
        win_fixture
    ) in win_index.items():


        partido_nombre = (

            f"{win_fixture.get('participant1Name', 'Local')} - "
            f"{win_fixture.get('participant2Name', 'Visitante')}"
        )


        win_data = (

            win_fixture
            .get(
                "bookmakerOdds",
                {}
            )
            .get(
                TARGET_BOOKMAKER,
                {}
            )
        )


        win_markets = win_data.get(
            "markets",
            {}
        )


        partido_con_consenso = False


        # =================================================
        # MERCADOS
        # =================================================

        for (
            market_name,
            config
        ) in MARKETS.items():


            win_market = (
                extract_market(
                    win_markets,
                    config
                )
            )


            if not win_market:
                continue


            ref_fair = {}
            ref_raw = {}


            # ---------------------------------------------
            # REFERENCIAS DEL MISMO PARTIDO
            # ---------------------------------------------

            for (
                bookmaker,
                fixture_index
            ) in reference_indexes.items():


                ref_fixture = (
                    fixture_index.get(
                        fixture_id
                    )
                )


                if not ref_fixture:
                    continue


                ref_data = (

                    ref_fixture
                    .get(
                        "bookmakerOdds",
                        {}
                    )
                    .get(
                        bookmaker,
                        {}
                    )
                )


                ref_markets = (
                    ref_data.get(
                        "markets",
                        {}
                    )
                )


                raw_market = (
                    extract_market(
                        ref_markets,
                        config
                    )
                )


                if not raw_market:
                    continue


                fair_market = (
                    remove_vig(
                        raw_market
                    )
                )


                if not fair_market:
                    continue


                ref_raw[
                    bookmaker
                ] = raw_market


                ref_fair[
                    bookmaker
                ] = fair_market


            if len(
                ref_fair
            ) < 2:

                continue


            mercados_analizados += 1

            partido_con_consenso = True


            # =================================================
            # SELECCIONES
            # =================================================

            for (
                selection,
                win_selection
            ) in win_market.items():


                win_price = (
                    win_selection[
                        "price"
                    ]
                )


                probabilities = []

                referencias = {}

                reference_times = []

                confirmations = 0


                for (
                    bookmaker,
                    fair_market
                ) in ref_fair.items():


                    if (
                        selection
                        not in fair_market
                    ):
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


                    raw_price = (

                        ref_raw[
                            bookmaker
                        ][
                            selection
                        ][
                            "price"
                        ]
                    )


                    changed_at = (

                        ref_raw[
                            bookmaker
                        ][
                            selection
                        ].get(
                            "changedAt"
                        )
                    )


                    probabilities.append(
                        probability
                    )


                    if changed_at:

                        reference_times.append(
                            changed_at
                        )


                    if (
                        win_price
                        > fair_odds
                    ):

                        confirmations += 1


                    referencias[
                        bookmaker
                    ] = {

                        "cuota":
                            round(
                                raw_price,
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


                # ---------------------------------------------
                # CONSENSO
                # ---------------------------------------------

                consensus_probability = (
                    statistics.median(
                        probabilities
                    )
                )


                consensus_fair_odds = (

                    1.0
                    /
                    consensus_probability
                )


                # ---------------------------------------------
                # DISPERSIÓN
                # ---------------------------------------------

                min_p = min(
                    probabilities
                )

                max_p = max(
                    probabilities
                )


                dispersion = (

                    (
                        max_p
                        - min_p
                    )

                    /

                    consensus_probability

                ) * 100


                # ---------------------------------------------
                # EDGE
                # ---------------------------------------------

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


                # ---------------------------------------------
                # FRESCURA
                # ---------------------------------------------

                gap = (
                    timestamp_gap_hours(

                        win_selection.get(
                            "changedAt"
                        ),

                        reference_times
                    )
                )


                # ---------------------------------------------
                # SCORE
                # ---------------------------------------------

                score, decision = (
                    calculate_score(

                        edge=
                            edge,

                        refs=
                            refs_count,

                        confirmations=
                            confirmations,

                        dispersion=
                            dispersion,

                        timestamp_gap=
                            gap,

                        price=
                            win_price
                    )
                )


                # ---------------------------------------------
                # ALERTAS
                # ---------------------------------------------

                alertas = []


                if dispersion > 10:

                    alertas.append(
                        "Alta dispersion entre referencias"
                    )


                if (
                    gap is not None
                    and
                    gap > 6
                ):

                    alertas.append(
                        "Winamax podria estar retrasado"
                    )


                if win_price > 6:

                    alertas.append(
                        "Cuota alta"
                    )


                if confirmations < refs_count:

                    alertas.append(
                        "No todas las referencias confirman"
                    )


                oportunidades.append({

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
                            consensus_fair_odds,
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

                    "gap_winamax_horas":
                        gap,

                    "value_score":
                        score,

                    "decision":
                        decision,

                    "alertas":
                        alertas,

                    "referencias":
                        referencias
                })


        if partido_con_consenso:

            partidos_consenso += 1


    # =====================================================
    # ORDEN
    # =====================================================

    orden = {
        "APTO": 0,
        "REVISAR": 1,
        "PASS": 2
    }


    oportunidades.sort(

        key=lambda x: (

            orden.get(
                x[
                    "decision"
                ],
                9
            ),

            -x[
                "edge_pct"
            ]
        )
    )


    aptos = [

        x
        for x in oportunidades

        if x[
            "decision"
        ] == "APTO"
    ]


    revisar = [

        x
        for x in oportunidades

        if x[
            "decision"
        ] == "REVISAR"
    ]


    # =====================================================
    # RESPUESTA
    # =====================================================

    return jsonify({

        "status":
            "ok",

        "version":
            "V2 multicasa rate-limit-safe",

        "metodo":
            "Winamax vs consenso sin margen",

        "referencias_configuradas":
            REFERENCE_BOOKMAKERS,

        "referencias_funcionando":
            list(
                reference_indexes.keys()
            ),

        "errores_referencias":
            reference_errors,

        "intervalo_entre_peticiones_segundos":
            ODDS_MIN_INTERVAL,

        "partidos_winamax":
            len(
                win_index
            ),

        "partidos_con_consenso":
            partidos_consenso,

        "mercados_analizados":
            mercados_analizados,

        "numero_aptos":
            len(
                aptos
            ),

        "numero_revisar":
            len(
                revisar
            ),

        "aptos":
            aptos,

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
