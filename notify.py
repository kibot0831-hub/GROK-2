"""Telegram HTML notifications."""
from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger("notify")


async def send_telegram(token: str, chat_id: str, text: str) -> None:
    if not token or not chat_id:
        log.warning("Telegram no configurado — mensaje omitido")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
        )
        if r.status_code >= 400:
            log.error("Telegram %s: %s", r.status_code, r.text[:200])


def format_signal(sig: dict[str, Any]) -> str:
    side = sig.get("side", "").upper()
    emoji = "🟢" if side == "LONG" else "🔴"
    return (
        f"{emoji} <b>IPDA GRAB · {side}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Par:</b> {sig.get('symbol')}\n"
        f"<b>Origen:</b> {sig.get('src', 'GRAB')} · Score {sig.get('score', 0)}/100\n"
        f"<b>Nivel:</b> {sig.get('level_name', '—')} {sig.get('level', '')}\n"
        f"\n"
        f"📍 Entrada: <code>{sig.get('entry')}</code>\n"
        f"🛑 SL: <code>{sig.get('sl')}</code>\n"
        f"🎯 TP1: <code>{sig.get('tp1')}</code> · TP2: <code>{sig.get('tp2')}</code>\n"
        f"\n"
        f"RVOL {sig.get('rvol', 0):.2f}x · Dist {sig.get('dist_atr', 0):.2f} ATR\n"
        f"BTC: {'alineado ✓' if sig.get('btc_ok') else 'n/d'}\n"
        f"TF {sig.get('tf', '15m')} · MODE={sig.get('mode', 'SIGNAL')}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Solo señal. Revisa gestión de riesgo.</i>"
    )


def format_heartbeat(info: dict[str, Any]) -> str:
    return (
        f"💓 <b>Heartbeat</b> · {info.get('n_sym', 0)} pares · ciclo {info.get('cycle_s', 0):.0f}s\n"
        f"Señales hoy {info.get('signals_today', 0)} · trades hoy {info.get('trades_today', 0)} · "
        f"W/L {info.get('wins', 0)}/{info.get('losses', 0)}\n"
        f"MODE={info.get('mode')} · sesión {info.get('session', '')}"
    )
