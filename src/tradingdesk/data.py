from __future__ import annotations

import math
from typing import Any, Protocol

import numpy as np
import pandas as pd


def _f(x: Any) -> float | None:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else round(x, 4)


def compute_indicators(df: pd.DataFrame) -> dict[str, float | None]:
    """Indicadores técnicos clásicos a partir de OHLC diario."""
    close, high, low = df["Close"], df["High"], df["Low"]

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))

    ema12, ema26 = close.ewm(span=12).mean(), close.ewm(span=26).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9).mean()

    prev = close.shift()
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()

    n = len(close)
    return {
        "price": _f(close.iloc[-1]),
        "sma20": _f(close.rolling(20).mean().iloc[-1]),
        "sma50": _f(close.rolling(50).mean().iloc[-1]),
        "sma200": _f(close.rolling(200).mean().iloc[-1]),
        "rsi14": _f(rsi.iloc[-1]),
        "macd": _f(macd.iloc[-1]),
        "macd_signal": _f(macd_signal.iloc[-1]),
        "atr14": _f(atr.iloc[-1]),
        "ret_1m_pct": _f((close.iloc[-1] / close.iloc[-22] - 1) * 100) if n > 22 else None,
        "ret_3m_pct": _f((close.iloc[-1] / close.iloc[-64] - 1) * 100) if n > 64 else None,
        "high_52w": _f(high.tail(252).max()),
        "low_52w": _f(low.tail(252).min()),
    }


class MarketData(Protocol):
    def get_context(self, symbol: str) -> dict[str, Any]: ...


class YahooMarket:
    """Datos gratuitos vía yfinance (retrasados; suficiente para paper trading)."""

    FUNDAMENTAL_KEYS = [
        "shortName", "sector", "industry", "marketCap", "trailingPE", "forwardPE",
        "priceToBook", "profitMargins", "revenueGrowth", "earningsGrowth",
        "debtToEquity", "returnOnEquity", "dividendYield", "beta",
        "recommendationMean", "targetMeanPrice",
    ]

    def get_context(self, symbol: str) -> dict[str, Any]:
        import yfinance as yf

        t = yf.Ticker(symbol)
        hist = t.history(period="5y", auto_adjust=True)
        if hist.empty:
            raise ValueError(f"Sin datos de precio para {symbol}")
        try:
            info = t.info or {}
        except Exception:
            info = {}
        try:
            raw_news = t.news or []
        except Exception:
            raw_news = []
        headlines = []
        for n in raw_news[:10]:
            c = n.get("content", n)
            title = c.get("title")
            if title:
                headlines.append(title)
        ind = compute_indicators(hist)
        return {
            "symbol": symbol.upper(),
            "price": ind["price"],
            "atr": ind["atr14"],
            "indicators": ind,
            "fundamentals": {k: info.get(k) for k in self.FUNDAMENTAL_KEYS if info.get(k) is not None},
            "headlines": headlines,
            "history": hist,
        }


class SyntheticMarket:
    """Mercado simulado (sin red) para demos y pruebas. Determinista por símbolo."""

    def get_context(self, symbol: str) -> dict[str, Any]:
        import zlib

        seed = zlib.crc32(symbol.upper().encode())
        rng = np.random.default_rng(seed)
        drift = rng.uniform(-0.0008, 0.0025)
        n = 1260
        cycle = 0.0015 * np.sin(2 * np.pi * np.arange(n) / rng.uniform(300, 500))  # tendencias que suben y bajan
        close = rng.uniform(40, 400) * np.cumprod(1 + drift + cycle + rng.normal(0, 0.014, n))
        df = pd.DataFrame({"Close": close, "High": close * 1.012, "Low": close * 0.988},
                          index=pd.bdate_range(end="2026-11-20", periods=n))
        ind = compute_indicators(df)
        pe = round(float(rng.uniform(9, 45)), 1)
        return {
            "symbol": symbol.upper(),
            "price": ind["price"],
            "atr": ind["atr14"],
            "indicators": ind,
            "fundamentals": {"trailingPE": pe, "profitMargins": round(float(rng.uniform(0.02, 0.35)), 3),
                             "revenueGrowth": round(float(rng.uniform(-0.05, 0.3)), 3), "synthetic": True},
            "headlines": [f"[demo] {symbol.upper()} datos sintéticos"],
            "history": df,
        }
