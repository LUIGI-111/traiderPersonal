"""Cliente mínimo de Bitso, SOLO LECTURA (saldos y precios). Nunca crea órdenes.

Firma: HMAC-SHA256(secret, nonce + método + ruta + cuerpo); cabecera
`Authorization: Bitso <key>:<nonce>:<firma>`. Crea la llave en Bitso con permiso únicamente de lectura.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request

BASE = "https://api.bitso.com"


class BitsoError(Exception):
    pass


def sign(secret: str, nonce: str, method: str, path: str, body: str = "") -> str:
    return hmac.new(secret.encode(), (nonce + method + path + body).encode(), hashlib.sha256).hexdigest()


def _get(path: str, key: str | None = None, secret: str | None = None, timeout: int = 15):
    headers = {"User-Agent": "tradingdesk-readonly"}
    if key and secret:
        nonce = str(int(time.time() * 1000))
        headers["Authorization"] = f"Bitso {key}:{nonce}:{sign(secret, nonce, 'GET', path)}"
    req = urllib.request.Request(BASE + path, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:  # no se incluye el cuerpo: podría contener datos de la cuenta
        raise BitsoError(f"Bitso respondió HTTP {e.code} (revisa la llave y que tenga permiso de lectura)") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise BitsoError(f"No se pudo conectar con Bitso: {getattr(e, 'reason', e)}") from None
    if not data.get("success"):
        raise BitsoError("Bitso devolvió una respuesta sin éxito")
    return data["payload"]


def fetch_balances(key: str, secret: str) -> list[dict]:
    payload = _get("/api/v3/balance/", key, secret)
    return [b for b in payload.get("balances", []) if float(b.get("total", 0)) > 0]


def last_price(book: str) -> float:
    return float(_get(f"/api/v3/ticker/?book={book}")["last"])
