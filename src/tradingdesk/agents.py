from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from .llm import LLM
from .strategies import VOTERS

ACTIONS = ("BUY", "SELL", "HOLD")

JSON_RULES = (
    'Responde SOLO con un objeto JSON: {"action": "BUY|SELL|HOLD", '
    '"confidence": número entre 0 y 1, "rationale": "máx. 3 frases"}. '
    "Sé escéptico: si la evidencia es débil o contradictoria, responde HOLD con baja confianza. "
    "Nunca inventes datos que no estén en la entrada."
)


@dataclass
class Signal:
    agent: str
    symbol: str
    action: str
    confidence: float
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean(agent: str, symbol: str, raw: dict[str, Any]) -> Signal:
    action = str(raw.get("action", "HOLD")).upper()
    if action not in ACTIONS:
        action = "HOLD"
    try:
        conf = max(0.0, min(1.0, float(raw.get("confidence", 0))))
    except (TypeError, ValueError):
        conf = 0.0
    return Signal(agent, symbol, action, conf, str(raw.get("rationale", ""))[:600])


class Agent:
    name = "agent"
    system = ""

    def __init__(self, llm: LLM | None = None):
        self.llm = llm

    def payload(self, ctx: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def heuristic(self, ctx: dict[str, Any]) -> Signal:
        """Fallback determinista (si falla la API)."""
        return Signal(self.name, ctx["symbol"], "HOLD", 0.0, "Sin análisis.")

    def analyze(self, ctx: dict[str, Any]) -> Signal:
        sym = ctx["symbol"]
        if self.llm is None or not self.llm.available:
            return self.heuristic(ctx)
        try:
            user = f"Activo: {sym}\nDatos:\n{json.dumps(self.payload(ctx), ensure_ascii=False, default=str)}"
            return _clean(self.name, sym, self.llm.complete_json(self.system + "\n" + JSON_RULES, user))
        except Exception as e:  # la mesa nunca debe caerse por un analista
            s = self.heuristic(ctx)
            s.rationale = f"[fallback por error: {type(e).__name__}: {str(e)[:300]}] {s.rationale}"
            return s


class StrategyBot(Agent):
    """Bot cuantitativo: aplica una regla clásica y reporta qué tan bien le ha ido en ESTE activo.
    No usa IA ni API: cálculo puro, sin costo por ejecución."""

    def __init__(self, key: str):
        super().__init__(None)
        self.key = self.name = key

    def analyze(self, ctx: dict[str, Any]) -> Signal:
        sym, lab = ctx["symbol"], ctx.get("lab")
        if not lab:
            return Signal(self.name, sym, "HOLD", 0.0, "Sin historial suficiente para evaluar esta regla.")
        s, bh = lab["strategies"][self.key], lab["strategies"]["buy_hold"]
        bullish = s["signal"] == 1
        conf = max(0.3, min(0.9, 0.5 + 0.2 * s["sharpe"]))
        txt = (f"{s['name']}: {'dentro' if bullish else 'fuera'} del mercado hoy. "
               f"Histórico en {sym}: {s['total_return']:+.0%} (comprar y mantener {bh['total_return']:+.0%}), "
               f"caída máx. {s['max_drawdown']:.0%}, Sharpe {s['sharpe']:.2f}.")
        return Signal(self.name, sym, "BUY" if bullish else "SELL", conf, txt)


class TechnicalAnalyst(Agent):
    name = "technical"
    system = (
        "Eres un analista técnico senior. Evalúas tendencia (SMA20/50/200), momentum (RSI, MACD), "
        "volatilidad (ATR) y posición dentro del rango de 52 semanas. Horizonte: swing de 2-8 semanas."
    )

    def payload(self, ctx):
        return ctx["indicators"]


class FundamentalAnalyst(Agent):
    name = "fundamental"
    system = (
        "Eres un analista fundamental. Evalúas valoración (PER, P/B), calidad (márgenes, ROE), "
        "crecimiento, deuda y consenso de analistas. Compara contra normas del sector cuando puedas."
    )

    def payload(self, ctx):
        return ctx["fundamentals"]


class NewsAnalyst(Agent):
    name = "news"
    system = (
        "Eres un analista de noticias y sentimiento. A partir de titulares recientes evalúas si hay "
        "catalizadores o riesgos materiales. Distingue ruido de información relevante."
    )

    def payload(self, ctx):
        return {"headlines": ctx["headlines"]}


class PortfolioManager:
    """Sintetiza las señales en una decisión única. Sin IA: voto ponderado por el desempeño histórico
    de cada bot en ese activo. Con IA opcional (use_llm) delega la síntesis en Claude."""

    name = "portfolio_manager"
    system = (
        "Eres el gestor de portafolio. Recibes señales de analistas y bots cuantitativos y el estado de la cartera. "
        "Pondera por calidad de la evidencia, no por mayoría simple; si hay desacuerdo fuerte, prefiere HOLD. "
        "SELL solo aplica si ya hay posición. Un gestor de riesgo independiente puede vetar o reducir tu orden."
    )
    LEGACY_WEIGHTS = {"technical": 0.4, "fundamental": 0.35, "news": 0.25}
    ENTER, EXIT = 0.5, -0.5  # umbrales del voto ponderado (-1..+1)

    def __init__(self, llm: LLM | None = None, use_llm: bool = False):
        self.llm, self.use_llm = llm, use_llm

    def heuristic(self, symbol: str, signals: list[Signal], view: dict[str, Any] | None = None,
                  weights: dict[str, float] | None = None) -> Signal:
        qty = (view or {}).get("position_qty", 0)
        quant = [s for s in signals if s.agent in VOTERS]
        if quant:
            w = {s.agent: (weights or {}).get(s.agent, 1 / len(quant)) for s in quant}
            tot = sum(w.values()) or 1.0
            score = sum(w[s.agent] * {"BUY": 1, "SELL": -1, "HOLD": 0}[s.action] for s in quant) / tot
            votes = ", ".join(f"{s.agent} {'↑' if s.action == 'BUY' else '↓' if s.action == 'SELL' else '·'}" for s in quant)
            conf = min(1.0, 0.5 + 0.5 * abs(score))
            info = f"Voto ponderado {score:+.2f} ({votes})."
            if score >= self.ENTER:
                if qty > 0:
                    return Signal(self.name, symbol, "HOLD", conf, f"{info} Señal alcista, pero ya hay posición abierta.")
                return Signal(self.name, symbol, "BUY", conf, f"{info} Los bots mejor puntuados en este activo coinciden: entrar.")
            if score <= self.EXIT:
                if qty > 0:
                    return Signal(self.name, symbol, "SELL", conf, f"{info} Los bots ven tendencia rota: salir.")
                return Signal(self.name, symbol, "HOLD", conf, f"{info} Señal bajista y sin posición: se queda fuera.")
            return Signal(self.name, symbol, "HOLD", 0.5, f"{info} Bots divididos: sin convicción.")

        score = sum(self.LEGACY_WEIGHTS.get(s.agent, 0.2) * s.confidence * {"BUY": 1, "SELL": -1, "HOLD": 0}[s.action]
                    for s in signals)
        if score > 0.2:
            return Signal(self.name, symbol, "BUY", min(1.0, score + 0.4), f"Voto ponderado {score:+.2f}.")
        if score < -0.2:
            return Signal(self.name, symbol, "SELL", min(1.0, -score + 0.4), f"Voto ponderado {score:+.2f}.")
        return Signal(self.name, symbol, "HOLD", 0.5, f"Voto ponderado {score:+.2f}: sin convicción.")

    def decide(self, symbol: str, signals: list[Signal], portfolio_view: dict[str, Any],
               weights: dict[str, float] | None = None) -> Signal:
        if not (self.use_llm and self.llm is not None and self.llm.available):
            return self.heuristic(symbol, signals, portfolio_view, weights)
        try:
            user = json.dumps(
                {"symbol": symbol, "signals": [s.to_dict() for s in signals], "portfolio": portfolio_view,
                 "pesos_historicos_de_los_bots": weights},
                ensure_ascii=False, default=str,
            )
            return _clean(self.name, symbol, self.llm.complete_json(self.system + "\n" + JSON_RULES, user))
        except Exception as e:
            s = self.heuristic(symbol, signals, portfolio_view, weights)
            s.rationale = f"[fallback por error: {type(e).__name__}: {str(e)[:300]}] {s.rationale}"
            return s
