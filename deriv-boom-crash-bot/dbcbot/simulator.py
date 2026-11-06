"""Offline stand-in for Boom/Crash prices plus a local multiplier-contract model.

The tick generator is an *approximation* for testing and backtesting only; it
is not Deriv's algorithm. It is built as a fair game: the expected drift
exactly cancels the expected spike, so any strategy's expected profit before
costs is ~0 and after commission is negative - which is the honest baseline to
compare against. Prefer `--source deriv` (real tick history) when you can.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Iterator, Optional

from .config import RiskConfig, index_kind, spike_interval
from .strategy import UP


def synthetic_ticks(symbol: str, n: int, seed: Optional[int] = None,
                    start: float = 10_000.0, noise: float = 2e-5,
                    spike_mean: float = 2e-3) -> Iterator[float]:
    rng = random.Random(seed)
    sign = 1 if index_kind(symbol) == "boom" else -1
    p = 1.0 / spike_interval(symbol)
    drift = -sign * spike_mean * p          # cancels the expected spike
    price = start
    for _ in range(n):
        r = drift + rng.gauss(0, noise)
        if rng.random() < p:
            r += sign * rng.expovariate(1.0 / spike_mean)
        price *= 1 + r
        yield round(price, 4)


@dataclass
class SimPosition:
    """Local model of a Deriv Multiplier contract (MULTUP / MULTDOWN)."""
    direction: str
    entry: float
    risk: RiskConfig
    ticks_held: int = 0

    @property
    def commission(self) -> float:
        return self.risk.stake * self.risk.multiplier * self.risk.commission_rate

    def profit_at(self, price: float) -> float:
        d = 1 if self.direction == UP else -1
        gross = self.risk.stake * self.risk.multiplier * (price / self.entry - 1) * d
        return max(gross - self.commission, -self.risk.stake)   # stop-out floor

    def update(self, price: float, max_hold: int) -> Optional[tuple[float, str]]:
        """Returns (profit, reason) when the position closes on this tick."""
        self.ticks_held += 1
        pnl = self.profit_at(price)
        r = self.risk
        if pnl <= -r.stake + 1e-12:
            return -r.stake, "stop_out"
        if pnl <= -r.stop_loss:
            return pnl, "stop_loss"      # filled at this tick's price -> slippage on spikes
        if pnl >= r.take_profit:
            return pnl, "take_profit"
        if self.ticks_held >= max_hold:
            return pnl, "max_hold"
        return None
