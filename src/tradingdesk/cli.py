from __future__ import annotations

import argparse
import json

from .config import Settings
from .desk import TradingDesk


def _print_result(r: dict) -> None:
    if "error" in r:
        print(f"\n== {r['symbol']} == ERROR: {r['error']}")
        return
    d = r["decision"]
    print(f"\n== {r['symbol']} @ {r['price']} ==")
    for s in r["signals"]:
        print(f"  {s['agent']:<12} {s['action']:<5} conf={s['confidence']:.2f}  {s['rationale']}")
    print(f"  -> GESTOR     {d['action']:<5} conf={d['confidence']:.2f}  {d['rationale']}")
    print(f"  -> RIESGO     {r['risk_verdict']}")
    print(f"  -> EJECUTADA  {'SÍ (paper)' if r['executed'] else 'no'}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="tradingdesk", description="Mesa de trading multi-agente (paper trading)")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, helptext in (("analyze", "Analiza sin operar"), ("run", "Analiza y ejecuta en paper trading")):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("symbols", nargs="+", help="Tickers, p.ej. AAPL MSFT NVDA")
    pf = sub.add_parser("portfolio", help="Estado del portafolio simulado")
    pf.add_argument("--json", action="store_true")
    sv = sub.add_parser("serve", help="Abre la oficina: panel web local para ver qué hacen los agentes")
    sv.add_argument("--port", type=int, default=8765)
    sv.add_argument("--demo", action="store_true", help="Datos sintéticos (sin red ni API key)")
    args = p.parse_args(argv)

    if args.cmd == "serve":
        from .server import serve

        serve(Settings.from_env(), port=args.port, demo=args.demo)
        return

    desk = TradingDesk(Settings.from_env())
    if not desk.llm.available:
        print("[aviso] Sin ANTHROPIC_API_KEY: usando heurísticas offline en vez de agentes Claude.")

    if args.cmd in ("analyze", "run"):
        for r in desk.run(args.symbols, execute=(args.cmd == "run")):
            _print_result(r)
    else:
        summ = desk.portfolio()
        if args.json:
            print(json.dumps(summ, indent=2))
        else:
            print(f"Equity: {summ['equity']:,.2f}  Efectivo: {summ['cash']:,.2f}  Máx. histórico: {summ['peak_equity']:,.2f}")
            for s, pos in summ["positions"].items():
                print(f"  {s}: {pos['qty']} @ {pos['avg_price']:.2f}  último={pos['last']}  P&L={pos['pnl']:+,.2f}  stop={pos['stop']}")


if __name__ == "__main__":
    main()
