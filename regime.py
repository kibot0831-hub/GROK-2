"""Régimen BTC simple: tendencia + volatilidad → ajustes de score y lados."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class Regime:
    name: str  # bull | bear | range | crisis
    btc_ema_slope: float
    atr_pct: float
    score_bump: int  # se suma a min_score efectivo
    allow_long: bool
    allow_short: bool
    note: str


def _ema(x: np.ndarray, n: int) -> Optional[float]:
    if len(x) < n:
        return None
    k = 2 / (n + 1)
    e = float(x[0])
    for v in x[1:]:
        e = float(v) * k + e * (1 - k)
    return e


def _atr_pct(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int = 14) -> float:
    if len(c) < n + 1:
        return 0.0
    prev = c[:-1]
    tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - prev), np.abs(l[1:] - prev)))
    atr = float(np.mean(tr[-n:])) if len(tr) >= n else float(np.mean(tr))
    return 100.0 * atr / c[-1] if c[-1] else 0.0


def detect_regime(
    ohlcv: list[list],
    *,
    ema_len: int = 50,
    slope_bars: int = 5,
    crisis_atr_pct: float = 3.5,
    range_slope_max: float = 0.0015,
) -> Regime:
    """
    bull: precio > EMA y EMA subiendo
    bear: precio < EMA y EMA bajando
    range: pendiente EMA casi plana
    crisis: ATR% extremo → solo score más duro, preferible no forzar
    """
    if not ohlcv or len(ohlcv) < ema_len + slope_bars + 2:
        return Regime("range", 0.0, 0.0, 0, True, True, "btc sin datos")

    arr = np.array(ohlcv, dtype=float)
    h, l, c = arr[:, 2], arr[:, 3], arr[:, 4]
    e_now = _ema(c, ema_len)
    e_prev = _ema(c[: -slope_bars], ema_len) if len(c) > ema_len + slope_bars else e_now
    if e_now is None or e_prev is None or e_prev == 0:
        return Regime("range", 0.0, 0.0, 0, True, True, "ema n/a")

    slope = (e_now - e_prev) / abs(e_prev)
    atrp = _atr_pct(h, l, c)
    px = float(c[-1])

    if atrp >= crisis_atr_pct:
        return Regime(
            "crisis",
            slope,
            atrp,
            score_bump=10,
            allow_long=True,
            allow_short=True,
            note=f"ATR% {atrp:.2f} alto → score +10",
        )

    if abs(slope) <= range_slope_max:
        return Regime(
            "range",
            slope,
            atrp,
            score_bump=5,
            allow_long=True,
            allow_short=True,
            note="EMA plana → score +5",
        )

    if px > e_now and slope > 0:
        return Regime(
            "bull",
            slope,
            atrp,
            score_bump=0,
            allow_long=True,
            allow_short=False,  # no short a favor del régimen
            note="BTC bull → solo LONG",
        )

    if px < e_now and slope < 0:
        return Regime(
            "bear",
            slope,
            atrp,
            score_bump=0,
            allow_long=False,
            allow_short=True,
            note="BTC bear → solo SHORT",
        )

    return Regime("range", slope, atrp, 3, True, True, "mixto → score +3")
