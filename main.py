import os
import time
import threading
import statistics
import requests

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from flask import Flask, jsonify


app = Flask(__name__)


# =========================================================
# CONFIGURACIÓN
# =========================================================

API_KEY = os.environ.get(
    "ODDSPAPI_KEY",
    ""
).strip()

BASE_URL = "https://api.oddspapi.io/v4"

FIXTURES_URL = f"{BASE_URL}/fixtures"
ODDS_URL = f"{BASE_URL}/odds-by-tournaments"
ACCOUNT_URL = f"{BASE_URL}/account"

TARGET_BOOKMAKER = "winamax.es"

REFERENCE_BOOKMAKERS = [
    "pinnacle",
    "singbet",
    "sbobet"
]

LOCAL_TZ = ZoneInfo(
    "Atlantic/Canary"
)


# =========================================================
# RATE LIMITS
# =========================================================

ENDPOINT_INTERVALS = {
    FIXTURES_URL: 2.10,
    ODDS_URL: 1.10
}

_last_request = {}

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

def wait_request_slot(url):

    interval = ENDPOINT_INTERVALS.get(
        url,
        0
    )

    if interval <= 0:
        return


    with _request_lock:

        now = time.monotonic()

        last = _last_request.get(
            url,
            0
        )

        elapsed = (
            now
            - last
        )

        wait_time = (
            interval
            - elapsed
        )


        if wait_time > 0:

            time.sleep(
                wait_time
            )


        _last_request[
            url
        ] = time.monotonic()


# =========================================================
# RETRY 429
# =========================================================

def get_retry_seconds(
    response
):

    try:

        data = response.json()

        retry_ms = (
            data
            .get(
                "error",
                {}
            )
            .get(
                "retryMs"
            )
        )


        if retry_ms is not None:

            return (
                float(
                    retry_ms
                )
                /
                1000.0
            )

    except Exception:
        pass


    return 1.5


# =========================================================
# API REQUEST
# =========================================================

def api_request(
    url,
    params,
    retries=3
):

    response = None


    for attempt in range(
        retries + 1
    ):

        try:

            wait_request_slot(
                url
            )


            response = requests.get(

                url,

                params=params,

                timeout=45
            )


            if (
                response.status_code == 429
                and
                attempt < retries
            ):

                time.sleep(

                    get_retry_seconds(
                        response
                    )
                    + 0.30
                )

                continue


            response.raise_for_status()


            return (
                response.json(),
                None
            )


        except requests.exceptions.RequestException as e:

            detail = ""


            if response is not None:

                try:

                    detail = (
                        response.text[
                            :1000
                        ]
                    )

                except Exception:
                    pass


            return None, {

                "mensaje":
                    sanitize(
                        str(e)
                    ),

                "respuesta_api":
                    sanitize(
                        detail
                    )
            }


        except ValueError as e:

            return None, {

                "mensaje":
                    "OddsPapi no devolvió JSON válido",

                "detalle":
                    sanitize(
                        str(e)
                    )
            }


    return None, {
        "mensaje":
            "Se agotaron los reintentos"
    }


# =========================================================
# FECHA DE HOY
# EN HORA CANARIA
# =========================================================

def today_utc_range():

    now_local = datetime.now(
        LOCAL_TZ
    )


    start_local = now_local.replace(

        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )


    end_local = (
        start_local
        +
        timedelta(
            days=1
        )
    )


    start_utc = (
        start_local
        .astimezone(
            timezone.utc
        )
    )


    end_utc = (
        end_local
        .astimezone(
            timezone.utc
        )
    )


    return (

        start_utc.strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),

        end_utc.strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),

        start_local.date().isoformat()
    )


# =========================================================
# TODOS LOS PARTIDOS DE HOY EN WINAMAX
# =========================================================

def get_today_winamax_fixtures():

    if not API_KEY:

        return None, {

            "mensaje":
                "ODDSPAPI_KEY no configurada"
        }


    start_utc, end_utc, local_date = (
        today_utc_range()
    )


    params = {

        "sportId":
            10,

        "from":
            start_utc,

        "to":
            end_utc,

        "statusId":
            0,

        "hasOdds":
            "true",

        "bookmakers":
            TARGET_BOOKMAKER,

        "language":
            "es",

        "apiKey":
            API_KEY
    }


    data, error = api_request(

        FIXTURES_URL,

        params
    )


    return (
        data,
        error
    )


# =========================================================
# CUOTAS DE UNA CASA
# PARA TODOS LOS TORNEOS
# =========================================================

