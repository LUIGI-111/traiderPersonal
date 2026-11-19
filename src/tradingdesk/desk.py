from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .agents import FundamentalAnalyst, NewsAnalyst, PortfolioManager, TechnicalAnalyst
from .broker import PaperBroker
from .config import Settings
from .data import MarketData, YahooMarket
from .llm import LLM
from .risk import RiskManager


class TradingDesk:
    """Orquesta: datos -> analistas (en paralelo) -> gestor -> riesgo -> broker."""

    def __init__(self, settings: Settings, market: MarketData | None = None, llm: LLM | None = None):
        self.s = settings
        self.market = market or YahooMarket()
        self.llm = llm or LLM(settings.model)
        self.analysts = [TechnicalAnalyst(self.llm), FundamentalAnalyst(self.llm), NewsAnalyst(self.llm)]
        self.pm = PortfolioManager(self.llm)
        self.risk = RiskManager(settings)
        state = Path(settings.state_dir)
        self.broker = PaperBroker(state / "paper_portfolio.json", settings.starting_cash)
        self.journal_path = state / "journal.jsonl"
        self._lock = threading.Lock()

    def _journal(self, entry: dict[str, Any]) -> None:
        entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **entry}
        with self._lock, self.journal_path.open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def run(self, symbols: list[str], execute: bool = True) -> list[dict[str, Any]]:
        symbols = [s.upper() for s in symbols]
        self._journal({"event": "run_start", "symbols": symbols, "execute": execute})
        contexts: dict[str, dict[str, Any]] = {}
        results: list[dict[str, Any]] = []
        for sym in symbols:
            try:
                contexts[sym] = self.market.get_context(sym)
            except Exception as e:
                results.append({"symbol": sym, "error": str(e)})
                self._journal({"event": "data_error", "symbol": sym, "error": str(e)[:200]})

        prices = {s: c["price"] for s, c in contexts.items()}
        # 1) stop-losses primero
        if execute:
            for o in self.broker.check_stops(prices):
                self._journal({"event": "stop_loss", "symbol": o.symbol, "qty": o.qty, "price": o.price})

        for sym, ctx in contexts.items():
            def work(a, sym=sym, ctx=ctx):
                self._journal({"event": "agent_start", "agent": a.name, "symbol": sym})
                sig = a.analyze(ctx)
                self._journal({"event": "agent_done", "agent": a.name, "symbol": sym, "signal": sig.to_dict()})
                return sig

            with ThreadPoolExecutor(max_workers=len(self.analysts)) as ex:
                signals = list(ex.map(work, self.analysts))
            equity = self.broker.equity(prices)
            portfolio_view = {
                "equity": round(equity, 2),
                "cash": round(self.broker.cash, 2),
                "position_qty": self.broker.qty(sym),
            }
            self._journal({"event": "agent_start", "agent": "portfolio_manager", "symbol": sym})
            decision = self.pm.decide(sym, signals, portfolio_view)
            self._journal({"event": "agent_done", "agent": "portfolio_manager", "symbol": sym, "signal": decision.to_dict()})
            self._journal({"event": "agent_start", "agent": "risk_manager", "symbol": sym})
            order, verdict = self.risk.evaluate(
                symbol=sym,
                action=decision.action,
                confidence=decision.confidence,
                price=ctx["price"],
                atr=ctx.get("atr"),
                equity=equity,
                cash=self.broker.cash,
                position_qty=self.broker.qty(sym),
                invested_value=self.broker.invested_value(prices),
                peak_equity=self.broker.peak_equity,
            )
            self._journal({"event": "agent_done", "agent": "risk_manager", "symbol": sym, "verdict": verdict})
            executed = False
            if order and execute:
                self.broker.execute(order, reason=decision.rationale)
                executed = True
                self._journal({"event": "trade", "symbol": sym, **order.__dict__})
            res = {
                "symbol": sym,
                "price": ctx["price"],
                "signals": [s.to_dict() for s in signals],
                "decision": decision.to_dict(),
                "risk_verdict": verdict,
                "order": order.__dict__ if order else None,
                "executed": executed,
            }
            self._journal({"event": "analysis", **res})
            results.append(res)
        self._journal({"event": "run_end", "symbols": symbols, "analyzed": len(contexts)})
        return results

    def portfolio(self, symbols: list[str] | None = None) -> dict[str, Any]:
        syms = list(self.broker.state["positions"]) + [s.upper() for s in (symbols or [])]
        prices = {}
        for s in set(syms):
            try:
                prices[s] = self.market.get_context(s)["price"]
            except Exception:
                pass
        return self.broker.summary(prices)
