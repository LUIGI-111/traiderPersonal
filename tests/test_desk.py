import numpy as np
import pandas as pd
import pytest

from tradingdesk.agents import Signal
from tradingdesk.broker import PaperBroker
from tradingdesk.config import Settings
from tradingdesk.data import compute_indicators
from tradingdesk.desk import TradingDesk
from tradingdesk.llm import LLM, parse_json
from tradingdesk.risk import Order, RiskManager


def make_df(n=300, drift=0.001):
    rng = np.random.default_rng(1)
    close = 100 * np.cumprod(1 + drift + rng.normal(0, 0.01, n))
    return pd.DataFrame({"Close": close, "High": close * 1.01, "Low": close * 0.99})


class FakeMarket:
    def __init__(self, drift=0.002):
        self.ind = compute_indicators(make_df(drift=drift))

    def get_context(self, symbol):
        return {"symbol": symbol, "price": self.ind["price"], "atr": self.ind["atr14"],
                "indicators": self.ind, "fundamentals": {}, "headlines": []}


def settings(tmp_path):
    return Settings(state_dir=str(tmp_path))


def offline_llm():
    llm = LLM("x", api_key=None)
    llm.client = None
    return llm


def test_indicators_sane():
    ind = compute_indicators(make_df())
    assert ind["sma50"] and ind["rsi14"] and 0 <= ind["rsi14"] <= 100
    assert ind["atr14"] > 0


def test_parse_json_with_noise():
    assert parse_json('claro:\n```json\n{"action":"BUY"}\n```')["action"] == "BUY"


def eval_buy(**kw):
    base = dict(symbol="X", action="BUY", confidence=0.8, price=100.0, atr=2.0, equity=100_000.0,
                cash=100_000.0, position_qty=0, invested_value=0.0, peak_equity=100_000.0)
    base.update(kw)
    return RiskManager(Settings()).evaluate(**base)


def test_risk_sizing_and_stop():
    order, _ = eval_buy()
    # riesgo 1% = 1000; stop 4 => 250 por riesgo; límite 5% => 50 acc.
    assert order.qty == 50 and order.stop == 96.0


def test_risk_rejects():
    assert eval_buy(confidence=0.3)[0] is None
    assert eval_buy(equity=85_000.0)[0] is None  # drawdown 15%
    assert eval_buy(atr=None)[0] is None
    assert eval_buy(action="SELL")[0] is None  # sin posición, sin cortos
    assert eval_buy(position_qty=50)[0] is None  # ya en el máximo por posición


def test_broker_roundtrip_and_stop(tmp_path):
    b = PaperBroker(tmp_path / "p.json", 10_000)
    b.execute(Order("X", "BUY", 10, 100.0, stop=95.0))
    assert b.cash == 9_000 and b.qty("X") == 10
    assert b.check_stops({"X": 96.0}) == []
    closed = b.check_stops({"X": 94.0})
    assert len(closed) == 1 and b.qty("X") == 0
    with pytest.raises(ValueError):
        b.execute(Order("X", "SELL", 1, 100.0))
    assert PaperBroker(tmp_path / "p.json", 1).cash == b.cash  # persiste


def test_desk_offline_end_to_end(tmp_path):
    desk = TradingDesk(settings(tmp_path), market=FakeMarket(), llm=offline_llm())
    res = desk.run(["AAA"])[0]
    assert res["decision"]["action"] in ("BUY", "SELL", "HOLD")
    assert (tmp_path / "journal.jsonl").exists()
    if res["executed"]:
        assert desk.broker.qty("AAA") == res["order"]["qty"]


def test_signal_clean_rejects_garbage():
    from tradingdesk.agents import _clean
    s = _clean("a", "X", {"action": "YOLO", "confidence": 9})
    assert s.action == "HOLD" and s.confidence == 1.0


def test_office_state_and_server(tmp_path):
    from tradingdesk.data import SyntheticMarket
    from tradingdesk.office import build_state
    from tradingdesk.server import Office

    desk = TradingDesk(settings(tmp_path), market=SyntheticMarket(), llm=offline_llm())
    desk.run(["AAA", "BBB", "CCC"])
    st = build_state(tmp_path)
    assert {a["key"] for a in st["agents"]} >= {"sma_cross", "momentum", "portfolio_manager", "risk_manager"}
    assert all(a["tasks_done"] == 3 for a in st["agents"])
    assert len(st["symbols"]) == 3 and st["kpis"]["analyses"] == 3
    assert st["feed"][0]["event"] == "run_end"

    o = Office(settings(tmp_path), demo=True)
    assert o.start_run(["bad ticker!"], False)[0] is False
    assert o.start_run([], False)[0] is False


