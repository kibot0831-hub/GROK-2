"""Confirmación 5m v2 — rechazo limpio + continuación mínima."""
from __future__ import annotations


def confirm_5m(ohlcv_5m, side, level, mode="reject"):
    if not ohlcv_5m or len(ohlcv_5m) < 4:
        return False
    o, h, l, c = (float(ohlcv_5m[-1][i]) for i in range(1, 5))
    body = abs(c - o)
    rng = max(h - l, 1e-12)
    body_pct = 100.0 * body / rng
    o2, h2, l2, c2 = (float(ohlcv_5m[-2][i]) for i in range(1, 5))
    if side == "LONG":
        if mode == "reject":
            return l <= level * 1.0015 and c > level and c > o and body_pct >= 30 and (c2 >= o2 * 0.998 or c > c2)
        return c > level
    else:
        if mode == "reject":
            return h >= level * 0.9985 and c < level and c < o and body_pct >= 30 and (c2 <= o2 * 1.002 or c < c2)
        return c < level
