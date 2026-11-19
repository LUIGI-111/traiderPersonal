from __future__ import annotations

import math
from dataclasses import dataclass

from .config import Settings


@dataclass
class Order:
    symbol: str
    side: str  # BUY | SELL
    qty: int
    price: float
    stop: float | None = None


class RiskManager:
    """Reglas duras, deterministas. Ningún LLM puede saltárselas."""

    def __init__(self, s: Settings):
        self.s = s

    def evaluate(
        self,
        *,
        symbol: str,
        action: str,
        confidence: float,
        price: float,
        atr: float | None,
        equity: float,
        cash: float,
        position_qty: int,
        invested_value: float,
        peak_equity: float,
    ) -> tuple[Order | None, str]:
        s = self.s
        if action == "HOLD":
            return None, "HOLD: sin orden."
        if action == "SELL":
            if position_qty <= 0:
                return None, "Rechazado: no hay posición que vender (no se permiten cortos)."
            return Order(symbol, "SELL", position_qty, price), "Cierre de posición."

        # --- BUY ---
        if confidence < s.min_confidence:
            return None, f"Rechazado: confianza {confidence:.2f} < mínimo {s.min_confidence:.2f}."
        if peak_equity > 0 and (peak_equity - equity) / peak_equity >= s.max_drawdown_pct:
            return None, "Rechazado: kill-switch por drawdown máximo."
        if not atr or atr <= 0 or price <= 0:
            return None, "Rechazado: sin ATR/precio válido para dimensionar riesgo."

        stop_dist = atr * s.stop_atr_mult
        qty_risk = (equity * s.risk_per_trade_pct) / stop_dist
        qty_position = (equity * s.max_position_pct - position_qty * price) / price
        qty_exposure = (equity * s.max_exposure_pct - invested_value) / price
        qty_cash = cash / price
        qty = math.floor(min(qty_risk, qty_position, qty_exposure, qty_cash))
        if qty < 1:
            return None, "Rechazado: los límites de riesgo/capital no permiten ni 1 acción."
        stop = round(price - stop_dist, 2)
        return Order(symbol, "BUY", qty, price, stop), f"Aprobado: {qty} acc., stop {stop}."
