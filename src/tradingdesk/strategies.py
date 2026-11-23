"""Estrategias clásicas, solo largo (long-only), con parámetros FIJOS y conocidos (no optimizados,
para no sobreajustar a la historia). Cada función devuelve una serie 0/1: 1 = estar dentro del
mercado según la información disponible al cierre de ese día."""
from __future__ import annotations

import numpy as np
import pandas as pd

WARMUP = 200  # días de calentamiento: todas las reglas se comparan sobre la misma ventana


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = (-delta.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + gain / loss.replace(0, np.nan))


def buy_hold(df: pd.DataFrame) -> pd.Series:
    return pd.Series(1.0, index=df.index)


def sma_cross(df: pd.DataFrame) -> pd.Series:
    c = df["Close"]
    return (c.rolling(50).mean() > c.rolling(200).mean()).astype(float)


def momentum(df: pd.DataFrame) -> pd.Series:
    return (df["Close"].pct_change(126) > 0).astype(float)


def rsi_reversion(df: pd.DataFrame) -> pd.Series:
    rsi = _rsi(df["Close"]).to_numpy()
    pos, state = np.zeros(len(rsi)), 0.0
    for i, x in enumerate(rsi):
        if not np.isnan(x):
            if state == 0.0 and x < 30:
                state = 1.0
            elif state == 1.0 and x > 55:
                state = 0.0
        pos[i] = state
    return pd.Series(pos, index=df.index)


def breakout(df: pd.DataFrame) -> pd.Series:
    """Canal de Donchian (estilo 'tortugas'): entra al superar el máximo de 55 días, sale bajo el mínimo de 20."""
    close = df["Close"].to_numpy()
    hh = df["High"].rolling(55).max().shift(1).to_numpy()
    ll = df["Low"].rolling(20).min().shift(1).to_numpy()
    pos, state = np.zeros(len(close)), 0.0
    for i in range(len(close)):
        if state == 0.0 and not np.isnan(hh[i]) and close[i] > hh[i]:
            state = 1.0
        elif state == 1.0 and not np.isnan(ll[i]) and close[i] < ll[i]:
            state = 0.0
        pos[i] = state
    return pd.Series(pos, index=df.index)


# clave -> (nombre, descripción, función). buy_hold es la referencia, no vota.
STRATEGIES = {
    "buy_hold": ("Comprar y mantener", "Referencia: comprar el primer día y no vender nunca.", buy_hold),
    "sma_cross": ("Cruce de medias 50/200", "Dentro cuando la media de 50 días supera la de 200 (tendencia alcista).", sma_cross),
    "momentum": ("Momentum 6 meses", "Dentro mientras la rentabilidad de los últimos 126 días sea positiva.", momentum),
    "rsi_reversion": ("Reversión RSI", "Compra cuando el RSI cae bajo 30 (sobreventa) y vende al superar 55.", rsi_reversion),
    "breakout": ("Ruptura de 55 días", "Entra al superar el máximo de 55 días; sale al caer bajo el mínimo de 20.", breakout),
}
VOTERS = [k for k in STRATEGIES if k != "buy_hold"]
