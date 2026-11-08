"""Gestión de posiciones PAPER/LIVE - partial TP + trailing."""
from __future__ import annotations
import json, logging, time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("position")

@dataclass
class OpenTrade:
    id: str
    symbol: str
    side: str
    entry: float
    sl: float
    tp1: float
    tp2: float
    qty: float
    opened_at: float
    score: int
    mode: str
    remaining_qty: float = 0.0
    tp1_hit: bool = False
    trailing_active: bool = False
    trail_price: float = 0.0
    highest: float = 0.0
    lowest: float = 0.0
    realized_pnl: float = 0.0
    status: str = "OPEN"
    close_reason: str = ""
    closed_at: float = 0.0
    def __post_init__(self):
        if self.remaining_qty <= 0: self.remaining_qty = self.qty
        if self.side == "LONG": self.highest = self.entry
        else: self.lowest = self.entry

class PositionManager:
    def __init__(self, path="/data/paper.json"):
        self.path = path
        self.trades = []
        self._load()
    def _load(self):
        p = Path(self.path)
        if not p.exists(): return
        try:
            data = json.loads(p.read_text())
            for t in data.get("open", []):
                self.trades.append(OpenTrade(**t))
        except Exception as e:
            log.warning("load paper: %s", e)
    def save(self):
        p = Path(self.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        open_t = [asdict(t) for t in self.trades if t.status == "OPEN"]
        closed = [asdict(t) for t in self.trades if t.status == "CLOSED"][-50:]
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"open": open_t, "closed": closed}, indent=2))
        tmp.replace(p)
    def open_trade(self, symbol, side, entry, sl, tp1, tp2, qty, score, mode="PAPER"):
        tid = f"{symbol}_{side}_{int(time.time())}"
        t = OpenTrade(id=tid, symbol=symbol, side=side, entry=entry, sl=sl, tp1=tp1, tp2=tp2,
                      qty=qty, opened_at=time.time(), score=score, mode=mode, remaining_qty=qty)
        self.trades.append(t)
        self.save()
        return t
    def update_price(self, symbol, price):
        events = []
        for t in self.trades:
            if t.status != "OPEN" or t.symbol != symbol: continue
            events.extend(self._check(t, price))
        if events: self.save()
        return events
    def _check(self, t, price):
        ev = []
        if t.side == "LONG":
            t.highest = max(t.highest, price)
            if price <= t.sl:
                pnl = (t.sl - t.entry) / t.entry * t.remaining_qty
                t.realized_pnl += pnl; t.status="CLOSED"; t.close_reason="SL"; t.closed_at=time.time(); t.remaining_qty=0
                return [{"type":"CLOSE","reason":"SL","trade":t,"pnl":pnl}]
            if not t.tp1_hit and price >= t.tp1:
                half = t.remaining_qty * 0.5
                pnl = (t.tp1 - t.entry) / t.entry * half
                t.realized_pnl += pnl; t.remaining_qty -= half; t.tp1_hit=True
                t.sl = t.entry * 1.001; t.trailing_active=True; t.trail_price = price * 0.992
                ev.append({"type":"TP1","trade":t,"pnl":pnl})
            if t.trailing_active and t.remaining_qty > 0:
                nt = t.highest * 0.992
                if nt > t.trail_price: t.trail_price = nt
                if price <= t.trail_price:
                    pnl = (price - t.entry) / t.entry * t.remaining_qty
                    t.realized_pnl += pnl; t.status="CLOSED"; t.close_reason="TRAIL"; t.closed_at=time.time(); t.remaining_qty=0
                    ev.append({"type":"CLOSE","reason":"TRAIL","trade":t,"pnl":pnl})
            if t.tp1_hit and price >= t.tp2 and t.remaining_qty > 0:
                pnl = (t.tp2 - t.entry) / t.entry * t.remaining_qty
                t.realized_pnl += pnl; t.status="CLOSED"; t.close_reason="TP2"; t.closed_at=time.time(); t.remaining_qty=0
                ev.append({"type":"CLOSE","reason":"TP2","trade":t,"pnl":pnl})
        else:
            t.lowest = min(t.lowest, price)
            if price >= t.sl:
                pnl = (t.entry - t.sl) / t.entry * t.remaining_qty
                t.realized_pnl += pnl; t.status="CLOSED"; t.close_reason="SL"; t.closed_at=time.time(); t.remaining_qty=0
                return [{"type":"CLOSE","reason":"SL","trade":t,"pnl":pnl}]
            if not t.tp1_hit and price <= t.tp1:
                half = t.remaining_qty * 0.5
                pnl = (t.entry - t.tp1) / t.entry * half
                t.realized_pnl += pnl; t.remaining_qty -= half; t.tp1_hit=True
                t.sl = t.entry * 0.999; t.trailing_active=True; t.trail_price = price * 1.008
                ev.append({"type":"TP1","trade":t,"pnl":pnl})
            if t.trailing_active and t.remaining_qty > 0:
                nt = t.lowest * 1.008
                if nt < t.trail_price: t.trail_price = nt
                if price >= t.trail_price:
                    pnl = (t.entry - price) / t.entry * t.remaining_qty
                    t.realized_pnl += pnl; t.status="CLOSED"; t.close_reason="TRAIL"; t.closed_at=time.time(); t.remaining_qty=0
                    ev.append({"type":"CLOSE","reason":"TRAIL","trade":t,"pnl":pnl})
            if t.tp1_hit and price <= t.tp2 and t.remaining_qty > 0:
                pnl = (t.entry - t.tp2) / t.entry * t.remaining_qty
                t.realized_pnl += pnl; t.status="CLOSED"; t.close_reason="TP2"; t.closed_at=time.time(); t.remaining_qty=0
                ev.append({"type":"CLOSE","reason":"TP2","trade":t,"pnl":pnl})
        return ev
    def open_count(self):
        return sum(1 for t in self.trades if t.status == "OPEN")
