"""Cliente BingX público (klines, tickers, funding) + firmado opcional para LIVE."""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any
from urllib.parse import urlencode

import httpx

log = logging.getLogger("bingx")
BASE = "https://open-api.bingx.com"


class BingX:
    def __init__(self, api_key: str = "", api_secret: str = "", timeout: float = 20.0):
        self.api_key = api_key
        self.api_secret = api_secret
        self.client = httpx.AsyncClient(timeout=timeout, headers={"User-Agent": "ipda-grab-bot/5.0"})

    async def close(self) -> None:
        await self.client.aclose()

    async def _get(self, path: str, params: dict | None = None) -> Any:
        r = await self.client.get(f"{BASE}{path}", params=params or {})
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and data.get("code") not in (None, 0, "0"):
            raise RuntimeError(f"BingX error {data.get('code')}: {data.get('msg')}")
        return data.get("data", data)

    def _sign(self, params: dict) -> dict:
        params = dict(params)
        params["timestamp"] = int(time.time() * 1000)
        qs = urlencode(params)
        sig = hmac.new(self.api_secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
        params["signature"] = sig
        return params

    async def swap_tickers(self) -> list[dict]:
        data = await self._get("/openApi/swap/v2/quote/ticker")
        return data if isinstance(data, list) else []

    async def klines(self, symbol: str, interval: str = "15m", limit: int = 200) -> list[list]:
        data = await self._get(
            "/openApi/swap/v3/quote/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
        )
        if not isinstance(data, list):
            return []
        out = []
        for row in data:
            if isinstance(row, dict):
                out.append([
                    int(row.get("time") or row.get("t") or 0),
                    float(row.get("open") or row.get("o") or 0),
                    float(row.get("high") or row.get("h") or 0),
                    float(row.get("low") or row.get("l") or 0),
                    float(row.get("close") or row.get("c") or 0),
                    float(row.get("volume") or row.get("v") or 0),
                ])
            elif isinstance(row, (list, tuple)) and len(row) >= 6:
                out.append([int(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])])
        out.sort(key=lambda x: x[0])
        return out

    async def premium_index(self, symbol: str | None = None):
        params = {"symbol": symbol} if symbol else {}
        data = await self._get("/openApi/swap/v2/quote/premiumIndex", params)
        return data if data is not None else []

    async def place_order(self, symbol: str, side: str, quantity: float, position_side: str = "BOTH") -> dict:
        if not self.api_key or not self.api_secret:
            raise RuntimeError("LIVE requiere BINGX_API_KEY y BINGX_API_SECRET")
        params = {
            "symbol": symbol, "side": side.upper(), "positionSide": position_side,
            "type": "MARKET", "quantity": quantity,
        }
        signed = self._sign(params)
        r = await self.client.post(
            f"{BASE}/openApi/swap/v2/trade/order",
            params=signed,
            headers={"X-BX-APIKEY": self.api_key},
        )
        r.raise_for_status()
        return r.json()
