import os
import statistics
import requests

from datetime import datetime, timezone
from flask import Flask, jsonify, request


app = Flask(__name__)


# =========================================================
# CONFIGURACIÓN
# =========================================================

API_KEY = os.environ.get("ODDSPAPI_KEY", "").strip()

BASE_URL = "https://api.oddspapi.io/v4"

ODDS_URL = f"{BASE_URL}/odds-by-tournaments"
ACCOUNT_URL = f"{BASE_URL}/account"

TARGET_BOOKMAKER = "winamax.es"

REFERENCE_BOOKMAKERS = [
    "pinnacle",
    "singbet",
    "sbobet"
]


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
# SEGURIDAD: NO MOSTRAR API KEY EN ERRORES
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
# LLAMADA API
# =========================================================

def api_request(url, params):

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
            "mensaje": sanitize_text(str(e)),
            "respuesta_api": sanitize_text(detalle)
        }

    except ValueError as e:

        return None, {
            "mensaje": "Respuesta JSON no válida",
            "detalle": sanitize_text(str(e))
        }


# =========================================================
# OBTENER CUOTAS DE UNA CASA
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
        "bookmaker": bookmaker,
        "language": "es",
        "verbosity": 3,
        "oddsFormat": "decimal",
        "apiKey": API_KEY
    }

    return api_request(
        ODDS_URL,
        params
    )


# =========================================================
# NORMALIZAR FIXTURES
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
# INDEXAR POR FIXTURE ID
# =========================================================

def index_fixtures(data):

    fixtures = normalize_fixtures(data)

    result = {}

    for fixture in fixtures:

        fixture_id = fixture.get(
            "fixtureId"
        )

        if fixture_id:
            result[fixture_id] = fixture

    return result


# =========================================================
# EXTRAER PRICE + TIMESTAMP
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

        if not player.get("active"):
            continue

        price = player.get("price")

        if price is None:
            continue

        try:
            price = float(price)
        except Exception:
            continue

        if price <= 1:
            continue

        return {
            "price": price,
            "changedAt": player.get(
                "changedAt"
            ),
            "mainLine": player.get(
                "mainLine"
            )
        }

    return None


# =========================================================
# EXTRAER MERCADO COMPLETO
# =========================================================

def extract_market(markets, config):

    result = {}

    for (
        selection_name,
        outcome_id
    ) in config["outcomes"].items():

        selection = get_selection(
            markets,
            config["market_id"],
            outcome_id
        )

        if not selection:
            return None

        result[
            selection_name
        ] = selection

    return result


# =========================================================
# QUITAR VIG DE UNA CASA
# =========================================================

def remove_vig(market):

    implied = {}
    total = 0.0

    for (
        selection,
        data
    ) in market.items():

        price = data["price"]

        probability = (
            1.0 / price
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
            probability / total
        )

        fair[
            selection
        ] = {
            "probability":
                fair_probability,

            "fair_odds":
                1.0 / fair_probability
        }

    return fair


# =========================================================
# PARSEAR TIMESTAMP
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


# =========================================================
# DIFERENCIA DE TIEMPO WINAMAX VS REFERENCIAS
# =========================================================

def timestamp_gap_hours(
    win_timestamp,
    reference_timestamps
):

    win_time = parse_timestamp(
        win_timestamp
    )

    refs = [
        parse_timestamp(x)
        for x in reference_timestamps
        if x
    ]

    refs = [
        x for x in refs
        if x is not None
    ]

    if (
        win_time is None
        or
        not refs
    ):
        return None

    newest_reference = max(refs)

    gap = (
        newest_reference
        - win_time
    ).total_seconds() / 3600

    return round(
        gap,
        2
    )


# =========================================================
# VALUE SCORE
# =========================================================

