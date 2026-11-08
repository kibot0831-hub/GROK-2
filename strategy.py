"""IPDA GRAB v5 — Liquidity Grab + HA-EMA (soft) + Volume + Volatility + OB + RS.
HA-EMA = EMA on Heikin-Ashi series. Soft filter: score bonus only, never blocks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class Signal:
    symbol: str
    side: str
    src: str
    score: int
    entry: float
    sl: float
    tp1: float
    tp2: float
    level: float
    level_name: str
    rvol: float
    dist_atr: float
    atr: float
    btc_ok: bool
    tf: str = "15m"
    equal_count: int = 0
    displacement: float = 0.0
    has_fvg: bool = False
    session_boost: int = 0
    has_ob: bool = False
    rs_vs_btc: float = 1.0
    funding_rate: float = 0.0
    vol_climax: bool = False
    vol_delta: float = 0.0
    vol_quality: float = 0.0
    atr_pct: float = 0.0
    atr_percentile: float = 50.0
    vol_regime: str = "normal"
    ha_ema: float = 0.0
    ha_ema_slope: float = 0.0
    ha_ema_cross: bool = False
    notes: list = field(default_factory=list)


def _atr(h, l, c, n=14):
    if len(c) < n + 1:
        return 0.0
    prev = c[:-1]
    tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - prev), np.abs(l[1:] - prev)))
    return float(np.mean(tr[-n:])) if len(tr) >= n else (float(np.mean(tr)) if len(tr) else 0.0)


def _atr_series(h, l, c, n=14):
    if len(c) < n + 1:
        return np.array([])
    prev = c[:-1]
    tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - prev), np.abs(l[1:] - prev)))
    out = np.full(len(c), np.nan)
    for i in range(n, len(tr) + 1):
        out[i] = np.mean(tr[i - n:i])
    return out


def _rvol(v, n=20):
    if len(v) < n + 1 or np.mean(v[-n-1:-1]) <= 0:
        return 0.0
    return float(v[-1] / np.mean(v[-n-1:-1]))


def _body_pct(o, h, l, c):
    return 100.0 * abs(c - o) / max(h - l, 1e-12)


def _ema(x, n):
    if len(x) < n:
        return None
    k = 2.0 / (n + 1)
    e = float(x[0])
    for v in x[1:]:
        e = float(v) * k + e * (1 - k)
    return e


def _ema_series(x, n):
    out = np.full(len(x), np.nan)
    if len(x) < 1:
        return out
    k = 2.0 / (n + 1)
    e = float(x[0])
    out[0] = e
    for i in range(1, len(x)):
        e = float(x[i]) * k + e * (1 - k)
        out[i] = e
    return out


def _heikin_ashi_close(o, h, l, c):
    return (o + h + l + c) / 4.0


def special_ema_features(o, h, l, c, length=21):
    if len(c) < length + 5:
        return 0.0, 0.0, True, False
    ha_c = _heikin_ashi_close(o, h, l, c)
    ema_s = _ema_series(ha_c, length)
    if np.isnan(ema_s[-1]) or np.isnan(ema_s[-3]):
        return 0.0, 0.0, True, False
    ema_now = float(ema_s[-1])
    ema_prev = float(ema_s[-3])
    slope = (ema_now - ema_prev) / max(abs(ema_prev), 1e-12)
    price, prev_price = float(c[-1]), float(c[-2])
    above = price > ema_now
    crossed = (prev_price <= float(ema_s[-2]) and price > ema_now) or (
        prev_price >= float(ema_s[-2]) and price < ema_now
    )
    return ema_now, float(slope), above, crossed


def _find_equal_levels(highs, lows, atr, tol_atr=0.15):
    if len(highs) < 10:
        return 0.0, 0, 0.0, 0
    tol = tol_atr * atr
    hi_s = np.sort(highs[-60:])[::-1]
    best_hi, cnt_hi = hi_s[0], 1
    for p in hi_s[1:]:
        if abs(p - best_hi) <= tol:
            cnt_hi += 1
        else:
            break
    lo_s = np.sort(lows[-60:])
    best_lo, cnt_lo = lo_s[0], 1
    for p in lo_s[1:]:
        if abs(p - best_lo) <= tol:
            cnt_lo += 1
        else:
            break
    return float(best_hi), cnt_hi, float(best_lo), cnt_lo


def _has_fvg(h, l, c, side, look=8):
    if len(c) < look + 3:
        return False
    for i in range(-look, -1):
        if side == "LONG" and l[i] > h[i-2] and c[i] > c[i-1]:
            return True
        if side == "SHORT" and h[i] < l[i-2] and c[i] < c[i-1]:
            return True
    return False


def _displacement_strength(o, h, l, c, side, atr):
    if len(c) < 4 or atr <= 0:
        return 0.0
    bodies = []
    for i in (-1, -2, -3):
        body = abs(c[i] - o[i])
        if side == "LONG" and c[i] > o[i]:
            bodies.append(body / atr)
        elif side == "SHORT" and c[i] < o[i]:
            bodies.append(body / atr)
    return float(sum(bodies)) if bodies else 0.0


def _session_boost_utc():
    from datetime import datetime, timezone
    hr = datetime.now(timezone.utc).hour
    if 7 <= hr < 10 or 13 <= hr < 16:
        return 8
    if 10 <= hr < 13 or 16 <= hr < 18:
        return 4
    return 0


def _detect_order_block(o, h, l, c, side, atr, look=25):
    if len(c) < look + 5 or atr <= 0:
        return False
    for i in range(-4, -look, -1):
        body = abs(c[i] - o[i])
        if body < 0.35 * atr:
            continue
        if side == "LONG" and c[i] < o[i]:
            if any(c[j] > o[j] and (c[j]-o[j]) > 0.5*atr for j in range(i+1, 0)):
                ob_h, ob_l = max(o[i], c[i]), min(o[i], c[i])
                if l[-1] <= ob_h + 0.15*atr and c[-1] >= ob_l - 0.1*atr:
                    return True
        elif side == "SHORT" and c[i] > o[i]:
            if any(c[j] < o[j] and (o[j]-c[j]) > 0.5*atr for j in range(i+1, 0)):
                ob_h, ob_l = max(o[i], c[i]), min(o[i], c[i])
                if h[-1] >= ob_l - 0.15*atr and c[-1] <= ob_h + 0.1*atr:
                    return True
    return False


def relative_strength_vs_btc(symbol_kl, btc_kl, look=20):
    if len(symbol_kl) < look+2 or len(btc_kl) < look+2:
        return 1.0
    s = np.array([x[4] for x in symbol_kl], dtype=float)
    b = np.array([x[4] for x in btc_kl], dtype=float)
    if s[-look-1] <= 0 or b[-look-1] <= 0:
        return 1.0
    sr = (s[-1]/s[-look-1]) - 1
    br = (b[-1]/b[-look-1]) - 1
    if abs(br) < 1e-6:
        return 1.0 + sr*10
    return float(sr/br)


def _volume_features(o, h, l, c, v, atr):
    if len(v) < 25 or atr <= 0:
        return False, 0.0, 0.0
    avg = float(np.mean(v[-21:-1])) or 1.0
    rvol = float(v[-1]/avg)
    wick = max(h[-1]-max(o[-1],c[-1]), min(o[-1],c[-1])-l[-1])
    climax = rvol >= 2.2 and wick >= 0.45*atr
    up = dn = 0.0
    for i in range(-8, 0):
        if c[i] >= o[i]:
            up += v[i]
        else:
            dn += v[i]
    tot = up + dn
    delta = (up-dn)/tot if tot > 0 else 0.0
    if 1.3 <= rvol <= 3.5:
        q = 0.7 + min(0.3, (rvol-1.3)/5)
    elif rvol > 3.5:
        q = 0.55
    elif rvol >= 1.1:
        q = 0.4
    else:
        q = 0.15
    rank = float(np.sum(v[-20:] <= v[-1])) / 20
    return climax, float(delta), float(min(1.0, q + 0.15*rank))


def _volatility_features(h, l, c, atr):
    if atr <= 0 or c[-1] <= 0:
        return 0.0, 50.0, "normal"
    atr_pct = 100.0 * atr / c[-1]
    atr_s = _atr_series(h, l, c)
    valid = atr_s[~np.isnan(atr_s)]
    pctile = float(np.sum(valid <= atr)/len(valid)*100) if len(valid) >= 30 else 50.0
    if pctile >= 92 or atr_pct >= 4.5:
        reg = "extreme"
    elif pctile >= 75 or atr_pct >= 2.8:
        reg = "high"
    elif pctile <= 20 or atr_pct <= 0.7:
        reg = "low"
    else:
        reg = "normal"
    return atr_pct, pctile, reg


def detect_grab(
    symbol, ohlcv, *, lookback=80, min_wick_atr=0.28, max_depth_atr=1.6,
    rvol_min=1.15, near_atr_max=0.55, require_near=True, sl_atr_buf=0.32,
    tp1_r=1.0, tp2_r=2.0, min_rr=1.5, allow_long=True, allow_short=True,
    btc_ok=True, btc_kl=None, funding_rate=0.0, require_ob=False,
    min_rs_long=0.0, max_rs_short=99.0, min_vol_quality=0.35,
    avoid_low_vol=True, avoid_extreme_vol=True, use_ha_ema=True,
    ha_ema_length=21, require_ha_ema_align=False,
):
    if len(ohlcv) < lookback + 10:
        return None
    arr = np.array(ohlcv[-lookback-10:], dtype=float)
    o, h, l, c, v = arr[:,1], arr[:,2], arr[:,3], arr[:,4], arr[:,5]
    atr = _atr(h, l, c)
    if atr <= 0:
        return None
    rvol = _rvol(v)
    if rvol < rvol_min:
        return None
    vol_climax, vol_delta, vol_quality = _volume_features(o, h, l, c, v, atr)
    if vol_quality < min_vol_quality:
        return None
    atr_pct, atr_pctile, vol_regime = _volatility_features(h, l, c, atr)
    if avoid_low_vol and vol_regime == "low":
        return None
    if avoid_extreme_vol and vol_regime == "extreme":
        return None

    ha_ema_val, ha_slope, above_ha, ha_cross = special_ema_features(o, h, l, c, ha_ema_length)
    eq_hi, cnt_hi, eq_lo, cnt_lo = _find_equal_levels(h[:-1], l[:-1], atr)
    lvl_hi = float(np.max(h[:-1][-lookback:]))
    lvl_lo = float(np.min(l[:-1][-lookback:]))
    if cnt_hi >= 2 and abs(eq_hi - lvl_hi) < 0.25*atr:
        lvl_hi, name_hi = eq_hi, f"EQ_H×{cnt_hi}"
    else:
        name_hi, cnt_hi = "SWING_H", 1
    if cnt_lo >= 2 and abs(eq_lo - lvl_lo) < 0.25*atr:
        lvl_lo, name_lo = eq_lo, f"EQ_L×{cnt_lo}"
    else:
        name_lo, cnt_lo = "SWING_L", 1

    last_o, last_h, last_l, last_c = float(o[-1]), float(h[-1]), float(l[-1]), float(c[-1])
    wick_up = last_h - max(last_o, last_c)
    wick_dn = min(last_o, last_c) - last_l
    body_pct = _body_pct(last_o, last_h, last_l, last_c)
    session_b = _session_boost_utc()
    rs = relative_strength_vs_btc(ohlcv, btc_kl or []) if btc_kl else 1.0

    def _build(side, level, level_name, eq_cnt, wick, depth):
        dist = abs(last_c - level) / atr
        if require_near and dist > near_atr_max:
            return None
        if side == "SHORT" and rs > max_rs_short:
            return None
        if side == "LONG" and rs < min_rs_long:
            return None
        if use_ha_ema and require_ha_ema_align:
            if side == "SHORT" and (above_ha or ha_slope > 0.0015):
                return None
            if side == "LONG" and (not above_ha or ha_slope < -0.0015):
                return None
        disp = _displacement_strength(o, h, l, c, side, atr)
        has_fvg = _has_fvg(h, l, c, side)
        has_ob = _detect_order_block(o, h, l, c, side, atr)
        if require_ob and not has_ob:
            return None
        if side == "SHORT":
            entry, sl = last_c, last_h + sl_atr_buf * atr
            risk = sl - entry
            if risk <= 0 or risk/entry > 0.045:
                return None
            tp1, tp2 = entry - tp1_r*risk, entry - tp2_r*risk
        else:
            entry, sl = last_c, last_l - sl_atr_buf * atr
            risk = entry - sl
            if risk <= 0 or risk/entry > 0.045:
                return None
            tp1, tp2 = entry + tp1_r*risk, entry + tp2_r*risk
        notes = []
        if eq_cnt >= 2: notes.append(f"EQ×{eq_cnt}")
        if has_fvg: notes.append("FVG")
        if has_ob: notes.append("OB")
        if disp >= 0.7: notes.append(f"Disp{disp:.1f}")
        if vol_climax: notes.append("VolClimax")
        if ha_cross: notes.append("HA-Cross")
        if use_ha_ema and ((side=="LONG" and above_ha and ha_slope>0) or (side=="SHORT" and not above_ha and ha_slope<0)):
            notes.append("HA-Align")
        if session_b >= 8: notes.append("Killzone")
        if side=="SHORT" and funding_rate > 0.0003: notes.append(f"Fund+{funding_rate*100:.3f}%")
        if side=="LONG" and funding_rate < -0.0003: notes.append(f"Fund{funding_rate*100:.3f}%")
        score = _score_v5(
            rvol, depth/atr, dist, wick/atr, body_pct, eq_cnt, disp,
            has_fvg, has_ob, btc_ok, session_b, rs, side, funding_rate,
            vol_climax, vol_delta, vol_quality, atr_pctile, vol_regime,
            ha_slope, ha_cross, above_ha,
        )
        return Signal(
            symbol=symbol, side=side, src="GRAB", score=score,
            entry=entry, sl=sl, tp1=tp1, tp2=tp2, level=level,
            level_name=level_name, rvol=rvol, dist_atr=dist, atr=atr,
            btc_ok=btc_ok, equal_count=eq_cnt, displacement=disp,
            has_fvg=has_fvg, session_boost=session_b, has_ob=has_ob,
            rs_vs_btc=rs, funding_rate=funding_rate,
            vol_climax=vol_climax, vol_delta=vol_delta, vol_quality=vol_quality,
            atr_pct=atr_pct, atr_percentile=atr_pctile, vol_regime=vol_regime,
            ha_ema=ha_ema_val, ha_ema_slope=ha_slope, ha_ema_cross=ha_cross,
            notes=notes,
        )

    if allow_short and last_h > lvl_hi:
        depth = last_h - lvl_hi
        clean = wick_up >= min_wick_atr*atr and last_c < lvl_hi and last_c < last_o and body_pct >= 28
        if depth <= max_depth_atr*atr and clean:
            sig = _build("SHORT", lvl_hi, name_hi, cnt_hi, wick_up, depth)
            if sig:
                return sig
    if allow_long and last_l < lvl_lo:
        depth = lvl_lo - last_l
        clean = wick_dn >= min_wick_atr*atr and last_c > lvl_lo and last_c > last_o and body_pct >= 28
        if depth <= max_depth_atr*atr and clean:
            sig = _build("LONG", lvl_lo, name_lo, cnt_lo, wick_dn, depth)
            if sig:
                return sig
    return None


def _score_v5(
    rvol, depth_atr, dist_atr, wick_atr, body_pct, equal_count, displacement,
    has_fvg, has_ob, btc_ok, session_boost, rs, side, funding_rate,
    vol_climax, vol_delta, vol_quality, atr_pctile, vol_regime,
    ha_slope, ha_cross, above_ha,
):
    s = 32
    s += min(13, int(rvol * 6))
    s += min(9, int(wick_atr * 7.5))
    if body_pct >= 45: s += 6
    elif body_pct >= 32: s += 3
    if 0.15 <= depth_atr <= 0.9: s += 5
    if dist_atr <= 0.25: s += 9
    elif dist_atr <= 0.40: s += 5
    if equal_count >= 3: s += 11
    elif equal_count >= 2: s += 6
    if displacement >= 1.2: s += 9
    elif displacement >= 0.7: s += 4
    if has_fvg: s += 6
    if has_ob: s += 7
    if btc_ok: s += 5
    s += session_boost
    if side == "LONG" and rs >= 1.15: s += 6
    elif side == "LONG" and rs >= 1.05: s += 3
    elif side == "SHORT" and rs <= 0.85: s += 6
    elif side == "SHORT" and rs <= 0.95: s += 3
    if side == "SHORT" and funding_rate >= 0.0004: s += 4
    elif side == "LONG" and funding_rate <= -0.0004: s += 4
    s += int(vol_quality * 10)
    if vol_climax: s += 4
    if side == "LONG" and vol_delta > 0.15: s += 3
    elif side == "SHORT" and vol_delta < -0.15: s += 3
    if vol_regime == "high": s += 4
    elif vol_regime == "normal": s += 2
    if 40 <= atr_pctile <= 85: s += 2
    # HA-EMA soft filter bonuses
    if ha_cross: s += 8
    if side == "LONG" and above_ha and ha_slope > 0: s += 6
    elif side == "SHORT" and (not above_ha) and ha_slope < 0: s += 6
    elif side == "LONG" and above_ha: s += 3
    elif side == "SHORT" and not above_ha: s += 3
    return int(max(0, min(100, s)))


def btc_aligned(btc_ohlcv, side, ema_len=50):
    if not btc_ohlcv or len(btc_ohlcv) < ema_len + 2:
        return True
    c = np.array([x[4] for x in btc_ohlcv], dtype=float)
    ema = _ema(c, ema_len)
    if ema is None:
        return True
    return c[-1] > ema if side == "LONG" else c[-1] < ema