def get_bookmaker_odds(
    bookmaker,
    tournament_ids
):

    if not tournament_ids:

        return (
            [],
            None
        )


    params = {

        "tournamentIds":
            ",".join(
                str(x)
                for x
                in tournament_ids
            ),

        # El servidor actual de OddsPapi
        # nos exige una casa por petición
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

def normalize_fixtures(
    data
):

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


        return [
            data
        ]


    return []


# =========================================================
# INDEXAR FIXTURES
# =========================================================

def index_fixtures(
    data,
    allowed_ids=None
):

    allowed = (

        set(
            allowed_ids
        )

        if allowed_ids

        else None
    )


    result = {}


    for fixture in (
        normalize_fixtures(
            data
        )
    ):

        fixture_id = (
            fixture.get(
                "fixtureId"
            )
        )


        if not fixture_id:
            continue


        if (
            allowed is not None
            and
            fixture_id not in allowed
        ):

            continue


        result[
            fixture_id
        ] = fixture


    return result


# =========================================================
# MERCADOS DE BOOKMAKER
# =========================================================

def get_bookmaker_markets(
    fixture,
    bookmaker
):

    bookmaker_data = (

        fixture
        .get(
            "bookmakerOdds",
            {}
        )
        .get(
            bookmaker
        )
    )


    if not bookmaker_data:
        return None


    if (
        bookmaker_data.get(
            "bookmakerIsActive"
        )
        is False
    ):

        return None


    if (
        bookmaker_data.get(
            "suspended"
        )
        is True
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

        str(
            market_id
        ),

        {}
    )


    if not market:
        return None


    if (
        market.get(
            "marketActive"
        )
        is False
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


    for player in (
        players.values()
    ):


        if (
            player.get(
                "active"
            )
            is False
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
# QUITAR MARGEN
# =========================================================

def remove_vig(
    market
):

    implied = {}

    total = 0.0


    for (
        selection,
        data
    ) in market.items():


        probability = (

            1.0
            /
            data[
                "price"
            ]
        )


        implied[
            selection
        ] = probability


        total += probability


    if total <= 0:
        return None


    fair = {}


    for (
        selection,
        probability
    ) in implied.items():


        fair_probability = (

            probability
            /
            total
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
# CLASIFICAR CANDIDATO
# =========================================================

def classify_candidate(

    market_name,
    selection,
    win_price,
    probability_pct,
    edge_pct,
    refs_count,
    confirmations,
    dispersion_pct
):

    # Necesitamos mínimo 2 referencias

    if refs_count < 2:

        return "PASS"


    # Todas las disponibles deben confirmar

    if confirmations != refs_count:

        return "PASS"


    # Mercado demasiado dividido

    if dispersion_pct > 10:

        return "PASS"


    # Evitamos Over 3.5 como señal diaria.
    # Seguimos pudiendo detectar Under 3.5.

    if (
        market_name
        ==
        "Over Under 3.5"

        and

        selection
        ==
        "Over 3.5"
    ):

        return "PASS"


    # =====================================================
    # NIVEL A
    # =====================================================

    if (

        probability_pct >= 60

        and

        edge_pct >= 5

        and

        1.40 <= win_price <= 2.10

        and

        dispersion_pct <= 8
    ):

        return "A"


    # =====================================================
    # NIVEL B
    # =====================================================

    if (

        probability_pct >= 57

        and

        edge_pct >= 3.5

        and

        1.45 <= win_price <= 2.30
    ):

        return "B"


    # =====================================================
    # NIVEL C
    # PARA TENER MÁS POSIBILIDADES DE SEÑAL DIARIA
    # =====================================================

    if (

        probability_pct >= 54

        and

        edge_pct >= 2.5

        and

        1.45 <= win_price <= 2.40
    ):

        return "C"


    return "PASS"


# =========================================================
# RANKING
# =========================================================

def level_rank(
    level
):

    return {

        "A": 3,
        "B": 2,
        "C": 1,
        "PASS": 0

    }.get(
        level,
        0
    )


# =========================================================
# MOTOR V4
# =========================================================

def build_value_v4():


    # =====================================================
    # 1. DESCUBRIR TODO EL FÚTBOL DE HOY
    # =====================================================

    fixtures_raw, fixtures_error = (
        get_today_winamax_fixtures()
    )


    if fixtures_error:

        return None, {

            "error":
                "Error obteniendo los partidos de hoy",

            "detalle":
                fixtures_error
        }


    today_fixtures = (
        normalize_fixtures(
            fixtures_raw
        )
    )


    # =====================================================
    # SIN PARTIDOS
    # =====================================================

    if not today_fixtures:

        (
            _,
            _,
            local_date
        ) = today_utc_range()


        return {

            "status":
                "ok",

            "version":
                "V4 todo el futbol",

            "fecha_local":
                local_date,

            "partidos_winamax_hoy":
                0,

            "torneos_detectados":
                0,

            "senal_del_dia":
                None,

            "top_candidatos":
                [],

            "mensaje":
                "No hay partidos pre-match de futbol con cuotas Winamax para hoy."

        }, None


    # =====================================================
    # IDs DE PARTIDOS
    # =====================================================

    fixture_ids = {

        fixture.get(
            "fixtureId"
        )

        for fixture
        in today_fixtures

        if fixture.get(
            "fixtureId"
        )
    }


    # =====================================================
    # TODOS LOS TORNEOS DEL DÍA
    # =====================================================

    tournament_ids = sorted({

        fixture.get(
            "tournamentId"
        )

        for fixture
        in today_fixtures

        if fixture.get(
            "tournamentId"
        )
        is not None
    })


    # =====================================================
    # METADATOS
    # =====================================================

    fixture_meta = {

        fixture.get(
            "fixtureId"
        ):
            fixture

        for fixture
        in today_fixtures

        if fixture.get(
            "fixtureId"
        )
    }


    # =====================================================
    # 2. DESCARGAR CUOTAS
    # =====================================================

    bookmaker_indexes = {}

    bookmaker_errors = {}


    bookmakers = [

        TARGET_BOOKMAKER

    ] + REFERENCE_BOOKMAKERS


    for bookmaker in bookmakers:


        raw, error = (
            get_bookmaker_odds(

                bookmaker,

                tournament_ids
            )
        )


        if error:

            bookmaker_errors[
                bookmaker
            ] = error

            continue


        bookmaker_indexes[
            bookmaker
        ] = index_fixtures(

            raw,

            allowed_ids=
                fixture_ids
        )


    # =====================================================
    # WINAMAX OBLIGATORIO
    # =====================================================

    if (
        TARGET_BOOKMAKER
        not in bookmaker_indexes
    ):

        return None, {

            "error":
                "No se pudieron obtener cuotas de Winamax",

            "errores":
                bookmaker_errors
        }


    # =====================================================
    # REFERENCIAS DISPONIBLES
    # =====================================================

    references_ok = [

        bookmaker

        for bookmaker
        in REFERENCE_BOOKMAKERS

        if bookmaker
        in bookmaker_indexes
    ]


    if len(
        references_ok
    ) < 2:

        return None, {

            "error":
                "No hay suficientes referencias disponibles",

            "referencias_ok":
                references_ok,

            "errores":
                bookmaker_errors
        }


    # =====================================================
    # VARIABLES
    # =====================================================

    candidates = []

    markets_analyzed = 0

    fixtures_with_consensus = set()


    win_index = (
        bookmaker_indexes[
            TARGET_BOOKMAKER
        ]
    )


    # =====================================================
    # 3. RECORRER TODOS LOS PARTIDOS
    # =====================================================

    for (
        fixture_id,
        win_fixture
    ) in win_index.items():


        meta = fixture_meta.get(

            fixture_id,

            win_fixture
        )


        match_name = (

            f"{meta.get('participant1Name', 'Local')} - "
            f"{meta.get('participant2Name', 'Visitante')}"
        )


        win_markets = (
            get_bookmaker_markets(

                win_fixture,

                TARGET_BOOKMAKER
            )
        )


        if not win_markets:
            continue


        # =================================================
        # TODOS LOS MERCADOS
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


            ref_raw = {}

            ref_fair = {}


            # =============================================
            # REFERENCIAS
            # =============================================

            for bookmaker in (
                references_ok
            ):


                ref_fixture = (

                    bookmaker_indexes[
                        bookmaker
                    ].get(
                        fixture_id
                    )
                )


                if not ref_fixture:
                    continue


                ref_markets = (
                    get_bookmaker_markets(

                        ref_fixture,

                        bookmaker
                    )
                )


                if not ref_markets:
                    continue


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


            markets_analyzed += 1

            fixtures_with_consensus.add(
                fixture_id
            )


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

                references = {}

                confirmations = 0


                # =============================================
                # CADA CASA SHARP
                # =============================================

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


                    probabilities.append(
                        probability
                    )


                    if (
                        win_price
                        >
                        fair_odds
                    ):

                        confirmations += 1


                    references[
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


                # =============================================
                # CONSENSO
                # =============================================

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


                # =============================================
                # DISPERSIÓN
                # =============================================

                dispersion = (

                    (
                        max(
                            probabilities
                        )
                        -
                        min(
                            probabilities
                        )
                    )

                    /

                    consensus_probability

                ) * 100


                # =============================================
                # EDGE
                # =============================================

                edge = (

                    (
                        win_price
                        *
                        consensus_probability
                    )

                    - 1

                ) * 100


                probability_pct = (

                    consensus_probability
                    *
                    100
                )


                # =============================================
                # NIVEL
                # =============================================

                level = classify_candidate(

                    market_name=
                        market_name,

                    selection=
                        selection,

                    win_price=
                        win_price,

                    probability_pct=
                        probability_pct,

                    edge_pct=
                        edge,

                    refs_count=
                        refs_count,

                    confirmations=
                        confirmations,

                    dispersion_pct=
                        dispersion
                )


                if level == "PASS":
                    continue


                # =============================================
                # GUARDAR CANDIDATO
                # =============================================

                candidates.append({

                    "fixtureId":
                        fixture_id,

                    "partido":
                        match_name,

                    "hora":
                        meta.get(
                            "startTime"
                        ),

                    "torneo":
                        meta.get(
                            "tournamentName"
                        ),

                    "mercado":
                        market_name,

                    "seleccion":
                        selection,

                    "cuota_winamax":
                        round(
                            win_price,
                            3
                        ),

                    "cuota_justa_consenso":
                        round(
                            consensus_odds,
                            3
                        ),

                    "probabilidad_consenso_pct":
                        round(
                            probability_pct,
                            2
                        ),

                    "edge_pct":
                        round(
                            edge,
                            2
                        ),

                    "dispersion_pct":
                        round(
                            dispersion,
                            2
                        ),

                    "referencias_disponibles":
                        refs_count,

                    "referencias_confirmando":
                        confirmations,

                    "nivel":
                        level,

                    "decision":
                        "APTO",

                    "referencias":
                        references
                })


    # =====================================================
    # 4. ORDENAR
    # NIVEL -> PROBABILIDAD -> EDGE
    # =====================================================

    candidates.sort(

        key=lambda item: (

            level_rank(
                item[
                    "nivel"
                ]
            ),

            item[
                "probabilidad_consenso_pct"
            ],

            item[
                "edge_pct"
            ]
        ),

        reverse=True
    )


    # =====================================================
    # 5. EVITAR VARIAS SEÑALES DEL MISMO PARTIDO
    # =====================================================

    top_candidates = []

    used_matches = set()


    for item in candidates:


        fixture_id = item[
            "fixtureId"
        ]


        if fixture_id in used_matches:

            continue


        top_candidates.append(
            item
        )


        used_matches.add(
            fixture_id
        )


        if len(
            top_candidates
        ) >= 5:

            break


    # =====================================================
    # SEÑAL PRINCIPAL DEL DÍA
    # =====================================================

    signal = (

        top_candidates[
            0
        ]

        if top_candidates

        else None
    )


    (
        _,
        _,
        local_date
    ) = today_utc_range()


    # =====================================================
    # RESULTADO
    # =====================================================

    result = {

        "status":
            "ok",

        "version":
            "V4 todo el futbol",

        "fecha_local":
            local_date,

        "metodo":
            "Winamax vs consenso Pinnacle/SingBet/SBOBET sin margen",

        "partidos_winamax_hoy":
            len(
                today_fixtures
            ),

        "torneos_detectados":
            len(
                tournament_ids
            ),

        "partidos_con_consenso":
            len(
                fixtures_with_consensus
            ),

        "mercados_analizados":
            markets_analyzed,

        "referencias_funcionando":
            references_ok,

        "errores_bookmakers":
            bookmaker_errors,

        "peticiones_api_estimadas":
            (
                1
                +
                1
                +
                len(
                    REFERENCE_BOOKMAKERS
                )
            ),

        "numero_candidatos":
            len(
                candidates
            ),

        "senal_del_dia":
            signal,

        "top_candidatos":
            top_candidates
    }


    return (
        result,
        None
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
            "Winamax Value Scanner V4 - todo el futbol del dia",

        "rutas": {

            "value_v4":
                "/value-v4",

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
# VALUE V4
# =========================================================

@app.route("/value-v4")
def value_v4():

    result, error = (
        build_value_v4()
    )


    if error:

        return jsonify(
            error
        ), 500


    return jsonify(
        result
    )


# =========================================================
# START
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
