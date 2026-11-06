"""Tick-driven signal logic. Pure Python, no I/O, so it is easy to test.

Boom indices drift *down* and spike *up*; Crash indices drift *up* and drop
*down*. Two classic retail approaches are implemented:

* ``drift``  - trade *with* the slow drift (SELL Boom / BUY Crash) and rely on a
               tight stop loss to cap the damage when a spike hits.
* ``spike``  - trade *in the direction of the spike* (BUY Boom / SELL Crash)
               after a long quiet stretch, hoping to catch the next spike.

Deriv states that ticks are independent: a long quiet stretch does NOT make a
spike more likely. Neither strategy is expected to have an edge; the backtester
exists so you can see that for yourself before risking money.
"""
from __future__ import annotations

import statistics
from collections import deque
from typing import Optional

from .config import StrategyConfig, index_kind, spike_interval

UP, DOWN = "UP", "DOWN"


class SpikeDetector:
    """Flags a tick as a spike when its move dwarfs the typical tick move."""

    def __init__(self, kind: str, sigma: float, window: int = 300):
        self.kind = kind
        self.sigma = sigma
        self.moves: deque[float] = deque(maxlen=window)
        self.last_price: Optional[float] = None

    def update(self, price: float) -> bool:
        if self.last_price is None:
            self.last_price = price
            return False
        move = price - self.last_price
        self.last_price = price
        is_spike = False
        if len(self.moves) >= 30:
            typical = statistics.median(self.moves) or 1e-12
            if abs(move) > self.sigma * typical:
                is_spike = (move > 0) if self.kind == "boom" else (move < 0)
        if not is_spike:
            # keep spikes out of the baseline so they don't inflate it
            self.moves.append(abs(move))
        return is_spike


class EMA:
    def __init__(self, period: int):
        self.k = 2.0 / (period + 1)
        self.value: Optional[float] = None
        self.count = 0

    def update(self, x: float) -> float:
        self.value = x if self.value is None else self.value + self.k * (x - self.value)
        self.count += 1
        return self.value


class Strategy:
    def __init__(self, symbol: str, cfg: StrategyConfig, spike_wait_factor: float = 1.0):
        self.cfg = cfg
        self.kind = index_kind(symbol)
        self.interval = spike_interval(symbol)
        self.spike_wait_factor = spike_wait_factor
        self.detector = SpikeDetector(self.kind, cfg.spike_sigma)
        self.fast = EMA(cfg.ema_fast)
        self.slow = EMA(cfg.ema_slow)
        self.ticks = 0
        self.ticks_since_spike = 0
        self.spikes = 0

    @property
    def drift_direction(self) -> str:
        return DOWN if self.kind == "boom" else UP

    @property
    def spike_direction(self) -> str:
        return UP if self.kind == "boom" else DOWN

    def on_tick(self, price: float) -> tuple[bool, Optional[str]]:
        """Feed one tick. Returns (spike_detected, entry_signal_or_None)."""
        self.ticks += 1
        spiked = self.detector.update(price)
        if spiked:
            self.spikes += 1
            self.ticks_since_spike = 0
        else:
            self.ticks_since_spike += 1
        fast, slow = self.fast.update(price), self.slow.update(price)

        warm = self.slow.count >= self.cfg.ema_slow and len(self.detector.moves) >= 30
        if not warm:
            return spiked, None

        if self.cfg.name == "drift":
            if self.ticks_since_spike < self.cfg.cooldown_ticks:
                return spiked, None
            trending = fast < slow if self.kind == "boom" else fast > slow
            return spiked, (self.drift_direction if trending else None)

        if self.cfg.name == "spike":
            if self.ticks_since_spike >= self.interval * self.spike_wait_factor:
                return spiked, self.spike_direction
            return spiked, None

        raise ValueError(f"unknown strategy {self.cfg.name!r}")
