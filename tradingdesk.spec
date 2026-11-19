# PyInstaller: `pyinstaller --noconfirm tradingdesk.spec`  (compilar en cada sistema operativo)
from PyInstaller.utils.hooks import collect_all

datas, binaries, hidden = [("src/tradingdesk/static", "tradingdesk/static")], [], []
for pkg in ("yfinance", "curl_cffi", "certifi", "anthropic", "webview"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hidden += h
    except Exception:
        pass

a = Analysis(["run_desktop.py"], pathex=["src"], datas=datas, binaries=binaries, hiddenimports=hidden)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="TradingDesk", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="TradingDesk")
app = BUNDLE(coll, name="TradingDesk.app", bundle_identifier="local.tradingdesk") if __import__("sys").platform == "darwin" else None
