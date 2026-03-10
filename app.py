import os
import time
import logging
import json
from datetime import datetime, timezone

import requests
from stem import Signal
from stem.control import Controller
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# ── Logging JSON ──────────────────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record):
        log = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            log["exc"] = self.formatException(record.exc_info)
        return json.dumps(log)

handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler])
log = logging.getLogger(__name__)

# ── App ───────────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["10 per minute"],
    storage_uri="memory://",
)

# ── Constantes ────────────────────────────────────────────────────────────────
POSTE_URL = "https://www.poste.it/online/dovequando/DQ-REST/ricercasemplice"
TIMEOUT   = 20          # segundos (Tor es más lento)
MAX_RETRY = 3
BACKOFF   = [2, 4, 8]   # segundos entre reintentos

TOR_PROXIES = {
    "http":  "socks5h://127.0.0.1:9050",
    "https": "socks5h://127.0.0.1:9050",
}

def renew_tor_circuit():
    """Pide a Tor una nueva IP de salida."""
    try:
        with Controller.from_port(port=9051) as c:
            c.authenticate()
            c.signal(Signal.NEWNYM)
        time.sleep(2)
        log.info({"action": "tor_renewed"})
    except Exception as e:
        log.warning({"action": "tor_renew_failed", "err": str(e)})

POSTE_HEADERS = {
    "User-Agent":   "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36",
    "Referer":      "https://www.poste.it/cerca/",
    "Origin":       "https://www.poste.it",
    "Content-Type": "application/json",
    "Accept":       "application/json, text/plain, */*",
}

# stato (str) → ShipLisa status
STATE_MAP = {
    "5": "DELIVERED",
    "4": "OUT_FOR_DELIVERY",
    "3": "IN_TRANSIT",
    "2": "PENDING",
    "1": "INFO_RECEIVED",
}

# ── Helpers ───────────────────────────────────────────────────────────────────
def map_status(stato: str) -> str:
    return STATE_MAP.get(str(stato), "UNKNOWN")


def fetch_poste(codice: str) -> dict:
    payload = {
        "tipoRichiedente": "WEB",
        "codiceSpedizione": codice,
        "periodoRicerca":   3,
    }
    last_err = None
    for attempt in range(MAX_RETRY):
        try:
            log.info({"action": "poste_request", "codice": codice, "attempt": attempt + 1})
            resp = requests.post(
                POSTE_URL,
                json=payload,
                headers=POSTE_HEADERS,
                proxies=TOR_PROXIES,
                timeout=TIMEOUT,
            )
            # CAPTCHA → nueva IP y retry
            if resp.status_code == 400:
                body = resp.json() if resp.content else {}
                if body.get("id") == 2:  # "captcha valido"
                    log.warning({"action": "captcha_detected", "attempt": attempt + 1})
                    renew_tor_circuit()
                    if attempt < MAX_RETRY - 1:
                        continue
                    raise requests.HTTPError("CAPTCHA after retries")
                # 400 normal = tracking no existe
                return None
            # 429 / 5xx → retry con backoff
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(f"HTTP {resp.status_code}")
            resp.raise_for_status()
            return resp.json()
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as e:
            last_err = e
            if attempt < MAX_RETRY - 1:
                sleep_s = BACKOFF[attempt]
                log.warning({"action": "retry", "attempt": attempt + 1, "sleep": sleep_s, "err": str(e)})
                time.sleep(sleep_s)
    raise last_err


def normalize(raw: dict) -> dict:
    tracking = raw.get("idTracciatura", "")
    stato    = str(raw.get("stato", ""))
    status   = map_status(stato)

    movements = raw.get("listaMovimenti") or []
    events = []
    for m in movements:
        raw_status = m.get("statoLavorazione", "")
        events.append({
            "timestamp":  m.get("dataOra"),
            "status":     map_status(stato),   # global status per event
            "location":   m.get("luogo", ""),
            "raw_status": raw_status,
        })

    # status_text = último evento o vacío
    status_text = events[-1]["raw_status"] if events else ""

    return {
        "success":     True,
        "tracking":    tracking,
        "carrier":     "POSTE",
        "status":      status,
        "status_text": status_text,
        "events":      events,
    }

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "ts": datetime.now(timezone.utc).isoformat()})


@app.route("/debug/poste", methods=["POST"])
def debug_poste():
    """Temporal: muestra respuesta raw de Poste para diagnóstico."""
    body = request.get_json(silent=True) or {}
    codice = (body.get("codiceSpedizione") or "").strip()
    payload = {"tipoRichiedente": "WEB", "codiceSpedizione": codice, "periodoRicerca": 3}
    try:
        resp = requests.post(POSTE_URL, json=payload, headers=POSTE_HEADERS, proxies=TOR_PROXIES, timeout=TIMEOUT)
        return jsonify({"status_code": resp.status_code, "body": resp.text[:2000]})
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/track/poste", methods=["POST"])
@limiter.limit("10 per minute")
def track_poste():
    body = request.get_json(silent=True) or {}
    codice = (body.get("codiceSpedizione") or "").strip()

    if not codice:
        return jsonify({"success": False, "error": "MISSING_TRACKING_NUMBER"}), 400

    try:
        raw = fetch_poste(codice)
    except requests.Timeout:
        log.error({"action": "timeout", "codice": codice})
        return jsonify({"success": False, "error": "TIMEOUT"}), 504
    except Exception as e:
        log.error({"action": "upstream_error", "codice": codice, "err": str(e)})
        return jsonify({"success": False, "error": "UPSTREAM_ERROR"}), 502

    # Tracking no encontrado (None = 400 de Poste, o respuesta vacía)
    if raw is None or not raw.get("idTracciatura"):
        log.info({"action": "not_found", "codice": codice})
        return jsonify({"success": False, "error": "NOT_FOUND"}), 404

    result = normalize(raw)
    log.info({"action": "tracked", "codice": codice, "status": result["status"]})
    return jsonify(result)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
