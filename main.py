import os
import re
import json
import math
from datetime import datetime, timezone
from urllib.parse import urlsplit

from flask import Flask, jsonify, request
from curl_cffi import requests as cffi_requests

app = Flask(__name__)

# ============================================================
# CONFIG
# ============================================================

DEFAULT_SPORT_URLS = [
    # España: probamos varias rutas porque Winamax puede cambiar
    # el slug visible sin cambiar la estructura interna.
    "https://www.winamax.es/apuestas-deportivas/sports/1",
    "https://www.winamax.es/paris-sportifs/sports/1",
    "https://www.winamax.es/deportes/sports/1",

    # Fallback técnico del frontend francés. No se usa si España funciona.
    "https://www.winamax.fr/paris-sportifs/sports/1",
]

CUSTOM_URL = os.environ.get("WINAMAX_SPORT_URL", "").strip()
SPORT_URLS = [CUSTOM_URL] + DEFAULT_SPORT_URLS if CUSTOM_URL else DEFAULT_SPORT_URLS

MIN_CANDIDATE_ODD = float(os.environ.get("MIN_CANDIDATE_ODD", "1.45"))
MAX_CANDIDATE_ODD = float(os.environ.get("MAX_CANDIDATE_ODD", "2.20"))
MAX_CANDIDATES = int(os.environ.get("MAX_CANDIDATES", "80"))

TIMEOUT = int(os.environ.get("WINAMAX_TIMEOUT", "25"))

# ============================================================
# HTTP DIRECTO A WINAMAX
# ============================================================

def fetch_page(url: str):
    """
    Acceso directo a Winamax.
    No usa OddsPapi ni otro agregador de cuotas.
    curl_cffi imita la huella TLS/HTTP2 de un navegador real.
    """
    headers = {
        "accept-language": "es-ES,es;q=0.9,en;q=0.7",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "referer": "https://www.winamax.es/",
    }

    try:
        response = cffi_requests.get(
            url,
            headers=headers,
            impersonate="chrome",
            timeout=TIMEOUT,
            allow_redirects=True,
        )
    except Exception as exc:
        return None, {
            "url": url,
            "error": f"{type(exc).__name__}: {exc}",
        }

    if response.status_code != 200:
        return None, {
            "url": url,
            "status_code": response.status_code,
            "final_url": str(response.url),
            "body_preview": response.text[:180].replace("\n", " "),
        }

    return response.text, {
        "url": url,
        "status_code": response.status_code,
        "final_url": str(response.url),
        "bytes": len(response.content),
    }


# ============================================================
# EXTRAER PRELOADED_STATE
# ============================================================

def _balanced_json_from(text: str, start: int):
    """
    Extrae un objeto JSON balanceado empezando en el primer '{'.
    Tolera strings y caracteres escapados.
    """
    brace = text.find("{", start)
    if brace < 0:
        return None

    depth = 0
    in_string = False
    escape = False

    for i in range(brace, len(text)):
        ch = text[i]

        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[brace:i + 1]

    return None


def extract_preloaded_state(html: str):
    """
    Winamax históricamente incrusta las cuotas en PRELOADED_STATE.
    Se prueban varias formas para resistir pequeños cambios del frontend.
    """
    markers = [
        "var PRELOADED_STATE",
        "window.PRELOADED_STATE",
        "PRELOADED_STATE =",
        "__PRELOADED_STATE__",
    ]

    for marker in markers:
        pos = html.find(marker)
        if pos < 0:
            continue

        json_text = _balanced_json_from(html, pos)
        if not json_text:
            continue

        try:
            state = json.loads(json_text)
            if isinstance(state, dict) and "matches" in state:
                return state, marker
        except json.JSONDecodeError:
            continue

    # Fallback antiguo exacto documentado por implementaciones públicas.
    exact = re.search(
        r"var\s+PRELOADED_STATE\s*=\s*(\{.*?\})\s*;\s*var\s+BETTING_CONFIGURATION",
        html,
        flags=re.S,
    )
    if exact:
        try:
            state = json.loads(exact.group(1))
            if isinstance(state, dict) and "matches" in state:
                return state, "legacy-regex"
        except json.JSONDecodeError:
            pass

    return None, None


