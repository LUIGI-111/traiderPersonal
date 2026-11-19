from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources

from .config import Settings
from .data import SyntheticMarket, YahooMarket
from .desk import TradingDesk
from .office import build_state

TICKER = re.compile(r"^[A-Z0-9.\-]{1,10}$")


class Office:
    """Estado compartido del servidor: lanza rondas en segundo plano (una a la vez)."""

    def __init__(self, settings: Settings, demo: bool = False):
        self.settings, self.demo = settings, demo
        self.lock = threading.Lock()
        self.runner: dict = {"running": False}

    def start_run(self, symbols: list[str], execute: bool) -> tuple[bool, str]:
        symbols = [s.strip().upper() for s in symbols if s.strip()]
        if not symbols or len(symbols) > 20 or not all(TICKER.match(s) for s in symbols):
            return False, "Tickers inválidos (máx. 20, formato AAPL, BRK-B…)."
        with self.lock:
            if self.runner.get("running"):
                return False, "Ya hay una ronda en curso."
            self.runner = {"running": True, "symbols": symbols, "execute": execute, "started": time.time(), "error": None}
        threading.Thread(target=self._work, args=(symbols, execute), daemon=True).start()
        return True, "Ronda iniciada."

    def _work(self, symbols: list[str], execute: bool) -> None:
        err = None
        try:
            desk = TradingDesk(self.settings, market=SyntheticMarket() if self.demo else YahooMarket())
            desk.run(symbols, execute=execute)
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
        with self.lock:
            self.runner = {**self.runner, "running": False, "error": err, "finished": time.time()}


def make_handler(office: Office):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silencioso
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
            if self.path in ("/", "/index.html"):
                html = resources.files("tradingdesk").joinpath("static/index.html").read_bytes()
                self._send(200, html, "text/html; charset=utf-8")
            elif self.path == "/api/state":
                st = build_state(office.settings.state_dir, dict(office.runner))
                st["mode"] = "demo" if office.demo else "mercado real (Yahoo)"
                self._json(200, st)
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path != "/api/run":
                return self._send(404, b"not found", "text/plain")
            # Mitiga CSRF/DNS-rebinding desde otras webs: solo aceptamos JSON del propio origen.
            if "application/json" not in (self.headers.get("Content-Type") or ""):
                return self._json(415, {"ok": False, "message": "Content-Type debe ser application/json"})
            host = (self.headers.get("Host") or "").split(":")[0]
            if host not in ("127.0.0.1", "localhost"):
                return self._json(403, {"ok": False, "message": "Host no permitido"})
            try:
                n = min(int(self.headers.get("Content-Length") or 0), 10_000)
                body = json.loads(self.rfile.read(n) or b"{}")
                ok, msg = office.start_run(list(body.get("symbols", [])), bool(body.get("execute", False)))
            except (ValueError, TypeError):
                ok, msg = False, "Petición inválida."
            self._json(200 if ok else 400, {"ok": ok, "message": msg})

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
