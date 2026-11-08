"""Journal automático: registra señales y resuelve TP1/SL/timeout."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("journal")


@dataclass
class JournalEntry:
    id: str
    symbol: str
    side: str
    score: int
    entry: float
    sl: float
    tp1: float
    tp2: float
    opened_at: float
    notes: list = field(default_factory=list)
    # resolución
    status: str = "OPEN"  # OPEN | TP1 | TP2 | SL | TIMEOUT
    resolved_at: float = 0.0
    exit_price: float = 0.0
    r_multiple: float = 0.0  # +1 = 1R, -1 = -1R
    hours_open: float = 0.0


class Journal:
    def __init__(self, path: str = "/data/journal.json"):
        self.path = path
        self.entries: list[JournalEntry] = []
        self._load()

    def _load(self) -> None:
        p = Path(self.path)
        if not p.exists():
            return
        try:
            data = json.loads(p.read_text())
            for e in data.get("entries", []):
                self.entries.append(JournalEntry(**e))
        except Exception as e:
            log.warning("journal load: %s", e)

    def save(self) -> None:
        p = Path(self.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        # guardar últimos 500
        payload = {"entries": [asdict(e) for e in self.entries[-500:]]}
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(p)

    def add_signal(self, sig: dict[str, Any]) -> JournalEntry:
        eid = f"{sig['symbol']}_{sig['side']}_{int(time.time())}"
        e = JournalEntry(
            id=eid,
            symbol=sig["symbol"],
            side=sig["side"],
            score=int(sig.get("score", 0)),
            entry=float(sig["entry"]),
            sl=float(sig["sl"]),
            tp1=float(sig["tp1"]),
            tp2=float(sig.get("tp2", sig["tp1"])),
            opened_at=time.time(),
            notes=list(sig.get("notes") or []),
        )
        self.entries.append(e)
        self.save()
        return e

    def resolve_open(self, price_map: dict[str, float], max_hours: float = 24.0) -> list[JournalEntry]:
        """Resuelve entradas OPEN con precio actual. Devuelve las resueltas en este ciclo."""
        resolved = []
        now = time.time()
        for e in self.entries:
            if e.status != "OPEN":
                continue
            px = price_map.get(e.symbol)
            if px is None or px <= 0:
                continue
            hours = (now - e.opened_at) / 3600.0
            risk = abs(e.entry - e.sl)
            if risk <= 0:
                continue

            hit = None
            exit_px = px
            if e.side == "LONG":
                if px <= e.sl:
                    hit = "SL"
                    exit_px = e.sl
                elif px >= e.tp2:
                    hit = "TP2"
                    exit_px = e.tp2
                elif px >= e.tp1:
                    hit = "TP1"
                    exit_px = e.tp1
            else:
                if px >= e.sl:
                    hit = "SL"
                    exit_px = e.sl
                elif px <= e.tp2:
                    hit = "TP2"
                    exit_px = e.tp2
                elif px <= e.tp1:
                    hit = "TP1"
                    exit_px = e.tp1

            if hit is None and hours >= max_hours:
                hit = "TIMEOUT"
                exit_px = px

            if hit:
                if e.side == "LONG":
                    r = (exit_px - e.entry) / risk
                else:
                    r = (e.entry - exit_px) / risk
                e.status = hit
                e.resolved_at = now
                e.exit_price = exit_px
                e.r_multiple = round(r, 3)
                e.hours_open = round(hours, 2)
                resolved.append(e)
                log.info("JOURNAL %s %s %s R=%.2f after %.1fh", e.symbol, e.side, hit, r, hours)

        if resolved:
            self.save()
        return resolved

    def stats(self, min_closed: int = 5) -> dict[str, Any]:
        closed = [e for e in self.entries if e.status != "OPEN"]
        if len(closed) < min_closed:
            return {"n": len(closed), "msg": "pocos datos"}
        wins = [e for e in closed if e.r_multiple > 0]
        losses = [e for e in closed if e.r_multiple <= 0]
        avg_r = sum(e.r_multiple for e in closed) / len(closed)
        winrate = 100.0 * len(wins) / len(closed)
        # por confluencia
        by_note: dict[str, list[float]] = {}
        for e in closed:
            for n in (e.notes or ["(none)"]):
                by_note.setdefault(n, []).append(e.r_multiple)
        note_edge = {
            k: round(sum(v) / len(v), 3)
            for k, v in by_note.items()
            if len(v) >= 3
        }
        return {
            "n": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "winrate": round(winrate, 1),
            "avg_R": round(avg_r, 3),
            "expectancy_R": round(avg_r, 3),
            "open": sum(1 for e in self.entries if e.status == "OPEN"),
            "by_confluence": note_edge,
        }