def test_desktop_server_ephemeral_port(tmp_path):
    import json, threading, urllib.request
    from tradingdesk.server import make_server

    httpd, _ = make_server(settings(tmp_path), port=0, demo=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        assert b"Oficina de Trading" in urllib.request.urlopen(base + "/").read()
        assert json.load(urllib.request.urlopen(base + "/api/state"))["mode"] == "demo"
    finally:
        httpd.shutdown()


# ---------- entorno real / cuentas ----------
def test_bitso_signature_is_deterministic():
    from tradingdesk.bitso import sign
    # HMAC-SHA256("secret", "1000GET/api/v3/balance/")
    import hashlib, hmac
    assert sign("secret", "1000", "GET", "/api/v3/balance/") == hmac.new(
        b"secret", b"1000GET/api/v3/balance/", hashlib.sha256).hexdigest()


def test_accounts_manual_and_totals(tmp_path):
    from tradingdesk.accounts import AccountError, AccountStore, summarize
    s = AccountStore(tmp_path / "a.json")
    assert [a["id"] for a in s.load()["accounts"]] == ["bitso", "tyba"]
    s.add_holding("tyba", {"name": "Fondo Renta", "qty": 10, "price": 1500.5, "currency": "cop", "cost": 14000})
    sm = summarize(s.load())
    assert sm["totals"] == {"COP": 15005.0} and sm["accounts"][1]["holdings"][0]["pnl"] == pytest.approx(1005.0)
    with pytest.raises(AccountError):
        s.add_holding("tyba", {"name": "x", "qty": -1, "price": 1})
    with pytest.raises(AccountError):
        s.add_holding("bitso", {"name": "x", "qty": 1, "price": 1})  # bitso solo por sincronización


def test_sync_bitso_values_and_errors(tmp_path, monkeypatch):
    from tradingdesk.accounts import AccountError, AccountStore, sync_bitso
    s = AccountStore(tmp_path / "a.json")
    with pytest.raises(AccountError):
        monkeypatch.delenv("BITSO_API_KEY", raising=False)
        sync_bitso(s)
    monkeypatch.setenv("BITSO_API_KEY", "k"); monkeypatch.setenv("BITSO_API_SECRET", "s")
    fake = lambda k, sec: [{"currency": "btc", "total": "0.5"}, {"currency": "mxn", "total": "1000"}]
    px = lambda book: {"btc_mxn": 2_000_000.0}[book]
    assert sync_bitso(s, fetch=fake, price=px) == 2
    h = {x["symbol"]: x for x in s.load()["accounts"][0]["holdings"]}
    assert h["BTC"]["price"] == 2_000_000.0 and h["BTC"]["ticker"] == "BTC-USD"
    assert h["MXN"]["price"] == 1.0 and h["MXN"]["ticker"] is None


def test_real_env_never_executes(tmp_path):
    import time
    from tradingdesk.server import Office
    o = Office(settings(tmp_path), demo=True)
    ok, _ = o.start_run(["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"], execute=True, env="real")
    assert ok and o.runner["execute"] is False
    for _ in range(100):
        if not o.runner["running"]:
            break
        time.sleep(0.1)
    assert (tmp_path / "real" / "journal.jsonl").exists()
    assert not (tmp_path / "journal.jsonl").exists()          # el entorno paper no se tocó
    assert PaperBroker(tmp_path / "real" / "paper_portfolio.json", 1).state["trades"] == []
    assert o.start_run(["AAA"], False, env="otro")[0] is False


def test_dotenv_tolerates_windows_bom(tmp_path, monkeypatch):
    from tradingdesk.config import _load_dotenv
    monkeypatch.delenv("TD_TEST_KEY", raising=False)
    f = tmp_path / ".env"
    f.write_bytes("﻿TD_TEST_KEY=abc123\r\n".encode("utf-8"))
    _load_dotenv(f)
    import os
    assert os.environ["TD_TEST_KEY"] == "abc123"


def test_llm_warning_surfaces_api_error(tmp_path):
    from tradingdesk.data import SyntheticMarket
    from tradingdesk.office import build_state

    class Boom(LLM):
        def __init__(self): self.model, self.client = "x", object()
        def complete_json(self, *a, **k): raise RuntimeError("Your credit balance is too low")

    desk = TradingDesk(Settings(state_dir=str(tmp_path), use_llm=True), market=SyntheticMarket(), llm=Boom())
    desk.run(["AAA"])
    assert "credit balance is too low" in build_state(tmp_path)["llm_warning"]


# ---------- laboratorio / estrategias ----------
def synth_df(n=1260, seed=3, drift=0.0004):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + drift + rng.normal(0, 0.012, n))
    return pd.DataFrame({"Close": close, "High": close * 1.01, "Low": close * 0.99},
                        index=pd.bdate_range(end="2026-11-20", periods=n))


