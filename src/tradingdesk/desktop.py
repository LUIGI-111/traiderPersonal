"""App de escritorio: ventana nativa (pywebview) sobre el servidor local de la oficina."""
from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser
from pathlib import Path

from .config import Settings
from .server import make_server


def user_data_dir() -> Path:
    """Carpeta de datos por usuario (cartera, journal y .env) según el sistema operativo."""
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    d = base / "TradingDesk"
    d.mkdir(parents=True, exist_ok=True)
    return d


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="tradingdesk-desktop")
    ap.add_argument("--demo", action="store_true", help="Datos sintéticos (sin red ni API key)")
    ap.add_argument("--browser", action="store_true", help="Abrir en el navegador en vez de ventana nativa")
    args = ap.parse_args(argv)

    # .env y data/ viven en la carpeta de datos del usuario, no donde se lanzó el programa.
    data = user_data_dir()
    os.chdir(data)
    if sys.stdout is None or sys.stderr is None:  # ejecutable sin consola: guardar registro en archivo
        log = open(data / "desktop.log", "a", buffering=1, encoding="utf-8")
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
    settings = Settings.from_env()
    httpd, _ = make_server(settings, port=0, demo=args.demo)
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    try:
        if args.browser:
            raise ImportError
        import webview

        webview.create_window("Oficina de Trading", url, width=1360, height=900, min_size=(900, 600))
        webview.start()
    except Exception as e:  # sin pywebview o sin entorno gráfico: navegador como plan B
        if not args.browser:
            print(f"[aviso] Ventana nativa no disponible ({type(e).__name__}); abriendo el navegador.")
        webbrowser.open(url)
        print(f"Oficina en {url} — Ctrl+C para salir. Datos en: {Path.cwd()}")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
