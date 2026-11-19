from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from .llm import LLM

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

    def __init__(self, llm: LLM):
        self.llm = llm

    def payload(self, ctx: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def heuristic(self, ctx: dict[str, Any]) -> Signal:
        """Fallback determinista (modo offline o si falla la API)."""
        return Signal(self.name, ctx["symbol"], "HOLD", 0.0, "Sin análisis (offline).")

    def analyze(self, ctx: dict[str, Any]) -> Signal:
        sym = ctx["symbol"]
        if not self.llm.available:
            return self.heuristic(ctx)
        try:
            user = f"Activo: {sym}\nDatos:\n{json.dumps(self.payload(ctx), ensure_ascii=False, default=str)}"
            return _clean(self.name, sym, self.llm.complete_json(self.system + "\n" + JSON_RULES, user))
        except Exception as e:  # la mesa nunca debe caerse por un analista
            s = self.heuristic(ctx)
            s.rationale = f"[fallback por error: {type(e).__name__}] {s.rationale}"
            return s


class TechnicalAnalyst(Agent):
    name = "technical"
    system = (
        "Eres un analista técnico senior. Evalúas tendencia (SMA20/50/200), momentum (RSI, MACD), "
        "volatilidad (ATR) y posición dentro del rango de 52 semanas. Horizonte: swing de 2-8 semanas."
    )

    def payload(self, ctx):
        return ctx["indicators"]

    def heuristic(self, ctx):
        i = ctx["indicators"]
        price, sma50, sma200, rsi = i.get("price"), i.get("sma50"), i.get("sma200"), i.get("rsi14")
        if None in (price, sma50, rsi):
            return Signal(self.name, ctx["symbol"], "HOLD", 0.0, "Datos insuficientes.")
        macd_up = (i.get("macd") or 0) > (i.get("macd_signal") or 0)
        uptrend = price > sma50 and (sma200 is None or sma50 > sma200)
        if uptrend and macd_up and rsi < 70:
            return Signal(self.name, ctx["symbol"], "BUY", 0.65, f"Tendencia alcista, MACD positivo, RSI {rsi:.0f}.")
        if price < sma50 and not macd_up:
            return Signal(self.name, ctx["symbol"], "SELL", 0.6, f"Bajo SMA50 con MACD negativo, RSI {rsi:.0f}.")
        return Signal(self.name, ctx["symbol"], "HOLD", 0.4, "Señales mixtas.")


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
    """Sintetiza las señales de los analistas en una decisión única."""

    name = "portfolio_manager"
    system = (
        "Eres el gestor de portafolio. Recibes señales de analistas (técnico, fundamental, noticias) y "
        "el estado de la cartera. Pondera por calidad de la evidencia, no por mayoría simple; "
        "si hay desacuerdo fuerte, prefiere HOLD. SELL solo aplica si ya hay posición. "
        "Un gestor de riesgo independiente puede vetar o reducir tu orden."
    )

    WEIGHTS = {"technical": 0.4, "fundamental": 0.35, "news": 0.25}

    def __init__(self, llm: LLM):
        self.llm = llm

    def heuristic(self, symbol: str, signals: list[Signal]) -> Signal:
        score = 0.0
        for s in signals:
            w = self.WEIGHTS.get(s.agent, 0.2)
            score += w * s.confidence * {"BUY": 1, "SELL": -1, "HOLD": 0}[s.action]
        if score > 0.2:
            return Signal(self.name, symbol, "BUY", min(1.0, score + 0.4), f"Voto ponderado {score:+.2f}.")
        if score < -0.2:
            return Signal(self.name, symbol, "SELL", min(1.0, -score + 0.4), f"Voto ponderado {score:+.2f}.")
        return Signal(self.name, symbol, "HOLD", 0.5, f"Voto ponderado {score:+.2f}: sin convicción.")

    def decide(self, symbol: str, signals: list[Signal], portfolio_view: dict[str, Any]) -> Signal:
        if not self.llm.available:
            return self.heuristic(symbol, signals)
        try:
            user = json.dumps(
                {"symbol": symbol, "signals": [s.to_dict() for s in signals], "portfolio": portfolio_view},
                ensure_ascii=False,
                default=str,
            )
            return _clean(self.name, symbol, self.llm.complete_json(self.system + "\n" + JSON_RULES, user))
        except Exception as e:
            s = self.heuristic(symbol, signals)
            s.rationale = f"[fallback por error: {type(e).__name__}] {s.rationale}"
            return s
