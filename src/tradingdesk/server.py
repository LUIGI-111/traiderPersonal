from __future__ import annotations

import dataclasses
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .accounts import AccountError, AccountStore, summarize, sync_bitso
from .config import Settings
from .data import SyntheticMarket, YahooMarket
from .desk import TradingDesk
from .office import build_state

TICKER = re.compile(r"^[A-Z0-9.\-]{1,12}$")
ENVS = ("paper", "real")


class Office:
    """Estado del servidor. Dos entornos aislados:
    - paper: dinero simulado (carpeta de datos raíz). Puede ejecutar órdenes simuladas.
    - real:  solo seguimiento (subcarpeta real/). NUNCA ejecuta órdenes, se fuerza aquí.
    """

    def __init__(self, settings: Settings, demo: bool = False):
        self.settings, self.demo = settings, demo
        self.lock = threading.Lock()
        self.runner: dict = {"running": False}
        self.real_dir = Path(settings.state_dir) / "real"
        self.real_dir.mkdir(parents=True, exist_ok=True)
        self.store = AccountStore(self.real_dir / "accounts.json")

    def state_dir(self, env: str) -> Path:
        return Path(self.settings.state_dir) if env == "paper" else self.real_dir

    def start_run(self, symbols: list[str], execute: bool, env: str = "paper", lab_only: bool = False) -> tuple[bool, str]:
        symbols = [s.strip().upper() for s in symbols if str(s).strip()]
        if env not in ENVS:
            return False, "Entorno inválido."
        if not symbols or len(symbols) > 20 or not all(TICKER.match(s) for s in symbols):
            return False, "Tickers inválidos (máx. 20, formato AAPL, BRK-B, BTC-USD…)."
        if env == "real":
            execute = False  # regla dura: el entorno real no opera
        with self.lock:
            if self.runner.get("running"):
                return False, "Ya hay una ronda en curso."
            self.runner = {"running": True, "symbols": symbols, "execute": execute, "env": env, "lab_only": lab_only,
                           "started": time.time(), "error": None}
        threading.Thread(target=self._work, args=(symbols, execute, env, lab_only), daemon=True).start()
        return True, "Ronda iniciada."

    def _work(self, symbols: list[str], execute: bool, env: str, lab_only: bool = False) -> None:
        err = None
        try:
            settings = dataclasses.replace(self.settings, state_dir=str(self.state_dir(env)))
            desk = TradingDesk(settings, market=SyntheticMarket() if self.demo else YahooMarket())
            desk.lab(symbols) if lab_only else desk.run(symbols, execute=execute)
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
        with self.lock:
            self.runner = {**self.runner, "running": False, "error": err, "finished": time.time()}


def make_handler(office: Office):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode(), "application/json; charset=utf-8")

        def do_GET(self):
            url = urlparse(self.path)
            if url.path in ("/", "/index.html"):
                html = resources.files("tradingdesk").joinpath("static/index.html").read_bytes()
                self._send(200, html, "text/html; charset=utf-8")
            elif url.path == "/api/lab":
                q = parse_qs(url.query)
                env = (q.get("env") or ["paper"])[0]
                sym = (q.get("symbol") or [""])[0].upper()
                path = office.state_dir(env if env in ENVS else "paper") / "lab.json"
                data = json.loads(path.read_text()) if path.exists() else {}
                self._json(200 if sym in data else 404, data.get(sym, {"ok": False, "message": "Sin laboratorio para ese símbolo."}))
            elif url.path == "/api/state":
                env = (parse_qs(url.query).get("env") or ["paper"])[0]
                env = env if env in ENVS else "paper"
                st = build_state(office.state_dir(env), dict(office.runner))
                st["env"] = env
                st["mode"] = "demo" if office.demo else "mercado real (Yahoo)"
                if env == "real":
                    st["real"] = summarize(office.store.load())
                self._json(200, st)
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            # Mitiga CSRF/DNS-rebinding: solo JSON y solo Host local.
            if "application/json" not in (self.headers.get("Content-Type") or ""):
                return self._json(415, {"ok": False, "message": "Content-Type debe ser application/json"})
            if (self.headers.get("Host") or "").split(":")[0] not in ("127.0.0.1", "localhost"):
                return self._json(403, {"ok": False, "message": "Host no permitido"})
            try:
                n = min(int(self.headers.get("Content-Length") or 0), 10_000)
                body = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except (ValueError, TypeError):
                return self._json(400, {"ok": False, "message": "Petición inválida."})

            path = urlparse(self.path).path
            try:
                if path == "/api/run":
                    ok, msg = office.start_run(list(body.get("symbols", [])), bool(body.get("execute", False)),
                                               str(body.get("env", "paper")))
                    return self._json(200 if ok else 400, {"ok": ok, "message": msg})
                if path == "/api/lab":
                    ok, msg = office.start_run(list(body.get("symbols", [])), False, str(body.get("env", "paper")), lab_only=True)
                    return self._json(200 if ok else 400, {"ok": ok, "message": msg})
                if path == "/api/real/sync":
                    n_pos = sync_bitso(office.store, str(body.get("account", "bitso")))
                    return self._json(200, {"ok": True, "message": f"Bitso sincronizado: {n_pos} posiciones."})
                if path == "/api/real/account":
                    office.store.add_account(body.get("name"), body.get("currency"))
                    return self._json(200, {"ok": True, "message": "Cuenta creada."})
                if path == "/api/real/holding":
                    office.store.add_holding(str(body.get("account", "")), body)
                    return self._json(200, {"ok": True, "message": "Posición guardada."})
                if path == "/api/real/delete":
                    if body.get("holding"):
                        office.store.delete_holding(str(body.get("account", "")), str(body["holding"]))
                    else:
                        office.store.delete_account(str(body.get("account", "")))
                    return self._json(200, {"ok": True, "message": "Eliminado."})
            except AccountError as e:
                return self._json(400, {"ok": False, "message": str(e)})
            self._send(404, b"not found", "text/plain")

    return H


def make_server(settings: Settings, host: str = "127.0.0.1", port: int = 8765, demo: bool = False):
    """Crea el servidor sin arrancarlo. port=0 => puerto libre elegido por el sistema."""
    office = Office(settings, demo=demo)
    return ThreadingHTTPServer((host, port), make_handler(office)), office


def serve(settings: Settings, host: str = "127.0.0.1", port: int = 8765, demo: bool = False) -> None:
    httpd, _ = make_server(settings, host, port, demo)
    print(f"Oficina de trading en http://{host}:{port}  ({'DEMO sintético' if demo else 'mercado real'}) — Ctrl+C para salir")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