def calculate_score(
    edge,
    refs,
    confirmations,
    dispersion,
    timestamp_gap,
    price
):

    # Cuotas muy altas:
    # más sensibles a pequeños cambios
    if price > 6:
        high_odds = True
    else:
        high_odds = False


    # Posible precio muy atrasado
    stale_warning = (
        timestamp_gap is not None
        and timestamp_gap > 6
    )


    if (
        edge >= 8
        and refs >= 3
        and confirmations == refs
        and dispersion <= 8
        and not stale_warning
        and not high_odds
    ):
        return "A+", "APTO"


    if (
        edge >= 6
        and refs >= 2
        and confirmations == refs
        and dispersion <= 10
        and not stale_warning
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

        "status": "ok",

        "message":
            "Winamax Value Scanner V2",

        "rutas": {
            "simple":
                "/simple",

            "value":
                "/value",

            "value_v2":
                "/value-v2",

            "quota":
                "/quota"
        }
    })


# =========================================================
# QUOTA
# /account NO GASTA PETICIONES
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
            "apiKey": API_KEY
        }
    )

    if error:

        return jsonify({
            "error": error
        }), 500

    # Eliminamos la API key antes de devolver datos
    if isinstance(data, dict):
        data.pop(
            "api_key",
            None
        )

    return jsonify(data)