# ============================================================
# CARGAR WINAMAX
# ============================================================

def load_sport_state():
    diagnostics = []

    for url in SPORT_URLS:
        html, meta = fetch_page(url)
        diagnostics.append(meta)

        if not html:
            continue

        state, marker = extract_preloaded_state(html)
        if not state:
            diagnostics[-1]["preloaded_state"] = False
            continue

        diagnostics[-1]["preloaded_state"] = True
        diagnostics[-1]["marker"] = marker

        return state, {
            "source_url": meta.get("final_url", url),
            "requested_url": url,
            "diagnostics": diagnostics,
        }

    return None, {
        "source_url": None,
        "diagnostics": diagnostics,
        "error": (
            "No se pudo leer PRELOADED_STATE directamente de Winamax. "
            "Si todos los intentos devuelven 403, el siguiente paso será "
            "activar un navegador headless directo (Playwright), sin usar agregadores."
        ),
    }


# ============================================================
# HELPERS DEL ESTADO WINAMAX
# ============================================================

def dget(mapping, key, default=None):
    if not isinstance(mapping, dict):
        return default
    return mapping.get(str(key), mapping.get(key, default))


def parse_timestamp(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None

    # Si viniera en milisegundos.
    if value > 10_000_000_000:
        value /= 1000.0

    try:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def tournament_name(state, tournament_id):
    tournament = dget(state.get("tournaments", {}), tournament_id, {})
    if isinstance(tournament, dict):
        return (
            tournament.get("tournamentName")
            or tournament.get("name")
            or str(tournament_id)
        )
    return str(tournament_id)


def outcome_label(state, outcome_id, index=None):
    outcome = dget(state.get("outcomes", {}), outcome_id, {})
    if isinstance(outcome, dict):
        return (
            outcome.get("label")
            or outcome.get("name")
            or outcome.get("code")
            or f"Selección {index + 1 if index is not None else outcome_id}"
        )
    return f"Selección {index + 1 if index is not None else outcome_id}"


def odd_value(state, outcome_id):
    value = dget(state.get("odds", {}), outcome_id)

    # En algunas versiones odds puede ser número; en otras objeto.
    if isinstance(value, dict):
        for key in ("odd", "odds", "price", "value"):
            if key in value:
                value = value[key]
                break

    try:
        odd = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(odd) or odd <= 1.0:
        return None

    return odd


def bet_to_market(state, bet_id):
    bet = dget(state.get("bets", {}), bet_id, {})
    if not isinstance(bet, dict):
        return None

    outcome_ids = bet.get("outcomes") or []
    if not isinstance(outcome_ids, list) or len(outcome_ids) < 2:
        return None

    selections = []
    for index, outcome_id in enumerate(outcome_ids):
        odd = odd_value(state, outcome_id)
        if odd is None:
            continue

        selections.append({
            "outcome_id": str(outcome_id),
            "seleccion": outcome_label(state, outcome_id, index),
            "cuota": round(odd, 3),
            "probabilidad_implicita_pct": round((1.0 / odd) * 100, 2),
        })

    if len(selections) < 2:
        return None

    overround = sum(1.0 / s["cuota"] for s in selections)

    for s in selections:
        fair_p = (1.0 / s["cuota"]) / overround
        s["probabilidad_sin_margen_winamax_pct"] = round(fair_p * 100, 2)
        s["cuota_sin_margen_winamax"] = round(1.0 / fair_p, 3)

    return {
        "bet_id": str(bet_id),
        "market_id": bet.get("marketId"),
        "bet_type": bet.get("betType"),
        "mercado": bet.get("betTitle") or bet.get("title") or "Mercado principal",
        "overround_pct": round(overround * 100, 2),
        "selecciones": selections,
    }


# ============================================================
# PARTIDOS PREMATCH
# ============================================================

def extract_matches(state):
    now = datetime.now(timezone.utc)
    results = []

    matches = state.get("matches", {})
    if not isinstance(matches, dict):
        return results

    for match in matches.values():
        if not isinstance(match, dict):
            continue

        # Fútbol: la implementación pública histórica de Winamax usa sportId=1.
        sport_id = match.get("sportId")
        if sport_id not in (1, "1", None):
            continue

        if match.get("isOutright"):
            continue

        if match.get("competitor1Id") == 0:
            continue

        start = parse_timestamp(match.get("matchStart"))
        if not start or start <= now:
            continue

        main_bet_id = match.get("mainBetId")
        if main_bet_id is None:
            continue

        market = bet_to_market(state, main_bet_id)
        if not market:
            continue

        match_id = match.get("matchId")
        title = (
            match.get("title")
            or match.get("matchName")
            or f"Partido {match_id}"
        )

        results.append({
            "match_id": str(match_id) if match_id is not None else None,
            "partido": str(title).strip(),
            "torneo": tournament_name(state, match.get("tournamentId")),
            "tournament_id": match.get("tournamentId"),
            "inicio_utc": start.isoformat(),
            "mercado_principal": market,
        })

    results.sort(key=lambda x: x["inicio_utc"])
    return results


# ============================================================
# CANDIDATOS PARA QUE CHATGPT LOS ANALICE
# ============================================================

def build_candidates(matches):
    candidates = []

    for match in matches:
        market = match["mercado_principal"]

        for selection in market["selecciones"]:
            odd = selection["cuota"]

            if not (MIN_CANDIDATE_ODD <= odd <= MAX_CANDIDATE_ODD):
                continue

            # Evitamos priorizar el empate como "alta probabilidad".
            label = selection["seleccion"].strip().lower()
            if label in {"empate", "x", "draw", "nul"}:
                continue

            candidates.append({
                "match_id": match["match_id"],
                "partido": match["partido"],
                "torneo": match["torneo"],
                "inicio_utc": match["inicio_utc"],
                "mercado": market["mercado"],
                "seleccion": selection["seleccion"],
                "cuota_winamax": odd,
                "probabilidad_implicita_pct": selection["probabilidad_implicita_pct"],
                "probabilidad_sin_margen_winamax_pct": selection[
                    "probabilidad_sin_margen_winamax_pct"
                ],
                "cuota_sin_margen_winamax": selection[
                    "cuota_sin_margen_winamax"
                ],
                "overround_pct": market["overround_pct"],
            })

    # Primero las probabilidades más altas; después la menor cuota.
    candidates.sort(
        key=lambda x: (
            x["probabilidad_sin_margen_winamax_pct"],
            -x["cuota_winamax"],
        ),
        reverse=True,
    )

    return candidates[:MAX_CANDIDATES]


# ============================================================
# DETALLE DE UN PARTIDO: TODOS LOS MERCADOS QUE WINAMAX EMBEBA
# ============================================================

def derive_match_url(source_url: str, match_id: str):
    if "/sports/" in source_url:
        prefix = source_url.split("/sports/")[0]
        return f"{prefix}/match/{match_id}"

    parts = urlsplit(source_url)
    return f"{parts.scheme}://{parts.netloc}/paris-sportifs/match/{match_id}"


def detect_bets_for_match(detail_state, match_id):
    """
    En la página /match/<id>, el PRELOADED_STATE suele contener las apuestas
    del encuentro. Al ser una página de un único partido, exponemos todos los
    bets con cuotas válidas.
    """
    markets = []

    bets = detail_state.get("bets", {})
    if not isinstance(bets, dict):
        return markets

    for bet_id, bet in bets.items():
        if not isinstance(bet, dict):
            continue

        # Si el objeto trae matchId/eventId y no coincide, se descarta.
        owner = (
            bet.get("matchId")
            or bet.get("eventId")
            or bet.get("event_id")
        )
        if owner is not None and str(owner) != str(match_id):
            continue

        market = bet_to_market(detail_state, bet_id)
        if market:
            markets.append(market)

    # Deduplicado sencillo.
    seen = set()
    unique = []
    for market in markets:
        signature = (
            market["mercado"],
            tuple((s["seleccion"], s["cuota"]) for s in market["selecciones"]),
        )
        if signature in seen:
            continue
        seen.add(signature)
        unique.append(market)

    return unique


# ============================================================
# ENDPOINTS
# ============================================================

@app.route("/")
def home():
    return jsonify({
        "status": "ok",
        "version": "Winamax Direct V1",
        "fuente": "Winamax directo",
        "oddsapi": False,
        "rutas": {
            "health": "/health",
            "scanner": "/winamax-direct",
            "candidatos": "/candidates",
            "detalle_partido": "/match/<match_id>",
        }
    })


@app.route("/health")
def health():
    state, meta = load_sport_state()

    if state is None:
        return jsonify({
            "status": "blocked",
            "directo_winamax": True,
            "oddsapi": False,
            **meta,
        }), 503

    return jsonify({
        "status": "ok",
        "directo_winamax": True,
        "oddsapi": False,
        "source_url": meta["source_url"],
        "keys": sorted(list(state.keys()))[:30],
        "matches_raw": len(state.get("matches", {})),
        "bets_raw": len(state.get("bets", {})),
        "odds_raw": len(state.get("odds", {})),
    })


@app.route("/winamax-direct")
def winamax_direct():
    state, meta = load_sport_state()

    if state is None:
        return jsonify({
            "status": "blocked",
            "directo_winamax": True,
            "oddsapi": False,
            **meta,
        }), 503

    matches = extract_matches(state)

    return jsonify({
        "status": "ok",
        "version": "Winamax Direct V1",
        "directo_winamax": True,
        "oddsapi": False,
        "source_url": meta["source_url"],
        "partidos_prematch": len(matches),
        "partidos": matches,
    })


@app.route("/candidates")
def candidates():
    state, meta = load_sport_state()

    if state is None:
        return jsonify({
            "status": "blocked",
            "directo_winamax": True,
            "oddsapi": False,
            **meta,
        }), 503

    matches = extract_matches(state)
    items = build_candidates(matches)

    return jsonify({
        "status": "ok",
        "version": "Winamax Direct V1",
        "directo_winamax": True,
        "oddsapi": False,
        "source_url": meta["source_url"],
        "rango_cuota": [MIN_CANDIDATE_ODD, MAX_CANDIDATE_ODD],
        "partidos_prematch": len(matches),
        "numero_candidatos": len(items),
        "nota": (
            "Estos son candidatos por cuota Winamax, no pronósticos finales. "
            "La probabilidad sin margen solo elimina el margen interno de Winamax; "
            "ChatGPT debe analizar el partido antes de declarar value."
        ),
        "candidatos": items,
    })


@app.route("/match/<match_id>")
def match_detail(match_id):
    # Primero descubrimos cuál de las rutas de Winamax funciona.
    state, meta = load_sport_state()

    if state is None:
        return jsonify({
            "status": "blocked",
            "directo_winamax": True,
            "oddsapi": False,
            **meta,
        }), 503

    match_url = derive_match_url(meta["source_url"], match_id)
    html, fetch_meta = fetch_page(match_url)

    if not html:
        return jsonify({
            "status": "error",
            "match_id": match_id,
            "match_url": match_url,
            "detalle": fetch_meta,
        }), 502

    detail_state, marker = extract_preloaded_state(html)

    if not detail_state:
        return jsonify({
            "status": "error",
            "match_id": match_id,
            "match_url": match_url,
            "mensaje": "La página abrió pero no apareció PRELOADED_STATE.",
            "fetch": fetch_meta,
        }), 502

    markets = detect_bets_for_match(detail_state, match_id)

    return jsonify({
        "status": "ok",
        "directo_winamax": True,
        "oddsapi": False,
        "match_id": match_id,
        "match_url": match_url,
        "marker": marker,
        "numero_mercados": len(markets),
        "mercados": markets,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
