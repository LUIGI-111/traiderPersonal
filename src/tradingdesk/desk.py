from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .agents import FundamentalAnalyst, NewsAnalyst, PortfolioManager, StrategyBot, TechnicalAnalyst
from .broker import PaperBroker
from .config import Settings
from .data import MarketData, YahooMarket
from .lab import run_lab
from .llm import LLM
from .risk import RiskManager
from .strategies import VOTERS, WARMUP


class TradingDesk:
    """Orquesta: datos -> laboratorio -> bots (en paralelo) -> gestor -> riesgo -> broker.

    Por defecto es 100% cuantitativo (sin costo de API). Con TD_USE_LLM=1 y una clave válida se suman
    analistas de Claude (técnico, fundamental, noticias) y Claude sintetiza la decisión."""

    def __init__(self, settings: Settings, market: MarketData | None = None, llm: LLM | None = None):
        self.s = settings
        self.market = market or YahooMarket()
        self.llm = llm or (LLM(settings.model) if settings.use_llm else None)
        use_ai = bool(settings.use_llm and self.llm is not None and self.llm.available)
        self.analysts = [StrategyBot(k) for k in VOTERS]
        if use_ai:
            self.analysts += [TechnicalAnalyst(self.llm), FundamentalAnalyst(self.llm), NewsAnalyst(self.llm)]
        self.pm = PortfolioManager(self.llm, use_llm=use_ai)
        self.risk = RiskManager(settings)
        state = Path(settings.state_dir)
        state.mkdir(parents=True, exist_ok=True)
        self.broker = PaperBroker(state / "paper_portfolio.json", settings.starting_cash)
        self.journal_path = state / "journal.jsonl"
        self.lab_path = state / "lab.json"
        self._lock = threading.Lock()

    # ---------- registro ----------
    def _journal(self, entry: dict[str, Any]) -> None:
        entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **entry}
        with self._lock, self.journal_path.open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def _save_lab(self, symbol: str, res: dict[str, Any]) -> None:
        with self._lock:
            data = json.loads(self.lab_path.read_text()) if self.lab_path.exists() else {}
            data[symbol] = {"computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **res}
            tmp = self.lab_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False))
            tmp.replace(self.lab_path)

    def _lab_for(self, sym: str, ctx: dict[str, Any]) -> dict[str, Any] | None:
        hist = ctx.get("history")
        if hist is None or len(hist) < WARMUP + 60:
            return None
        res = run_lab(hist)
        self._save_lab(sym, res)
        self._journal({"event": "lab", "symbol": sym, "verdict": res["verdict"]})
        return res

    # ---------- solo laboratorio ----------
    def lab(self, symbols: list[str]) -> list[dict[str, Any]]:
        symbols = [s.upper() for s in symbols]
        self._journal({"event": "run_start", "symbols": symbols, "execute": False, "lab_only": True})
        out = []
        for sym in symbols:
            try:
                ctx = self.market.get_context(sym)
                res = self._lab_for(sym, ctx)
                if res is None:
                    raise ValueError("historial insuficiente")
                out.append({"symbol": sym, "verdict": res["verdict"]})
            except Exception as e:  # noqa: BLE001
                out.append({"symbol": sym, "error": str(e)})
                self._journal({"event": "data_error", "symbol": sym, "error": str(e)[:200]})
        self._journal({"event": "run_end", "symbols": symbols, "analyzed": len([o for o in out if "error" not in o])})
        return out

    # ---------- ronda completa ----------
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
        if execute:  # 1) stop-losses primero
            for o in self.broker.check_stops(prices):
                self._journal({"event": "stop_loss", "symbol": o.symbol, "qty": o.qty, "price": o.price})

        for sym, ctx in contexts.items():
            lab_res = self._lab_for(sym, ctx)
            ctx["lab"] = lab_res
            weights = lab_res["weights"] if lab_res else None

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
            decision = self.pm.decide(sym, signals, portfolio_view, weights)
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
