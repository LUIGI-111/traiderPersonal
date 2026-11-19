from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Carga mínima de .env sin dependencias extra."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, default))


@dataclass(frozen=True)
class Settings:
    model: str = "claude-sonnet-5-5"
    starting_cash: float = 100_000.0
    max_position_pct: float = 0.05
    max_exposure_pct: float = 0.60
    risk_per_trade_pct: float = 0.01
    stop_atr_mult: float = 2.0
    min_confidence: float = 0.60
    max_drawdown_pct: float = 0.10
    state_dir: str = "data"

    @classmethod
    def from_env(cls) -> "Settings":
        _load_dotenv()
        return cls(
            model=os.getenv("TD_MODEL", cls.model),
            starting_cash=_f("TD_STARTING_CASH", cls.starting_cash),
            max_position_pct=_f("TD_MAX_POSITION_PCT", cls.max_position_pct),
            max_exposure_pct=_f("TD_MAX_EXPOSURE_PCT", cls.max_exposure_pct),
            risk_per_trade_pct=_f("TD_RISK_PER_TRADE_PCT", cls.risk_per_trade_pct),
            stop_atr_mult=_f("TD_STOP_ATR_MULT", cls.stop_atr_mult),
            min_confidence=_f("TD_MIN_CONFIDENCE", cls.min_confidence),
            max_drawdown_pct=_f("TD_MAX_DRAWDOWN_PCT", cls.max_drawdown_pct),
            state_dir=os.getenv("TD_STATE_DIR", cls.state_dir),
        )
