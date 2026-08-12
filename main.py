import os
import re
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit

from flask import Flask, jsonify, request
from curl_cffi import requests as cffi_requests

app = Flask(__name__)

# ============================================================
# CONFIG
# ============================================================

VERSION = "Winamax Direct V2"

DEFAULT_SPORT_URLS = [
    "https://www.winamax.es/apuestas-deportivas/sports/1",
    "https://www.winamax.es/paris-sportifs/sports/1",
    "https://www.winamax.es/deportes/sports/1",
    "https://www.winamax.fr/paris-sportifs/sports/1",
]

CUSTOM_URL = os.environ.get("WINAMAX_SPORT_URL", "").strip()
SPORT_URLS = [CUSTOM_URL] + DEFAULT_SPORT_URLS if CUSTOM_URL else DEFAULT_SPORT_URLS

MIN_CANDIDATE_ODD = float(os.environ.get("MIN_CANDIDATE_ODD", "1.35"))
MAX_CANDIDATE_ODD = float(os.environ.get("MAX_CANDIDATE_ODD", "2.20"))
MAX_CANDIDATES = int(os.environ.get("MAX_CANDIDATES", "100"))

TIMEOUT = int(os.environ.get("WINAMAX_TIMEOUT", "25"))
DETAIL_SCAN_MATCHES = int(os.environ.get("DETAIL_SCAN_MATCHES", "18"))
DETAIL_SCAN_WORKERS = int(os.environ.get("DETAIL_SCAN_WORKERS", "6"))
DETAIL_SCAN_HOURS = int(os.environ.get("DETAIL_SCAN_HOURS", "96"))
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "120"))

# Pequeña caché en memoria para no pedir varias veces las mismas páginas
# en una misma instancia de Render.
_CACHE = {}


# ============================================================
# CACHE
# ============================================================

def cache_get(key):
    item = _CACHE.get(key)
    if not item:
        return None

    expires_at, value = item
    if time.time() >= expires_at:
        _CACHE.pop(key, None)
        return None

    return value


def cache_set(key, value, ttl=CACHE_TTL_SECONDS):
    _CACHE[key] = (time.time() + ttl, value)


# ============================================================
# HTTP DIRECTO A WINAMAX
# ============================================================

def fetch_page(url: str, use_cache=True):
    """
    Acceso directo a Winamax.
    No usa OddsAPI, OddsPapi ni ningún agregador de cuotas.
    curl_cffi imita la huella TLS/HTTP2 de un navegador real.
    """
    cache_key = f"html:{url}"

    if use_cache:
        cached = cache_get(cache_key)
        if cached is not None:
            html, meta = cached
            meta = dict(meta)
            meta["cache"] = True
            return html, meta

    headers = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "accept-language": "es-ES,es;q=0.9,en;q=0.7",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "referer": "https://www.winamax.es/",
        "upgrade-insecure-requests": "1",
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
            "cache": False,
        }

    if response.status_code != 200:
        return None, {
            "url": url,
            "status_code": response.status_code,
            "final_url": str(response.url),
            "body_preview": response.text[:180].replace("\n", " "),
            "cache": False,
        }

    result = (
        response.text,
        {
            "url": url,
            "status_code": response.status_code,
            "final_url": str(response.url),
            "bytes": len(response.content),
            "cache": False,
        },
    )

    if use_cache:
        cache_set(cache_key, result)

    return result


# ============================================================
# EXTRAER PRELOADED_STATE
# ============================================================

