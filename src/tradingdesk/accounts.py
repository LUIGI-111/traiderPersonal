"""Cuentas reales (solo seguimiento): Bitso por API de lectura; Tyba y cualquier otra a mano."""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from . import bitso

SYMBOL = re.compile(r"^[A-Za-z0-9.\-]{1,15}$")
CURRENCY = re.compile(r"^[A-Za-z]{3,5}$")
FIAT = {"mxn", "cop", "usd", "ars", "brl", "eur"}

DEFAULT = {
    "accounts": [
        {"id": "bitso", "name": "Bitso", "kind": "bitso", "currency": "MXN", "holdings": [], "synced_at": None, "error": None},
        {"id": "tyba", "name": "Tyba", "kind": "manual", "currency": "COP", "holdings": [], "synced_at": None, "error": None},
    ]
}


class AccountError(ValueError):
    """Entrada inválida del usuario (mensaje apto para mostrar)."""


def _num(v: Any, label: str, optional: bool = False) -> float | None:
    if v in (None, ""):
        if optional:
            return None
        raise AccountError(f"Falta {label}.")
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise AccountError(f"{label} debe ser un número.") from None
    if not math.isfinite(x) or x < 0:
        raise AccountError(f"{label} debe ser un número válido, mayor o igual a 0.")
    return x


def _cur(v: Any) -> str:
    v = str(v or "").strip()
    if not CURRENCY.match(v):
        raise AccountError("Moneda inválida (ej. COP, MXN, USD).")
    return v.upper()


class AccountStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        if not self.path.exists():
            self._save(json.loads(json.dumps(DEFAULT)))

    def load(self) -> dict[str, Any]:
        return json.loads(self.path.read_text())

    def _save(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        tmp.replace(self.path)

    def _acct(self, data: dict, account_id: str) -> dict:
        for a in data["accounts"]:
            if a["id"] == account_id:
                return a
        raise AccountError("Cuenta no encontrada.")

    def add_account(self, name: str, currency: str) -> None:
        name = str(name or "").strip()
        if not 1 <= len(name) <= 40:
            raise AccountError("El nombre de la cuenta debe tener entre 1 y 40 caracteres.")
        with self._lock:
            data = self.load()
            data["accounts"].append({"id": uuid.uuid4().hex[:8], "name": name, "kind": "manual",
                                     "currency": _cur(currency), "holdings": [], "synced_at": None, "error": None})
            self._save(data)

    def delete_account(self, account_id: str) -> None:
        with self._lock:
            data = self.load()
            self._acct(data, account_id)
            data["accounts"] = [a for a in data["accounts"] if a["id"] != account_id]
            self._save(data)

    def add_holding(self, account_id: str, h: dict[str, Any]) -> None:
        name = str(h.get("name") or "").strip()
        if not 1 <= len(name) <= 60:
            raise AccountError("El nombre de la posición debe tener entre 1 y 60 caracteres.")
        ticker = str(h.get("ticker") or "").strip().upper()
        if ticker and not SYMBOL.match(ticker):
            raise AccountError("Ticker inválido (ej. AAPL, BTC-USD).")
        with self._lock:
            data = self.load()
            a = self._acct(data, account_id)
            if a["kind"] != "manual":
                raise AccountError("Las posiciones de esta cuenta vienen de la sincronización, no se editan a mano.")
            a["holdings"].append({
                "id": uuid.uuid4().hex[:8], "name": name, "symbol": ticker or name[:15], "ticker": ticker or None,
                "qty": _num(h.get("qty"), "La cantidad"), "price": _num(h.get("price"), "El precio o valor unitario"),
                "currency": _cur(h.get("currency") or a["currency"]), "cost": _num(h.get("cost"), "El costo", optional=True),
                "source": "manual",
            })
            self._save(data)

    def delete_holding(self, account_id: str, holding_id: str) -> None:
        with self._lock:
            data = self.load()
            a = self._acct(data, account_id)
            if a["kind"] != "manual":
                raise AccountError("Las posiciones sincronizadas no se borran a mano.")
            a["holdings"] = [x for x in a["holdings"] if x["id"] != holding_id]
            self._save(data)

    def set_synced(self, account_id: str, holdings: list[dict] | None, error: str | None) -> None:
        with self._lock:
            data = self.load()
            a = self._acct(data, account_id)
            if holdings is not None:
                a["holdings"], a["synced_at"] = holdings, time.strftime("%Y-%m-%dT%H:%M:%S%z")
            a["error"] = error
            self._save(data)


def sync_bitso(
    store: AccountStore,
    account_id: str = "bitso",
    fetch: Callable = bitso.fetch_balances,
    price: Callable = bitso.last_price,
) -> int:
    """Lee saldos (solo lectura) y los valora en la moneda de la cuenta (o USD si no hay mercado)."""
    key, secret = os.environ.get("BITSO_API_KEY"), os.environ.get("BITSO_API_SECRET")
    if not key or not secret:
        msg = "Faltan BITSO_API_KEY y BITSO_API_SECRET en el archivo .env."
        store.set_synced(account_id, None, msg)
        raise AccountError(msg)
    acct = next((a for a in store.load()["accounts"] if a["id"] == account_id and a["kind"] == "bitso"), None)
    if acct is None:
        raise AccountError("Cuenta Bitso no encontrada.")
    try:
        balances = fetch(key, secret)
    except Exception as e:  # noqa: BLE001
        store.set_synced(account_id, None, str(e))
        raise AccountError(str(e)) from None

    quote = acct["currency"].lower()
    holdings = []
    for b in balances:
        cur, total = b["currency"].lower(), float(b["total"])
        px, pcur = None, None
        for q in dict.fromkeys((quote, "usd")):
            if cur == q:
                px, pcur = 1.0, q.upper()
                break
            try:
                px, pcur = price(f"{cur}_{q}"), q.upper()
                break
            except Exception:  # noqa: BLE001  (sin mercado para ese par)
                continue
        holdings.append({
            "id": cur, "name": cur.upper(), "symbol": cur.upper(),
            "ticker": None if cur in FIAT else f"{cur.upper()}-USD",
            "qty": total, "price": px, "currency": pcur, "cost": None, "source": "bitso",
        })
    store.set_synced(account_id, holdings, None)
    return len(holdings)


def summarize(data: dict[str, Any]) -> dict[str, Any]:
    totals: dict[str, float] = {}
    tickers: set[str] = set()
    accounts, positions = [], 0
    for a in data["accounts"]:
        acc_tot: dict[str, float] = {}
        hs = []
        for h in a["holdings"]:
            val = h["qty"] * h["price"] if h.get("price") is not None else None
            pnl = val - h["cost"] if val is not None and h.get("cost") else None
            hs.append({**h, "value": val, "pnl": pnl})
            if val is not None:
                acc_tot[h["currency"]] = acc_tot.get(h["currency"], 0.0) + val
                totals[h["currency"]] = totals.get(h["currency"], 0.0) + val
            if h.get("ticker"):
                tickers.add(h["ticker"])
        positions += len(hs)
        accounts.append({**a, "holdings": hs, "totals": acc_tot})
    return {"accounts": accounts, "totals": totals, "tickers": sorted(tickers), "positions": positions}
