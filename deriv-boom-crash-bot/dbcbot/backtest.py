"""Run a strategy over a tick series with the local multiplier model."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

from .config import Config
from .risk import RiskManager
from .simulator import SimPosition
from .strategy import Strategy


@dataclass
class BacktestResult:
    risk: RiskManager
    exits: Counter = field(default_factory=Counter)
    ticks: int = 0
    spikes: int = 0

    def report(self) -> str:
        r = self.risk
        avg = (r.pnl / r.trades) if r.trades else 0.0
        lines = [
            f"ticks={self.ticks} spikes_detected={self.spikes}",
            r.summary(),
            f"avg_pnl_per_trade={avg:+.4f}",
            "exits: " + ", ".join(f"{k}={v}" for k, v in sorted(self.exits.items())),
        ]
        return "\n".join(lines)


def run_backtest(cfg: Config, prices: Iterable[float], spike_wait_factor: float = 1.0,
                 enforce_limits: bool = True) -> BacktestResult:
    strat = Strategy(cfg.symbol, cfg.strategy, spike_wait_factor)
    risk = RiskManager(cfg.risk)
    res = BacktestResult(risk=risk)
    pos: SimPosition | None = None
    for price in prices:
        res.ticks += 1
        spiked, signal = strat.on_tick(price)
        res.spikes += spiked
        if pos is not None:
            closed = pos.update(price, cfg.strategy.max_hold_ticks)
            if closed:
                profit, reason = closed
                risk.record(profit)
                res.exits[reason] += 1
                pos = None
            continue
        if signal and (risk.can_trade() if enforce_limits else True):
            pos = SimPosition(signal, price, cfg.risk)
    return res