def _balanced_json_from(text: str, start: int):
    """Extrae un objeto JSON balanceado empezando en el primer '{'."""
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
    """Extrae PRELOADED_STATE usando varias firmas del frontend de Winamax."""
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
    cache_key = "sport_state"
    cached = cache_get(cache_key)
    if cached is not None:
        state, meta = cached
        meta = dict(meta)
        meta["cache"] = True
        return state, meta

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

        result = (
            state,
            {
                "source_url": meta.get("final_url", url),
                "requested_url": url,
                "diagnostics": diagnostics,
                "cache": False,
            },
        )
        cache_set(cache_key, result)
        return result

    return None, {
        "source_url": None,
        "diagnostics": diagnostics,
        "error": "No se pudo leer PRELOADED_STATE directamente de Winamax.",
        "cache": False,
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


def normalize_text(value):
    return str(value or "").strip().lower()


def extract_goal_line(label):
    """Devuelve la línea numérica de una selección Over/Under, si existe."""
    text = normalize_text(label).replace(",", ".")
    match = re.search(r"(?:más de|menos de|over|under)\s*(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def is_yes_no_pair(labels):
    normalized = {normalize_text(x) for x in labels}
    return normalized in (
        {"sí", "no"},
        {"si", "no"},
        {"yes", "no"},
        {"oui", "non"},
    )


def is_over_under_pair(labels):
    if len(labels) != 2:
        return False

    a = normalize_text(labels[0])
    b = normalize_text(labels[1])
    line_a = extract_goal_line(a)
    line_b = extract_goal_line(b)

    if line_a is None or line_b is None or abs(line_a - line_b) > 1e-9:
        return False

    over_a = a.startswith("más de") or a.startswith("over")
    under_a = a.startswith("menos de") or a.startswith("under")
    over_b = b.startswith("más de") or b.startswith("over")
    under_b = b.startswith("menos de") or b.startswith("under")

    return (over_a and under_b) or (under_a and over_b)


def market_is_mutually_exclusive(market_title, selections):
    """
    Solo normalizamos el margen cuando las selecciones son realmente excluyentes.

    Importante: mercados como 'Doble oportunidad' contienen selecciones solapadas.
    En ellos NO tiene sentido sumar 1/cuota y convertirlo en probabilidades justas.
    """
    labels = [s["seleccion"] for s in selections]
    title = normalize_text(market_title)

    # 1X2 clásico.
    if title == "resultado" and len(labels) == 3:
        return True

    # Mercados binarios Sí/No.
    if len(labels) == 2 and is_yes_no_pair(labels):
        return True

    # Over/Under de una misma línea.
    if len(labels) == 2 and is_over_under_pair(labels):
        return True

    return False


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

    market_title = bet.get("betTitle") or bet.get("title") or "Mercado principal"
    raw_sum = sum(1.0 / s["cuota"] for s in selections)
    mutually_exclusive = market_is_mutually_exclusive(market_title, selections)

    for s in selections:
        if mutually_exclusive:
            fair_p = (1.0 / s["cuota"]) / raw_sum
            s["probabilidad_sin_margen_winamax_pct"] = round(fair_p * 100, 2)
            s["cuota_sin_margen_winamax"] = round(1.0 / fair_p, 3)
        else:
            # No inventamos una probabilidad sin margen cuando las opciones se solapan.
            s["probabilidad_sin_margen_winamax_pct"] = None
            s["cuota_sin_margen_winamax"] = None

    return {
        "bet_id": str(bet_id),
        "market_id": bet.get("marketId"),
        "bet_type": bet.get("betType"),
        "mercado": market_title,
        "mercado_excluyente": mutually_exclusive,
        "suma_probabilidades_implicitas_pct": round(raw_sum * 100, 2),
        "overround_pct": round(raw_sum * 100, 2) if mutually_exclusive else None,
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
# DETALLE DE PARTIDO
# ============================================================

def derive_match_url(source_url: str, match_id: str):
    if "/sports/" in source_url:
        prefix = source_url.split("/sports/")[0]
        return f"{prefix}/match/{match_id}"

    parts = urlsplit(source_url)
    return f"{parts.scheme}://{parts.netloc}/paris-sportifs/match/{match_id}"


def detect_bets_for_match(detail_state, match_id):
    markets = []
    bets = detail_state.get("bets", {})

    if not isinstance(bets, dict):
        return markets

    for bet_id, bet in bets.items():
        if not isinstance(bet, dict):
            continue

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


def load_match_markets(source_url, match_id):
    cache_key = f"markets:{match_id}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    match_url = derive_match_url(source_url, match_id)
    html, fetch_meta = fetch_page(match_url)

    if not html:
        result = {
            "ok": False,
            "match_id": str(match_id),
            "match_url": match_url,
            "error": fetch_meta,
            "markets": [],
        }
        return result

    detail_state, marker = extract_preloaded_state(html)
    if not detail_state:
        result = {
            "ok": False,
            "match_id": str(match_id),
            "match_url": match_url,
            "error": "PRELOADED_STATE no encontrado",
            "fetch": fetch_meta,
            "markets": [],
        }
        return result

    result = {
        "ok": True,
        "match_id": str(match_id),
        "match_url": match_url,
        "marker": marker,
        "markets": detect_bets_for_match(detail_state, match_id),
    }
    cache_set(cache_key, result)
    return result


# ============================================================
# CLASIFICACIÓN DE MERCADOS PRIORITARIOS
# ============================================================

def classify_priority_market(market):
    """
    Devuelve (clave, prioridad) o (None, None).

    Prioridades del radar:
      1) 1X2
      2) Doble oportunidad
      3) Over/Under 1.5 y 2.5
      4) Under 3.5 y 4.5
      5) Ambos marcan
    """
    title = normalize_text(market.get("mercado"))
    labels = [normalize_text(s.get("seleccion")) for s in market.get("selecciones", [])]

    if title == "resultado" and len(labels) == 3:
        return "1X2", 1

    # Solo doble oportunidad simple. Excluimos combinadas y primera mitad.
    if title == "doble oportunidad":
        return "DOBLE_OPORTUNIDAD", 2

    # Ambos equipos marcan simple.
    if title in {"ambos equipos marcan", "¿ambos equipos marcan?"}:
        return "AMBOS_MARCAN", 5

    # Total de goles partido completo, no equipo individual ni primera mitad.
    if title == "número total de goles" and len(labels) == 2:
        lines = [extract_goal_line(x) for x in labels]
        line = lines[0] if lines and lines[0] is not None else None

        if line is not None and all(x is not None and abs(x - line) < 1e-9 for x in lines):
            if line == 1.5:
                return "TOTAL_1_5", 3
            if line == 2.5:
                return "TOTAL_2_5", 3
            if line == 3.5:
                return "UNDER_3_5", 4
            if line == 4.5:
                return "UNDER_4_5", 4

    return None, None


def selection_allowed_for_priority(market_key, selection_label):
    label = normalize_text(selection_label)

    # En 1X2 evitamos el empate como candidato principal.
    if market_key == "1X2" and label in {"empate", "x", "draw", "nul"}:
        return False

    # Para 3.5 y 4.5 el radar quiere específicamente el Under.
    if market_key in {"UNDER_3_5", "UNDER_4_5"}:
        return label.startswith("menos de") or label.startswith("under")

    return True


def candidate_from_selection(match, market, market_key, market_priority, selection):
    odd = selection["cuota"]

    if not (MIN_CANDIDATE_ODD <= odd <= MAX_CANDIDATE_ODD):
        return None

    if not selection_allowed_for_priority(market_key, selection["seleccion"]):
        return None

    fair_prob = selection.get("probabilidad_sin_margen_winamax_pct")
    ranking_prob = fair_prob if fair_prob is not None else selection["probabilidad_implicita_pct"]

    return {
        "match_id": match["match_id"],
        "partido": match["partido"],
        "torneo": match["torneo"],
        "inicio_utc": match["inicio_utc"],
        "tipo_mercado": market_key,
        "prioridad_mercado": market_priority,
        "mercado": market["mercado"],
        "seleccion": selection["seleccion"],
        "cuota_winamax": odd,
        "probabilidad_implicita_pct": selection["probabilidad_implicita_pct"],
        "probabilidad_sin_margen_winamax_pct": fair_prob,
        "cuota_sin_margen_winamax": selection.get("cuota_sin_margen_winamax"),
        "mercado_excluyente": market.get("mercado_excluyente", False),
        "overround_pct": market.get("overround_pct"),
        "ranking_probabilidad_pct": ranking_prob,
        "fuente": "Winamax directo",
    }


# ============================================================
# CANDIDATOS V2
# ============================================================

def build_main_1x2_candidates(matches):
    candidates = []

    for match in matches:
        market = match["mercado_principal"]
        market_key, priority = classify_priority_market(market)

        if market_key != "1X2":
            continue

        for selection in market["selecciones"]:
            candidate = candidate_from_selection(
                match, market, market_key, priority, selection
            )
            if candidate:
                candidate["origen"] = "mercado_principal"
                candidates.append(candidate)

    return candidates


def matches_for_detail_scan(matches):
    """Escanea primero los partidos más próximos dentro del horizonte configurado."""
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=DETAIL_SCAN_HOURS)

    selected = []
    for match in matches:
        start = datetime.fromisoformat(match["inicio_utc"])
        if start <= horizon:
            selected.append(match)
        if len(selected) >= DETAIL_SCAN_MATCHES:
            break

    # Si hay pocos dentro del horizonte, completamos con los siguientes.
    if len(selected) < DETAIL_SCAN_MATCHES:
        used = {m["match_id"] for m in selected}
        for match in matches:
            if match["match_id"] in used:
                continue
            selected.append(match)
            if len(selected) >= DETAIL_SCAN_MATCHES:
                break

    return selected


def build_detail_candidates(matches, source_url):
    selected_matches = matches_for_detail_scan(matches)
    match_lookup = {m["match_id"]: m for m in selected_matches}
    candidates = []
    diagnostics = []

    if not selected_matches:
        return candidates, diagnostics

    workers = max(1, min(DETAIL_SCAN_WORKERS, len(selected_matches)))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(load_match_markets, source_url, match["match_id"]): match["match_id"]
            for match in selected_matches
        }

        for future in as_completed(futures):
            match_id = futures[future]
            match = match_lookup[match_id]

            try:
                detail = future.result()
            except Exception as exc:
                diagnostics.append({
                    "match_id": match_id,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                })
                continue

            diagnostics.append({
                "match_id": match_id,
                "ok": detail.get("ok", False),
                "numero_mercados": len(detail.get("markets", [])),
            })

            if not detail.get("ok"):
                continue

            for market in detail["markets"]:
                market_key, priority = classify_priority_market(market)
                if market_key is None:
                    continue

                for selection in market["selecciones"]:
                    candidate = candidate_from_selection(
                        match, market, market_key, priority, selection
                    )
                    if candidate:
                        candidate["origen"] = "detalle_partido"
                        candidates.append(candidate)

    return candidates, diagnostics


def dedupe_candidates(candidates):
    best = {}

    for item in candidates:
        signature = (
            item["match_id"],
            item["tipo_mercado"],
            normalize_text(item["mercado"]),
            normalize_text(item["seleccion"]),
        )

        previous = best.get(signature)
        if previous is None:
            best[signature] = item
            continue

        # Si por alguna razón aparece duplicado, conservamos la cuota más alta.
        if item["cuota_winamax"] > previous["cuota_winamax"]:
            best[signature] = item

    return list(best.values())


def candidate_sort_key(item):
    start = datetime.fromisoformat(item["inicio_utc"])
    return (
        start,
        item["prioridad_mercado"],
        -item["ranking_probabilidad_pct"],
        -item["cuota_winamax"],
    )


def build_candidates_v2(matches, source_url, include_detail=True):
    candidates = build_main_1x2_candidates(matches)
    detail_diagnostics = []

    if include_detail:
        expanded, detail_diagnostics = build_detail_candidates(matches, source_url)
        candidates.extend(expanded)

    candidates = dedupe_candidates(candidates)
    candidates.sort(key=candidate_sort_key)

    return candidates[:MAX_CANDIDATES], detail_diagnostics


# ============================================================
# ENDPOINTS
# ============================================================

@app.route("/")
def home():
    return jsonify({
        "status": "ok",
        "version": VERSION,
        "fuente": "Winamax directo",
        "directo_winamax": True,
        "oddsapi": False,
        "rutas": {
            "health": "/health",
            "scanner": "/winamax-direct",
            "candidatos": "/candidates",
            "detalle_partido": "/match/<match_id>",
        },
        "mercados_prioritarios": [
            "1X2",
            "Doble oportunidad",
            "Over/Under 1.5",
            "Over/Under 2.5",
            "Under 3.5",
            "Under 4.5",
            "Ambos equipos marcan",
        ],
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
        "version": VERSION,
        "directo_winamax": True,
        "oddsapi": False,
        "source_url": meta["source_url"],
        "cache": meta.get("cache", False),
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
        "version": VERSION,
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

    # detail=0 permite una respuesta rápida solo con 1X2.
    detail_param = normalize_text(request.args.get("detail", "1"))
    include_detail = detail_param not in {"0", "false", "no", "off"}

    items, detail_diagnostics = build_candidates_v2(
        matches,
        meta["source_url"],
        include_detail=include_detail,
    )

    tipos = {}
    for item in items:
        tipos[item["tipo_mercado"]] = tipos.get(item["tipo_mercado"], 0) + 1

    return jsonify({
        "status": "ok",
        "version": VERSION,
        "directo_winamax": True,
        "oddsapi": False,
        "source_url": meta["source_url"],
        "rango_cuota": [MIN_CANDIDATE_ODD, MAX_CANDIDATE_ODD],
        "partidos_prematch": len(matches),
        "detalle_activado": include_detail,
        "partidos_detalle_objetivo": DETAIL_SCAN_MATCHES if include_detail else 0,
        "numero_candidatos": len(items),
        "candidatos_por_tipo": tipos,
        "nota": (
            "Candidatos obtenidos directamente de Winamax. No son pronósticos finales. "
            "probabilidad_sin_margen_winamax_pct solo se calcula cuando las selecciones "
            "del mercado son mutuamente excluyentes. En mercados solapados, como doble "
            "oportunidad, ese campo queda null y ChatGPT debe estimar su propia probabilidad."
        ),
        "diagnostico_detalle": detail_diagnostics,
        "candidatos": items,
    })


@app.route("/match/<match_id>")
def match_detail(match_id):
    state, meta = load_sport_state()

    if state is None:
        return jsonify({
            "status": "blocked",
            "directo_winamax": True,
            "oddsapi": False,
            **meta,
        }), 503

    detail = load_match_markets(meta["source_url"], match_id)

    if not detail.get("ok"):
        return jsonify({
            "status": "error",
            "directo_winamax": True,
            "oddsapi": False,
            "match_id": match_id,
            "match_url": detail.get("match_url"),
            "detalle": detail.get("error"),
            "fetch": detail.get("fetch"),
        }), 502

    priority_markets = []
    for market in detail["markets"]:
        market_key, priority = classify_priority_market(market)
        if market_key:
            enriched = dict(market)
            enriched["tipo_mercado"] = market_key
            enriched["prioridad_mercado"] = priority
            priority_markets.append(enriched)

    return jsonify({
        "status": "ok",
        "version": VERSION,
        "directo_winamax": True,
        "oddsapi": False,
        "match_id": match_id,
        "match_url": detail["match_url"],
        "marker": detail.get("marker"),
        "numero_mercados": len(detail["markets"]),
        "numero_mercados_prioritarios": len(priority_markets),
        "mercados_prioritarios": priority_markets,
        "mercados": detail["markets"],
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
