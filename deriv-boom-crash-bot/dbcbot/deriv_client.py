"""Minimal async client for the Deriv WebSocket API (request/response + streams).

Every request gets a ``req_id``; responses and subscription updates echo it,
which is how messages are routed back to the caller.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
from typing import Any, AsyncIterator

import websockets

log = logging.getLogger("dbcbot.api")


class DerivAPIError(RuntimeError):
    def __init__(self, error: dict[str, Any], request: dict[str, Any] | None = None):
        self.code = error.get("code")
        self.error = error
        super().__init__(f"{self.code}: {error.get('message')}"
                         + (f" (request: {next(iter(request))})" if request else ""))


class DerivClient:
    def __init__(self, url: str):
        self.url = url
        self.ws = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._streams: dict[int, asyncio.Queue] = {}
        self._reader: asyncio.Task | None = None

    async def __aenter__(self) -> "DerivClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def connect(self) -> None:
        self.ws = await websockets.connect(self.url, ping_interval=20, ping_timeout=20,
                                           max_size=2**24)
        self._reader = asyncio.create_task(self._read_loop())

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
        if self.ws:
            await self.ws.close()

    async def _read_loop(self) -> None:
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                rid = msg.get("req_id")
                if rid in self._streams:
                    self._streams[rid].put_nowait(msg)
                fut = self._pending.pop(rid, None)
                if fut and not fut.done():
                    fut.set_result(msg)
        except Exception as e:                       # connection dropped
            log.error("websocket reader stopped: %s", e)
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError(str(e)))
            for q in self._streams.values():
                q.put_nowait({"error": {"code": "Disconnected", "message": str(e)}})
            raise

    async def send(self, request: dict[str, Any], timeout: float = 30) -> dict[str, Any]:
        rid = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self.ws.send(json.dumps({**request, "req_id": rid}))
        msg = await asyncio.wait_for(fut, timeout)
        if "error" in msg:
            raise DerivAPIError(msg["error"], request)
        return msg

    async def subscribe(self, request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """Yields every message for a subscription until the caller stops iterating."""
        rid = next(self._ids)
        q: asyncio.Queue = asyncio.Queue()
        self._streams[rid] = q
        sub_id = None
        await self.ws.send(json.dumps({**request, "subscribe": 1, "req_id": rid}))
        try:
            while True:
                msg = await q.get()
                if "error" in msg:
                    raise DerivAPIError(msg["error"], request)
                sub_id = (msg.get("subscription") or {}).get("id", sub_id)
                yield msg
        finally:
            self._streams.pop(rid, None)
            if sub_id and self.ws and self.ws.state.name == "OPEN":
                try:
                    await self.ws.send(json.dumps({"forget": sub_id}))
                except Exception:
                    pass

    # ---- convenience wrappers -------------------------------------------
    async def authorize(self, token: str) -> dict[str, Any]:
        return (await self.send({"authorize": token}))["authorize"]

    async def active_symbol(self, symbol: str) -> dict[str, Any] | None:
        res = await self.send({"active_symbols": "brief"})
        return next((s for s in res["active_symbols"] if s["symbol"] == symbol), None)

    async def multiplier_range(self, symbol: str, currency: str) -> list[int]:
        res = await self.send({"contracts_for": symbol, "currency": currency})
        for c in res["contracts_for"]["available"]:
            if c.get("contract_type") in ("MULTUP", "MULTDOWN") and c.get("multiplier_range"):
                return [int(m) for m in c["multiplier_range"]]
        return []

    async def tick_history(self, symbol: str, count: int) -> list[float]:
        """Most recent `count` tick prices, oldest first (paged, 5000 per call)."""
        prices: list[float] = []
        end: Any = "latest"
        while len(prices) < count:
            n = min(5000, count - len(prices))
            res = await self.send({"ticks_history": symbol, "end": end, "count": n,
                                   "style": "ticks"})
            h = res["history"]
            if not h["prices"]:
                break
            prices = [float(p) for p in h["prices"]] + prices
            end = int(h["times"][0]) - 1
            if len(h["prices"]) < n:
                break
        return prices
