"""Construye el estado de la 'oficina' (agentes, fichas por activo, actividad) leyendo el journal."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

AGENTS = [
    ("technical", "Analista Técnico", "Tendencia, momentum y volatilidad"),
    ("fundamental", "Analista Fundamental", "Valoración, calidad y crecimiento"),
    ("news", "Analista de Noticias", "Titulares y catalizadores"),
    ("portfolio_manager", "Gestor de Portafolio", "Sintetiza señales en una decisión"),
    ("risk_manager", "Gestor de Riesgo", "Reglas duras: tamaño, stops, límites"),
]


def read_journal(path: Path, limit: int = 3000) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines()[-limit:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def describe(e: dict[str, Any]) -> str:
    ev, sym = e.get("event"), e.get("symbol", "")
    if ev == "run_start":
        return f"Inicio de ronda: {', '.join(e.get('symbols', []))} ({'ejecutando' if e.get('execute') else 'solo análisis'})"
    if ev == "run_end":
        return f"Ronda terminada ({e.get('analyzed', 0)} activos analizados)"
    if ev == "agent_start":
        return f"{e['agent']} analizando {sym}…"
    if ev == "agent_done":
        s = e.get("signal")
        if s:
            return f"{e['agent']} → {s['action']} {sym} (conf. {s['confidence']:.0%}): {s['rationale']}"
        return f"{e['agent']}: {e.get('verdict', '')}"
    if ev == "trade":
        return f"{e['side']} {e['qty']} {sym} @ {e['price']}" + (f" (stop {e['stop']})" if e.get("stop") else "")
    if ev == "stop_loss":
        return f"STOP-LOSS: vendidas {e['qty']} {sym} @ {e['price']}"
    if ev == "analysis":
        return f"Decisión final {sym}: {e['decision']['action']} → {e['risk_verdict']}"
    if ev == "data_error":
        return f"Error de datos en {sym}: {e.get('error')}"
    return ev or "evento"


def build_state(state_dir: str | Path, runner: dict[str, Any] | None = None) -> dict[str, Any]:
    state_dir = Path(state_dir)
    events = read_journal(state_dir / "journal.jsonl")
    pf_path = state_dir / "paper_portfolio.json"
    pf = json.loads(pf_path.read_text()) if pf_path.exists() else {"cash": 0, "positions": {}, "peak_equity": 0, "trades": []}
    running = bool(runner and runner.get("running"))

    # --- agentes ---
    agents = []
    for key, title, role in AGENTS:
        mine = [e for e in events if e.get("agent") == key]
        done = [e for e in mine if e["event"] == "agent_done"]
        pending = len([e for e in mine if e["event"] == "agent_start"]) - len(done)
        last = done[-1] if done else None
        agents.append({
            "key": key, "title": title, "role": role,
            "status": "working" if running and pending > 0 else "idle",
            "tasks_done": len(done),
            "last_ts": last["ts"] if last else None,
            "last_symbol": last.get("symbol") if last else None,
            "last_output": (f"{last['signal']['action']} ({last['signal']['confidence']:.0%})" if last and last.get("signal")
                            else (last or {}).get("verdict")),
        })

    # --- fichas por activo (estilo CRM) ---
    analyses = [e for e in events if e["event"] == "analysis"]
    last_price: dict[str, float] = {}
    symbols: dict[str, dict[str, Any]] = {}
    for a in analyses:
        sym = a["symbol"]
        last_price[sym] = a["price"]
        card = symbols.setdefault(sym, {"symbol": sym, "history": []})
        card["history"].append({
            "ts": a["ts"], "price": a["price"], "action": a["decision"]["action"],
            "confidence": a["decision"]["confidence"], "rationale": a["decision"]["rationale"],
            "verdict": a["risk_verdict"], "executed": a["executed"],
            "signals": a["signals"],
        })
    for card in symbols.values():
        card["last"] = card["history"][-1]
        card["history"] = card["history"][-8:][::-1]
        card["analyses"] = sum(1 for a in analyses if a["symbol"] == card["symbol"])
        pos = pf["positions"].get(card["symbol"])
        card["position"] = pos
        card["pnl"] = round((last_price[card["symbol"]] - pos["avg_price"]) * pos["qty"], 2) if pos else None

    invested = sum(p["qty"] * last_price.get(s, p["avg_price"]) for s, p in pf["positions"].items())
    equity = pf["cash"] + invested
    approved = sum(1 for a in analyses if a["executed"])
    runs = [e for e in events if e["event"] == "run_start"]

    return {
        "running": running,
        "runner": runner or {},
        "agents": agents,
        "kpis": {
            "equity": round(equity, 2), "cash": round(pf["cash"], 2), "invested": round(invested, 2),
            "peak_equity": round(max(pf["peak_equity"], equity), 2),
            "positions": len(pf["positions"]), "runs": len(runs), "analyses": len(analyses),
            "trades": len(pf["trades"]), "approved": approved,
            "rejected": sum(1 for a in analyses if a["decision"]["action"] != "HOLD" and not a["executed"]),
        },
        "symbols": sorted(symbols.values(), key=lambda c: c["last"]["ts"], reverse=True),
        "feed": [{"ts": e["ts"], "event": e["event"], "text": describe(e)} for e in events[-80:]][::-1],
        "trades": pf["trades"][-30:][::-1],
    }
