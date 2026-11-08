"""
IPDA GRAB Bot v1.0 — BingX perps · Telegram · filtros papel (solo GRAB score≥85)
MODE=SIGNAL por defecto (no opera).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bingx import BingX
from config import settings
from confirm import confirm_5m
from notify import format_heartbeat, format_signal, send_telegram
from strategy import Signal, btc_aligned, detect_grab
from regime import Regime, detect_regime

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
                "seen": {},  # symbol+side+hour -> ts
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
    rows = []
    all_syms = set()
    for t in tickers:
        sym = (t.get("symbol") or "").upper()
        if not sym.endswith(f"-{quote}"):
            # también aceptar símbolos commodity tipo XBR-USDT si el quote coincide
            if f"-{quote}" not in sym and not sym.endswith(quote):
                continue
        try:
            vol = float(t.get("quoteVolume") or t.get("volume") or 0)
        except (TypeError, ValueError):
            vol = 0.0
        rows.append((sym, vol))
        all_syms.add(sym)
    rows.sort(key=lambda x: x[1], reverse=True)
    out = [s for s, _ in rows[: settings.max_symbols]]
    # EXTRA_SYMBOLS: OIL-USDT,XBR-USDT,BRENT-USDT,...
    for raw in (settings.extra_symbols or "").split(","):
        s = raw.strip().upper().replace("/", "-").replace("_", "-")
        if not s:
            continue
        if not s.endswith(f"-{quote}") and quote not in s:
            s = f"{s}-{quote}" if "-" not in s else s
        if s not in out:
            out.insert(0, s)
            log.info("extra symbol forzado: %s", s)
    return out


async def scan_symbol(
    bx: BingX,
    symbol: str,
    btc_kl: list[list],
    sem: asyncio.Semaphore,
) -> Signal | None:
    async with sem:
        try:
            kl = await bx.klines(symbol, settings.tf_main, limit=200)
        except Exception as e:
            log.debug("%s klines: %s", symbol, e)
            return None
    if not settings.allow_grab:
        return None

    side_btc_long = btc_aligned(btc_kl, "LONG")
    side_btc_short = btc_aligned(btc_kl, "SHORT")

    # probar short y long
    for side, allow, btc_ok in (
        ("SHORT", settings.allow_short, side_btc_short if settings.require_btc_align else True),
        ("LONG", settings.allow_long, side_btc_long if settings.require_btc_align else True),
    ):
        if not allow:
            continue
        if settings.require_btc_align and not btc_ok:
            continue
        sig = detect_grab(
            symbol,
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
        )
        if not sig:
            continue
        need = settings.short_min_score if sig.side == "SHORT" else settings.min_score
        if sig.score < need:
            continue
        # confirmación 5m
        if settings.confirm_required:
            try:
                async with sem:
                    kl5 = await bx.klines(symbol, settings.tf_confirm, limit=30)
                if not confirm_5m(kl5, sig.side, sig.level, settings.confirm_mode):
                    continue
            except Exception:
                continue
        return sig
    return None


async def run_cycle(bx: BingX, state: State) -> dict[str, Any]:
    t0 = time.time()
    if not _in_session():
        log.info("fuera de sesión UTC hour=%s window=%s-%s", _utc_hour(), settings.session_start, settings.session_end)
        return {"n_sym": 0, "cycle_s": 0, "skipped": "session", "hour_utc": _utc_hour()}

    if state.data.get("trades_today", 0) >= settings.max_trades_day:
        return {"n_sym": 0, "cycle_s": 0, "skipped": "max_trades"}

    if state.data.get("loss_streak", 0) >= settings.max_loss_streak:
        return {"n_sym": 0, "cycle_s": 0, "skipped": "loss_streak"}

    symbols = await pick_symbols(bx)
    try:
        btc_kl = await bx.klines(f"BTC-{settings.quote}", settings.tf_main, limit=120)
    except Exception:
        btc_kl = []

    # ── Régimen BTC ──
    reg: Regime = (
        detect_regime(
            btc_kl,
            ema_len=settings.regime_ema,
            crisis_atr_pct=settings.crisis_atr_pct,
        )
        if settings.use_regime
        else Regime("off", 0.0, 0.0, 0, True, True, "USE_REGIME=false")
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

        for side, allow, btc_ok, need in (
            ("SHORT", allow_short, side_btc_short if settings.require_btc_align else True, eff_min_short),
            ("LONG", allow_long, side_btc_long if settings.require_btc_align else True, eff_min_long),
        ):
            if not allow:
                continue
            if settings.require_btc_align and not btc_ok:
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
        }
        text = format_signal(payload)
        text += f"\n🏛 Régimen: <b>{reg.name}</b> · {reg.note}"
        await send_telegram(settings.telegram_token, settings.telegram_chat_id, text)
        state.mark_sent(sig.symbol, sig.side)
        funnel["sent"] += 1
        log.info("SIGNAL %s %s score=%s regime=%s", sig.side, sig.symbol, sig.score, reg.name)

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
    }


async def main() -> None:
    log.info(
        "IPDA GRAB bot arranque MODE=%s MIN_SCORE=%s SHORT_MIN=%s CONT=%s SESSION=%s-%s",
        settings.mode,
        settings.min_score,
        settings.short_min_score,
        settings.allow_cont,
        settings.session_start,
        settings.session_end,
    )
    bx = BingX(settings.bingx_api_key, settings.bingx_api_secret)
    state = State()
    await send_telegram(
        settings.telegram_token,
        settings.telegram_chat_id,
        (
            f"🤖 <b>IPDA GRAB arrancado</b>\n"
            f"MODE={settings.mode} · sesión {settings.session_start}-{settings.session_end} UTC\n"
            f"MIN_SCORE={settings.min_score} · SHORT={settings.short_min_score}\n"
            f"CONT={settings.allow_cont} · SCAN={settings.scan_sec}s\n"
            f"Extra: {settings.extra_symbols or '—'}"
        ),
    )
    hb_every = max(1, int(1800 / max(settings.scan_sec, 30)))  # ~30 min
    n = 0
    try:
        while True:
            state._roll_day()
            try:
                info = await run_cycle(bx, state)
                log.info("ciclo %s", info)
                n += 1
                if n % hb_every == 0:
                    fun = info.get("funnel") or {}
                    extra = (
                        f"\nRégimen <b>{info.get('regime', '—')}</b> · {info.get('regime_note', '')}\n"
                        f"Score eff L/S {info.get('eff_min_L')}/{info.get('eff_min_S')}\n"
                        f"Embudo scan {fun.get('scan', 0)} › grab {fun.get('grab', 0)} › "
                        f"score {fun.get('score', 0)} › conf {fun.get('confirm', 0)} › sent {fun.get('sent', 0)}"
                    )
                    await send_telegram(
                        settings.telegram_token,
                        settings.telegram_chat_id,
                        format_heartbeat(info) + extra,
                    )
            except Exception:
                log.exception("ciclo error")
            await asyncio.sleep(settings.scan_sec)
    finally:
        await bx.close()


if __name__ == "__main__":
    asyncio.run(main())