# =========================================================
# SIMPLE WINAMAX
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

    result = []

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

        markets = bookmaker_data.get(
            "markets",
            {}
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

            extracted = extract_market(
                markets,
                config
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
                    data["price"]

                for (
                    selection,
                    data
                ) in extracted.items()
            }

        result.append(
            partido
        )

    return jsonify({

        "status": "ok",

        "bookmaker":
            TARGET_BOOKMAKER,

        "numero_partidos":
            len(result),

        "partidos":
            result
    })


# =========================================================
# VALUE ANTIGUO
# PINNACLE SOLAMENTE
# =========================================================

@app.route("/value")
def value_old():

    return jsonify({
        "status": "ok",
        "message":
            "Usa /value-v2 para el scanner multicasa"
    })


# =========================================================
# VALUE V2
# =========================================================

@app.route("/value-v2")
def value_v2():

    # =====================================================
    # 1 - WINAMAX
    # =====================================================

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


    win_index = index_fixtures(
        win_raw
    )


    # =====================================================
    # 2 - CASAS SHARP
    # =====================================================

    reference_indexes = {}

    reference_errors = {}

    for bookmaker in REFERENCE_BOOKMAKERS:

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
        ] = index_fixtures(
            raw
        )


    # Necesitamos mínimo 2 casas sharp funcionando

    if len(reference_indexes) < 2:

        return jsonify({

            "status": "error",

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

    markets_analyzed = 0

    fixtures_with_consensus = 0


    # =====================================================
    # 3 - CADA PARTIDO WINAMAX
    # =====================================================

    for (
        fixture_id,
        win_fixture
    ) in win_index.items():


        match_name = (
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


        match_had_consensus = False


        # =================================================
        # 4 - CADA MERCADO
        # =================================================

        for (
            market_name,
            config
        ) in MARKETS.items():


            win_market = extract_market(
                win_markets,
                config
            )


            if not win_market:
                continue


            # ---------------------------------------------
            # CREAR FAIR ODDS POR CASA DE REFERENCIA
            # ---------------------------------------------

            ref_fair_markets = {}

            ref_raw_markets = {}


            for (
                bookmaker,
                fixture_index
            ) in reference_indexes.items():


                reference_fixture = (
                    fixture_index.get(
                        fixture_id
                    )
                )


                if not reference_fixture:
                    continue


                reference_data = (
                    reference_fixture
                    .get(
                        "bookmakerOdds",
                        {}
                    )
                    .get(
                        bookmaker,
                        {}
                    )
                )


                reference_markets = (
                    reference_data.get(
                        "markets",
                        {}
                    )
                )


                raw_market = extract_market(
                    reference_markets,
                    config
                )


                if not raw_market:
                    continue


                fair_market = remove_vig(
                    raw_market
                )


                if not fair_market:
                    continue


                ref_raw_markets[
                    bookmaker
                ] = raw_market


                ref_fair_markets[
                    bookmaker
                ] = fair_market


            # Necesitamos mínimo 2 referencias

            if len(ref_fair_markets) < 2:
                continue


            markets_analyzed += 1
            match_had_consensus = True


            # =================================================
            # 5 - CADA SELECCIÓN
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

                reference_details = {}

                reference_times = []

                confirmations = 0


                for (
                    bookmaker,
                    fair_market
                ) in ref_fair_markets.items():


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


                    raw_price = (
                        ref_raw_markets[
                            bookmaker
                        ][
                            selection
                        ][
                            "price"
                        ]
                    )


                    changed_at = (
                        ref_raw_markets[
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


                    # Confirma value si Winamax supera
                    # la cuota justa de esa casa

                    if win_price > fair_odds:

                        confirmations += 1


                    reference_details[
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
                # CONSENSO = MEDIANA DE PROBABILIDADES
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
                # DISPERSIÓN ENTRE CASAS
                # ---------------------------------------------

                min_probability = min(
                    probabilities
                )

                max_probability = max(
                    probabilities
                )


                dispersion = (
                    (
                        max_probability
                        - min_probability
                    )
                    /
                    consensus_probability
                ) * 100


                # ---------------------------------------------
                # EDGE REAL VS CONSENSO
                # ---------------------------------------------

                edge = (
                    (
                        win_price
                        *
                        consensus_probability
                    )
                    - 1
                ) * 100


                # No nos interesa < 3 %

                if edge < 3:
                    continue


                # ---------------------------------------------
                # DIFERENCIA TEMPORAL
                # ---------------------------------------------

                timestamp_gap = (
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

                        edge=edge,

                        refs=refs_count,

                        confirmations=
                            confirmations,

                        dispersion=
                            dispersion,

                        timestamp_gap=
                            timestamp_gap,

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
                    timestamp_gap is not None
                    and
                    timestamp_gap > 6
                ):

                    alertas.append(
                        "Winamax podria estar retrasado"
                    )


                if win_price > 6:

                    alertas.append(
                        "Cuota alta: mayor sensibilidad"
                    )


                if confirmations < refs_count:

                    alertas.append(
                        "No todas las casas confirman"
                    )


                # ---------------------------------------------
                # GUARDAR
                # ---------------------------------------------

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
                        timestamp_gap,

                    "value_score":
                        score,

                    "decision":
                        decision,

                    "alertas":
                        alertas,

                    "referencias":
                        reference_details
                })


        if match_had_consensus:

            fixtures_with_consensus += 1


    # =====================================================
    # ORDENAR
    # APTO PRIMERO, DESPUÉS EDGE
    # =====================================================

    decision_order = {
        "APTO": 0,
        "REVISAR": 1,
        "PASS": 2
    }


    oportunidades.sort(
        key=lambda x: (
            decision_order.get(
                x["decision"],
                9
            ),
            -x["edge_pct"]
        )
    )


    aptos = [
        x
        for x in oportunidades
        if x["decision"] == "APTO"
    ]


    revisar = [
        x
        for x in oportunidades
        if x["decision"] == "REVISAR"
    ]


    # =====================================================
    # RESPUESTA
    # =====================================================

    return jsonify({

        "status":
            "ok",

        "version":
            "V2 multicasa",

        "metodo":
            "Winamax vs consenso sharp sin margen",

        "referencias_configuradas":
            REFERENCE_BOOKMAKERS,

        "referencias_funcionando":
            list(
                reference_indexes.keys()
            ),

        "errores_referencias":
            reference_errors,

        "peticiones_api_estimadas":
            1
            +
            len(
                REFERENCE_BOOKMAKERS
            ),

        "partidos_winamax":
            len(
                win_index
            ),

        "partidos_con_consenso":
            fixtures_with_consensus,

        "mercados_analizados":
            markets_analyzed,

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
            revisar,

        "todas_oportunidades":
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
