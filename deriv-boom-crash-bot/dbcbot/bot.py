"""Live / paper trading loop against the Deriv WebSocket API."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .config import Config
from .deriv_client import DerivAPIError, DerivClient
from .risk import RiskManager
from .simulator import SimPosition
from .strategy import UP, Strategy

log = logging.getLogger("dbcbot")


class TradingBot:
    def __init__(self, cfg: Config, paper: bool, spike_wait_factor: float = 1.0):
        self.cfg = cfg
        self.paper = paper
        self.strategy = Strategy(cfg.symbol, cfg.strategy, spike_wait_factor)
        self.risk = RiskManager(cfg.risk)
        self.client = DerivClient(cfg.url)
        self.paper_pos: SimPosition | None = None
        self.live_task: asyncio.Task | None = None
        self.ticks_in_trade = 0
        self.currency = cfg.currency

    # ------------------------------------------------------------------ setup
    async def _prepare(self) -> None:
        if not self.paper:
            if not self.cfg.token:
                raise SystemExit("live mode needs DERIV_API_TOKEN (use a DEMO account token)")
            acct = await self.client.authorize(self.cfg.token)
            self.currency = acct.get("currency") or self.currency
            virtual = bool(acct.get("is_virtual"))
            log.info("authorized %s (%s) balance=%s %s", acct.get("loginid"),
                     "DEMO" if virtual else "REAL", acct.get("balance"), self.currency)
            if not virtual and not self.cfg.allow_real:
                raise SystemExit("refusing to trade a REAL account; pass --allow-real if you "
                                 "really mean it")

        info = await self.client.active_symbol(self.cfg.symbol)
        if not info:
            raise SystemExit(f"symbol {self.cfg.symbol} is not offered on this account/region")
        if not info.get("exchange_is_open", 1):
            raise SystemExit(f"{self.cfg.symbol} market is closed")
        log.info("market: %s (%s)", info.get("display_name"), self.cfg.symbol)

        allowed = await self.client.multiplier_range(self.cfg.symbol, self.currency)
        if allowed and self.cfg.risk.multiplier not in allowed:
            chosen = min(allowed, key=lambda m: abs(m - self.cfg.risk.multiplier))
            log.warning("multiplier x%d not allowed (allowed: %s); using x%d",
                        self.cfg.risk.multiplier, allowed, chosen)
            self.cfg.risk.multiplier = chosen
        log.info("allowed multipliers: %s", allowed or "unknown")

    # ------------------------------------------------------------ main loop
    async def run(self) -> RiskManager:
        async with self.client:
            await self._prepare()
            mode = "PAPER" if self.paper else "LIVE"
            log.info("%s trading %s with strategy=%s stake=%.2f x%d TP=%.2f SL=%.2f",
                     mode, self.cfg.symbol, self.cfg.strategy.name, self.cfg.risk.stake,
                     self.cfg.risk.multiplier, self.cfg.risk.take_profit, self.cfg.risk.stop_loss)
            try:
                async for msg in self.client.subscribe({"ticks": self.cfg.symbol}):
                    price = float(msg["tick"]["quote"])
                    if await self._on_tick(price):
                        break
            finally:
                if self.live_task and not self.live_task.done():
                    log.warning("stopping: waiting for the open contract to close")
                    self.ticks_in_trade = 10**9        # trigger max-hold sell
                    try:
                        await asyncio.wait_for(self.live_task, 30)
                    except Exception as e:
                        log.error("could not confirm contract closed: %s - check Deriv", e)
        log.info("session finished: %s", self.risk.summary())
        return self.risk

    async def _on_tick(self, price: float) -> bool:
        """Returns True when the session should end."""
        spiked, signal = self.strategy.on_tick(price)
        if spiked:
            log.info("spike detected at %.4f", price)

        busy = self.paper_pos is not None or (self.live_task and not self.live_task.done())
        if busy:
            self.ticks_in_trade += 1
            if self.paper_pos:
                closed = self.paper_pos.update(price, self.cfg.strategy.max_hold_ticks)
                if closed:
                    self._closed(*closed)
                    self.paper_pos = None
            return False

        if not self.risk.can_trade():
            log.warning("risk limit: %s", self.risk.halted_reason)
            return True
        if signal:
            self.ticks_in_trade = 0
            log.info("signal %s at %.4f", signal, price)
            if self.paper:
                self.paper_pos = SimPosition(signal, price, self.cfg.risk)
            else:
                self.live_task = asyncio.create_task(self._live_trade(signal))
        return False

    def _closed(self, profit: float, reason: str) -> None:
        self.risk.record(profit)
        log.info("closed (%s) profit=%+.2f | %s", reason, profit, self.risk.summary())

    # ---------------------------------------------------------- live trade
    async def _live_trade(self, direction: str) -> None:
        r = self.cfg.risk
        req: dict[str, Any] = {
            "proposal": 1, "amount": r.stake, "basis": "stake",
            "contract_type": "MULTUP" if direction == UP else "MULTDOWN",
            "currency": self.currency, "symbol": self.cfg.symbol, "multiplier": r.multiplier,
            "limit_order": {"take_profit": r.take_profit, "stop_loss": r.stop_loss},
        }
        try:
            prop = (await self.client.send(req))["proposal"]
            buy = (await self.client.send({"buy": prop["id"], "price": r.stake}))["buy"]
        except DerivAPIError as e:
            log.error("order rejected: %s", e)
            return
        cid = buy["contract_id"]
        log.info("bought %s contract_id=%s price=%s commission=%s", req["contract_type"], cid,
                 buy.get("buy_price"), prop.get("commission"))
        sell_sent = False
        try:
            async for msg in self.client.subscribe({"proposal_open_contract": 1,
                                                    "contract_id": cid}):
                poc = msg["proposal_open_contract"]
                if poc.get("is_sold"):
                    reason = poc.get("status", "sold")
                    self._closed(float(poc.get("profit", 0)), reason)
                    return
                if (not sell_sent and self.ticks_in_trade >= self.cfg.strategy.max_hold_ticks
                        and poc.get("is_valid_to_sell")):
                    sell_sent = True
                    try:
                        await self.client.send({"sell": cid, "price": 0})
                        log.info("max hold reached - sell sent for %s", cid)
                    except DerivAPIError as e:
                        log.error("sell failed: %s", e)
                        sell_sent = False
        except DerivAPIError as e:
            log.error("lost track of contract %s: %s (check it on Deriv)", cid, e)
