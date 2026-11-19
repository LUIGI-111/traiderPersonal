from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .risk import Order


class PaperBroker:
    """Broker simulado con estado persistente en JSON. Sin dinero real."""

    def __init__(self, state_file: str | Path, starting_cash: float):
        self.path = Path(state_file)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.state = json.loads(self.path.read_text())
        else:
            self.state = {
                "cash": float(starting_cash),
                "positions": {},  # sym -> {qty, avg_price, stop}
                "peak_equity": float(starting_cash),
                "trades": [],
            }
            self.save()

    def save(self) -> None:
        self.path.write_text(json.dumps(self.state, indent=2))

    @property
    def cash(self) -> float:
        return self.state["cash"]

    @property
    def peak_equity(self) -> float:
        return self.state["peak_equity"]

    def qty(self, symbol: str) -> int:
        return self.state["positions"].get(symbol, {}).get("qty", 0)

    def invested_value(self, prices: dict[str, float]) -> float:
        return sum(p["qty"] * prices.get(sym, p["avg_price"]) for sym, p in self.state["positions"].items())

    def equity(self, prices: dict[str, float]) -> float:
        eq = self.cash + self.invested_value(prices)
        if eq > self.state["peak_equity"]:
            self.state["peak_equity"] = eq
        return eq

    def execute(self, o: Order, reason: str = "") -> None:
        pos = self.state["positions"]
        if o.side == "BUY":
            cost = o.qty * o.price
            if cost > self.cash + 1e-9:
                raise ValueError("Efectivo insuficiente")
            cur = pos.get(o.symbol, {"qty": 0, "avg_price": 0.0, "stop": None})
            new_qty = cur["qty"] + o.qty
            cur["avg_price"] = (cur["qty"] * cur["avg_price"] + cost) / new_qty
            cur["qty"], cur["stop"] = new_qty, o.stop
            pos[o.symbol] = cur
            self.state["cash"] -= cost
        else:
            cur = pos.get(o.symbol)
            if not cur or cur["qty"] < o.qty:
                raise ValueError("Posición insuficiente")
            self.state["cash"] += o.qty * o.price
            cur["qty"] -= o.qty
            if cur["qty"] == 0:
                del pos[o.symbol]
        self.state["trades"].append(
            {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "symbol": o.symbol, "side": o.side, "qty": o.qty, "price": o.price,
                "stop": o.stop, "reason": reason,
            }
        )
        self.save()

    def check_stops(self, prices: dict[str, float]) -> list[Order]:
        """Cierra posiciones cuyo precio tocó el stop-loss."""
        closed = []
        for sym, p in list(self.state["positions"].items()):
            px = prices.get(sym)
            if px is not None and p.get("stop") is not None and px <= p["stop"]:
                o = Order(sym, "SELL", p["qty"], px)
                self.execute(o, reason="stop-loss")
                closed.append(o)
        return closed

    def summary(self, prices: dict[str, float]) -> dict[str, Any]:
        eq = self.equity(prices)
        self.save()
        return {
            "cash": round(self.cash, 2),
            "equity": round(eq, 2),
            "peak_equity": round(self.peak_equity, 2),
            "positions": {
                s: {**p, "last": prices.get(s), "pnl": round((prices.get(s, p["avg_price"]) - p["avg_price"]) * p["qty"], 2)}
                for s, p in self.state["positions"].items()
            },
        }