def test_backtest_has_no_lookahead():
    from tradingdesk.lab import backtest
    df = synth_df()
    cheat = (df["Close"].pct_change() > 0).astype(float)  # "sabe" si HOY sube; si se ejecutara hoy ganaría siempre
    m, _ = backtest(df, cheat)
    assert m["total_return"] < 1.0  # ejecutada al día siguiente no tiene ventaja: no explota


def test_buy_hold_matches_price_and_costs():
    from tradingdesk.lab import backtest
    from tradingdesk.strategies import WARMUP, buy_hold
    df = synth_df()
    m, pts = backtest(df, buy_hold(df))
    raw = df["Close"].iloc[-1] / df["Close"].iloc[WARMUP - 1] - 1
    assert abs(m["total_return"] - raw) < 0.02 and m["exposure"] == 1.0 and m["trades"] == 1
    assert pts[0][1] > 90 and len(pts) <= 125


def test_run_lab_structure_and_weights():
    from tradingdesk.lab import run_lab
    from tradingdesk.strategies import VOTERS
    lab = run_lab(synth_df())
    assert set(lab["strategies"]) == {"buy_hold", *VOTERS}
    assert abs(sum(lab["weights"].values()) - 1) < 0.01 and "buy_hold" not in lab["weights"]
    assert lab["verdict"] and lab["strategies"]["sma_cross"]["signal"] in (0, 1)
    with pytest.raises(ValueError):
        run_lab(synth_df(n=150))


def test_portfolio_manager_votes_with_position_awareness():
    from tradingdesk.agents import PortfolioManager, Signal
    pm = PortfolioManager(None, use_llm=False)
    up = [Signal(k, "X", "BUY", .8, "") for k in ("sma_cross", "momentum", "rsi_reversion", "breakout")]
    down = [Signal(k, "X", "SELL", .8, "") for k in ("sma_cross", "momentum", "rsi_reversion", "breakout")]
    assert pm.decide("X", up, {"position_qty": 0}).action == "BUY"
    assert pm.decide("X", up, {"position_qty": 5}).action == "HOLD"      # ya está dentro
    assert pm.decide("X", down, {"position_qty": 0}).action == "HOLD"    # sin posición no hay nada que vender
    assert pm.decide("X", down, {"position_qty": 5}).action == "SELL"
    split = up[:2] + down[:2]
    assert pm.decide("X", split, {"position_qty": 0}).action == "HOLD"
    # un bot con peso casi nulo no puede arrastrar la decisión
    w = {"sma_cross": .45, "momentum": .45, "rsi_reversion": .05, "breakout": .05}
    mixed = [up[0], up[1], down[2], down[3]]
    assert pm.decide("X", mixed, {"position_qty": 0}, w).action == "BUY"


def test_desk_run_populates_lab_and_needs_no_api(tmp_path, monkeypatch):
    from tradingdesk.data import SyntheticMarket
    from tradingdesk.office import build_state
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    desk = TradingDesk(settings(tmp_path), market=SyntheticMarket())
    assert desk.llm is None and len(desk.analysts) == 4
    desk.run(["AAA", "BBB"], execute=True)
    st = build_state(tmp_path)
    assert set(st["lab"]) == {"AAA", "BBB"} and st["llm_warning"] is None
    full = __import__("json").loads((tmp_path / "lab.json").read_text())
    assert len(full["AAA"]["strategies"]["buy_hold"]["equity"]) > 10
