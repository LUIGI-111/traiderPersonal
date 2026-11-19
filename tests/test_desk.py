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
    assert {a["key"] for a in st["agents"]} >= {"technical", "risk_manager"}
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
