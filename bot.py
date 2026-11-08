"""
IPDA GRAB Bot v5 — BingX + Telegram + Journal + PAPER
MODE=SIGNAL por defecto.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bingx import BingX
from config import settings
from confirm import confirm_5m
from journal import Journal
from notify import format_heartbeat, format_signal, send_telegram
from position import PositionManager
from regime import Regime, detect_regime
from strategy import Signal, btc_aligned, detect_grab

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")


def _utc_hour() -> int:
    return datetime.now(timezone.utc).hour


def _in_session() -> bool:
    h = _utc_hour()
    return settings.session_start <= h < settings.session_end


def _load_json(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _save_json(path: str, data: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(p)


def _day_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class State:
    def __init__(self) -> None:
        self.data = _load_json(settings.state_path)
        self._roll_day()

    def _roll_day(self) -> None:
        d = _day_key()
        if self.data.get("day") != d:
            self.data = {
                "day": d,
                "signals_today": 0,
                "trades_today": 0,
                "wins": 0,
                "losses": 0,
                "loss_streak": 0,
                "seen": {},
            }
            self.save()

    def save(self) -> None:
        _save_json(settings.state_path, self.data)

    def seen_key(self, symbol: str, side: str) -> str:
        return f"{symbol}:{side}:{_day_key()}:{_utc_hour()}"

    def already_sent(self, symbol: str, side: str) -> bool:
        return self.seen_key(symbol, side) in self.data.get("seen", {})

    def mark_sent(self, symbol: str, side: str) -> None:
        self.data.setdefault("seen", {})[self.seen_key(symbol, side)] = time.time()
        self.data["signals_today"] = int(self.data.get("signals_today", 0)) + 1
        self.save()


async def pick_symbols(bx: BingX) -> list[str]:
    tickers = await bx.swap_tickers()
    quote = settings.quote
    min_vol = getattr(settings, "min_quote_volume", 2_000_000.0)
    rows = []
    for t in tickers:
        sym = (t.get("symbol") or "").upper()
        if not sym.endswith(f"-{quote}"):
            if f"-{quote}" not in sym and not sym.endswith(quote):
                continue
        try:
            vol = float(t.get("quoteVolume") or t.get("volume") or 0)
        except (TypeError, ValueError):
            vol = 0.0
        if vol < min_vol:
            continue
        rows.append((sym, vol))
    rows.sort(key=lambda x: x[1], reverse=True)
    out = [s for s, _ in rows[: settings.max_symbols]]
    for raw in (settings.extra_symbols or "").split(","):
        s = raw.strip().upper().replace("/", "-").replace("_", "-")
        if not s:
            continue
        if not s.endswith(f"-{quote}") and quote not in s:
            s = f"{s}-{quote}" if "-" not in s else s
        if s not in out:
            out.insert(0, s)
            log.info("extra symbol: %s", s)
    return out


async def load_funding_map(bx: BingX) -> dict[str, float]:
    out: dict[str, float] = {}
    if not getattr(settings, "use_funding", True):
        return out
    try:
        data = await bx.premium_index()
        rows = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
        for row in rows:
            sym = (row.get("symbol") or "").upper()
            try:
                fr = float(row.get("lastFundingRate") or row.get("fundingRate") or 0)
            except (TypeError, ValueError):
                fr = 0.0
            if sym:
                out[sym] = fr
    except Exception as e:
        log.debug("funding: %s", e)
    return out


async def run_cycle(bx: BingX, state: State, pm: PositionManager, journal: Journal) -> dict[str, Any]:
    t0 = time.time()
    if not _in_session():
        return {"n_sym": 0, "cycle_s": 0, "skipped": "session", "hour_utc": _utc_hour()}

    if state.data.get("trades_today", 0) >= settings.max_trades_day:
        return {"n_sym": 0, "cycle_s": 0, "skipped": "max_trades"}

    if state.data.get("loss_streak", 0) >= settings.max_loss_streak:
        return {"n_sym": 0, "cycle_s": 0, "skipped": "loss_streak"}

    symbols = await pick_symbols(bx)
    funding_map = await load_funding_map(bx)

    try:
        btc_kl = await bx.klines(f"BTC-{settings.quote}", settings.tf_main, limit=120)
    except Exception:
        btc_kl = []

    reg: Regime = (
        detect_regime(btc_kl, ema_len=settings.regime_ema, crisis_atr_pct=settings.crisis_atr_pct)
        if settings.use_regime
        else Regime("off", 0.0, 0.0, 0, True, True, "off")
    )
    eff_min_long = settings.min_score + (reg.score_bump if settings.use_regime else 0)
    eff_min_short = settings.short_min_score + (reg.score_bump if settings.use_regime else 0)
    allow_long = settings.allow_long and reg.allow_long
    allow_short = settings.allow_short and reg.allow_short

    sem = asyncio.Semaphore(settings.workers)
    funnel = {"scan": 0, "grab": 0, "score": 0, "confirm": 0, "sent": 0}

    async def _one(sym: str) -> Signal | None:
        async with sem:
            try:
                kl = await bx.klines(sym, settings.tf_main, limit=200)
            except Exception:
                return None
        funnel["scan"] += 1
        if not settings.allow_grab:
            return None

        side_btc_long = btc_aligned(btc_kl, "LONG")
        side_btc_short = btc_aligned(btc_kl, "SHORT")
        fr = funding_map.get(sym, 0.0)

        for side, allow, btc_ok, need in (
            ("SHORT", allow_short, side_btc_short if settings.require_btc_align else True, eff_min_short),
            ("LONG", allow_long, side_btc_long if settings.require_btc_align else True, eff_min_long),
        ):
            if not allow:
                continue
            if settings.require_btc_align and not btc_ok:
                continue
            if getattr(settings, "use_funding", True):
                if side == "LONG" and fr > 0.001:
                    continue
                if side == "SHORT" and fr < -0.001:
                    continue

            sig = detect_grab(
                sym,
                kl,
                lookback=min(120, max(40, len(kl) - 5)),
                near_atr_max=settings.near_atr_max,
                require_near=settings.require_near_level,
                sl_atr_buf=settings.sl_atr_buf,
                tp1_r=settings.tp1_r,
                tp2_r=settings.tp2_r,
                min_rr=settings.min_rr,
                allow_long=side == "LONG",
                allow_short=side == "SHORT",
                btc_ok=btc_ok,
                btc_kl=btc_kl,
                funding_rate=fr,
                require_ob=getattr(settings, "require_ob", False),
                min_rs_long=getattr(settings, "min_rs_long", 0.0),
                max_rs_short=getattr(settings, "max_rs_short", 99.0),
                min_vol_quality=getattr(settings, "min_vol_quality", 0.35),
                avoid_low_vol=getattr(settings, "avoid_low_vol", True),
                avoid_extreme_vol=getattr(settings, "avoid_extreme_vol", True),
                use_ha_ema=getattr(settings, "use_ha_ema", True),
                ha_ema_length=getattr(settings, "ha_ema_length", 21),
                require_ha_ema_align=getattr(settings, "require_ha_ema_align", False),
            )
            if not sig:
                continue
            funnel["grab"] += 1
            if sig.score < need:
                continue
            funnel["score"] += 1
            if settings.confirm_required:
                try:
                    async with sem:
                        kl5 = await bx.klines(sym, settings.tf_confirm, limit=30)
                    if not confirm_5m(kl5, sig.side, sig.level, settings.confirm_mode):
                        continue
                except Exception:
                    continue
            funnel["confirm"] += 1
            return sig
        return None

    results = await asyncio.gather(*[_one(s) for s in symbols], return_exceptions=True)
    signals: list[Signal] = [r for r in results if isinstance(r, Signal)]
    signals.sort(key=lambda x: x.score, reverse=True)

    for sig in signals:
        if state.already_sent(sig.symbol, sig.side):
            continue
        if state.data.get("signals_today", 0) >= settings.max_trades_day * 3:
            break
        need = eff_min_short if sig.side == "SHORT" else eff_min_long
        if sig.score < need:
            continue

        payload = {
            "symbol": sig.symbol,
            "side": sig.side,
            "src": sig.src,
            "score": sig.score,
            "entry": sig.entry,
            "sl": sig.sl,
            "tp1": sig.tp1,
            "tp2": sig.tp2,
            "level": sig.level,
            "level_name": sig.level_name,
            "rvol": sig.rvol,
            "dist_atr": sig.dist_atr,
            "btc_ok": sig.btc_ok,
            "tf": settings.tf_main,
            "mode": settings.mode,
            "notes": getattr(sig, "notes", []),
            "rs_vs_btc": getattr(sig, "rs_vs_btc", 1.0),
            "funding_rate": getattr(sig, "funding_rate", 0.0),
            "vol_quality": getattr(sig, "vol_quality", 0.0),
            "vol_regime": getattr(sig, "vol_regime", ""),
            "ha_ema_cross": getattr(sig, "ha_ema_cross", False),
        }
        text = format_signal(payload)
        text += f"\nRegimen: <b>{reg.name}</b> · {reg.note}"
        await send_telegram(settings.telegram_token, settings.telegram_chat_id, text)
        state.mark_sent(sig.symbol, sig.side)
        funnel["sent"] += 1

        # Journal
        journal.add_signal(payload)

        # PAPER / LIVE
        if settings.mode in ("PAPER", "LIVE") and pm.open_count() < settings.max_trades_day:
            qty = getattr(settings, "paper_qty", 100.0) / max(sig.entry, 1e-12)
            if settings.mode == "LIVE":
                try:
                    side_order = "BUY" if sig.side == "LONG" else "SELL"
                    await bx.place_order(sig.symbol, side_order, qty)
                except Exception as e:
                    log.error("LIVE order %s: %s", sig.symbol, e)
                    continue
            pm.open_trade(
                symbol=sig.symbol, side=sig.side, entry=sig.entry,
                sl=sig.sl, tp1=sig.tp1, tp2=sig.tp2, qty=qty,
                score=sig.score, mode=settings.mode,
            )
            state.data["trades_today"] = int(state.data.get("trades_today", 0)) + 1
            state.save()

        log.info("SIGNAL %s %s score=%s notes=%s", sig.side, sig.symbol, sig.score, payload.get("notes"))

    # Precios para journal + positions
    price_map: dict[str, float] = {}
    try:
        tickers = await bx.swap_tickers()
        for t in tickers:
            sym = (t.get("symbol") or "").upper()
            try:
                px = float(t.get("lastPrice") or t.get("close") or 0)
            except (TypeError, ValueError):
                px = 0.0
            if sym and px > 0:
                price_map[sym] = px
    except Exception:
        log.exception("tickers for resolve")

    # Resolver journal
    resolved = journal.resolve_open(price_map, max_hours=getattr(settings, "journal_max_hours", 24.0))
    for e in resolved:
        emoji = "OK" if e.r_multiple > 0 else "SL"
        msg = (
            f"{'🟢' if e.r_multiple > 0 else '🔴'} <b>Journal {e.status}</b> {e.symbol} {e.side}\n"
            f"R = <b>{e.r_multiple:+.2f}</b> · {e.hours_open:.1f}h · score {e.score}"
        )
        await send_telegram(settings.telegram_token, settings.telegram_chat_id, msg)
        if e.r_multiple > 0:
            state.data["wins"] = int(state.data.get("wins", 0)) + 1
            state.data["loss_streak"] = 0
        else:
            state.data["losses"] = int(state.data.get("losses", 0)) + 1
            state.data["loss_streak"] = int(state.data.get("loss_streak", 0)) + 1
        state.save()

    # Position manager updates
    if settings.mode in ("PAPER", "LIVE") and pm.open_count() > 0:
        for sym, px in price_map.items():
            events = pm.update_price(sym, px)
            for ev in events:
                t = ev.get("trade")
                if not t:
                    continue
                if ev.get("type") == "TP1":
                    await send_telegram(
                        settings.telegram_token, settings.telegram_chat_id,
                        f"✅ TP1 {t.symbol} {t.side} · SL→BE · trailing ON",
                    )
                elif ev.get("type") == "CLOSE":
                    pnl = ev.get("pnl", 0)
                    await send_telegram(
                        settings.telegram_token, settings.telegram_chat_id,
                        f"{'🟢' if pnl > 0 else '🔴'} Cerrado ({ev.get('reason')}) {t.symbol} {t.side} · PnL {pnl*100:.2f}%",
                    )

    return {
        "n_sym": len(symbols),
        "cycle_s": time.time() - t0,
        "signals_today": state.data.get("signals_today", 0),
        "trades_today": state.data.get("trades_today", 0),
        "wins": state.data.get("wins", 0),
        "losses": state.data.get("losses", 0),
        "mode": settings.mode,
        "session": f"{settings.session_start}-{settings.session_end}",
        "regime": reg.name,
        "regime_note": reg.note,
        "eff_min_L": eff_min_long,
        "eff_min_S": eff_min_short,
        "funnel": dict(funnel),
        "open_positions": pm.open_count(),
        "journal": journal.stats(min_closed=1),
    }


async def main() -> None:
    log.info(
        "IPDA GRAB v5 MODE=%s MIN_SCORE=%s HA_EMA=%s soft=%s",
        settings.mode, settings.min_score,
        getattr(settings, "use_ha_ema", True),
        not getattr(settings, "require_ha_ema_align", False),
    )
    bx = BingX(settings.bingx_api_key, settings.bingx_api_secret)
    state = State()
    pm = PositionManager(settings.paper_path)
    journal = Journal(getattr(settings, "journal_path", "/data/journal.json"))

    await send_telegram(
        settings.telegram_token,
        settings.telegram_chat_id,
        (
            f"🤖 <b>IPDA GRAB v5 arrancado</b>\n"
            f"MODE={settings.mode} · {settings.session_start}-{settings.session_end} UTC\n"
            f"MIN_SCORE={settings.min_score} · HA-EMA soft · Journal ON"
        ),
    )

    hb_every = max(1, int(1800 / max(settings.scan_sec, 30)))
    n = 0
    try:
        while True:
            state._roll_day()
            try:
                info = await run_cycle(bx, state, pm, journal)
                log.info("ciclo %s", info)
                n += 1
                if n % hb_every == 0:
                    fun = info.get("funnel") or {}
                    j = info.get("journal") or {}
                    extra = (
                        f"\nRegimen <b>{info.get('regime')}</b>\n"
                        f"Embudo {fun.get('scan',0)}→{fun.get('grab',0)}→{fun.get('score',0)}→"
                        f"{fun.get('confirm',0)}→{fun.get('sent',0)}\n"
                        f"Journal: n={j.get('n',0)} WR={j.get('winrate','—')}% avgR={j.get('avg_R','—')}"
                    )
                    await send_telegram(
                        settings.telegram_token, settings.telegram_chat_id,
                        format_heartbeat(info) + extra,
                    )
            except Exception:
                log.exception("ciclo error")
            await asyncio.sleep(settings.scan_sec)
    finally:
        await bx.close()


if __name__ == "__main__":
    asyncio.run(main())
