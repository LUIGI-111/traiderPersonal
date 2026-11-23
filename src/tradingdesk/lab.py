"""Laboratorio: prueba cada estrategia contra la historia real y la compara con comprar y mantener."""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from .strategies import STRATEGIES, VOTERS, WARMUP

COST = 0.001  # 0,1% por cada entrada o salida (comisión + deslizamiento)


def _f(x: Any, nd: int = 4) -> float:
    x = float(x)
    return round(x, nd) if math.isfinite(x) else 0.0


def backtest(df: pd.DataFrame, pos: pd.Series, cost: float = COST, start: int = WARMUP) -> tuple[dict[str, Any], list]:
    """La señal del día t se ejecuta al día siguiente (shift 1): sin mirar el futuro."""
    ret = df["Close"].pct_change().fillna(0.0)
    held = pos.shift(1).fillna(0.0).iloc[start:]
    r = ret.iloc[start:]
    trades = held.diff().abs()
    trades.iloc[0] = held.iloc[0]
    sr = held * r - trades * cost
    eq = (1 + sr).cumprod() * 100

    n, years = len(eq), len(eq) / 252
    total = eq.iloc[-1] / 100 - 1
    cagr = (eq.iloc[-1] / 100) ** (1 / years) - 1 if eq.iloc[-1] > 0 else -1.0
    dd = (eq / eq.cummax() - 1).min()
    sd = sr.std()
    sharpe = sr.mean() / sd * np.sqrt(252) if sd and sd > 0 else 0.0

    entries = held.diff().fillna(held) == 1
    run_id = entries.cumsum()
    inside = held == 1
    runs = (1 + sr[inside]).groupby(run_id[inside]).prod() - 1
    idx = int(n * 0.7)

    step = max(1, -(-n // 120))  # techo: como mucho ~120 puntos por curva
    pts = [[eq.index[i].strftime("%Y-%m-%d"), round(float(eq.iloc[i]), 2)] for i in range(0, n, step)]
    if (n - 1) % step:
        pts.append([eq.index[-1].strftime("%Y-%m-%d"), round(float(eq.iloc[-1]), 2)])

    metrics = {
        "total_return": _f(total), "cagr": _f(cagr), "max_drawdown": _f(dd), "sharpe": _f(sharpe, 2),
        "trades": int(entries.sum()), "win_rate": _f((runs > 0).mean()) if len(runs) else None,
        "exposure": _f(held.mean()), "recent_return": _f(eq.iloc[-1] / eq.iloc[idx] - 1),
    }
    return metrics, pts


def run_lab(df: pd.DataFrame) -> dict[str, Any]:
    if len(df) < WARMUP + 60:
        raise ValueError("Historial insuficiente para evaluar las estrategias (se necesitan ~1 año y medio).")
    out: dict[str, Any] = {}
    for key, (name, desc, fn) in STRATEGIES.items():
        pos = fn(df)
        m, pts = backtest(df, pos)
        out[key] = {"name": name, "desc": desc, "signal": int(pos.iloc[-1]), **m, "equity": pts}

    # Peso de cada bot en la votación: según su Sharpe en ESTE activo (las que no funcionan casi no pesan).
    raw = {k: max(out[k]["sharpe"], 0.0) + 0.05 for k in VOTERS}
    tot = sum(raw.values())
    weights = {k: round(v / tot, 4) for k, v in raw.items()}

    best = max(VOTERS, key=lambda k: out[k]["sharpe"])
    b, bh = out[best], out["buy_hold"]
    beats, less_dd = b["total_return"] > bh["total_return"], b["max_drawdown"] > bh["max_drawdown"]
    tail = ("Superó a comprar y mantener en rentabilidad y en caída máxima." if beats and less_dd else
            "Superó en rentabilidad, pero con una caída máxima mayor." if beats else
            "No superó a comprar y mantener en rentabilidad, pero cayó menos." if less_dd else
            "No superó a comprar y mantener en ningún aspecto.")
    verdict = (f"Mejor regla en este activo: «{b['name']}» ({b['total_return']:+.0%}, caída máx. {b['max_drawdown']:.0%}). "
               f"Comprar y mantener: {bh['total_return']:+.0%}, caída máx. {bh['max_drawdown']:.0%}. {tail}")
    return {
        "start": out["buy_hold"]["equity"][0][0], "end": out["buy_hold"]["equity"][-1][0], "days": len(df) - WARMUP,
        "strategies": out, "weights": weights, "best": best, "verdict": verdict,
    }
