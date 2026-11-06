"""Configuration, loaded from CLI flags and environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

# Boom/Crash symbols as exposed by the Deriv API. The bot validates the
# chosen symbol against `active_symbols` at runtime, so this list is only
# used for CLI help and offline simulation.
BOOM_CRASH_SYMBOLS = {
    "BOOM300N": 300, "BOOM500": 500, "BOOM600": 600, "BOOM900": 900, "BOOM1000": 1000,
    "CRASH300N": 300, "CRASH500": 500, "CRASH600": 600, "CRASH900": 900, "CRASH1000": 1000,
}


def index_kind(symbol: str) -> str:
    s = symbol.upper()
    if s.startswith("BOOM"):
        return "boom"
    if s.startswith("CRASH"):
        return "crash"
    raise ValueError(f"{symbol!r} is not a Boom/Crash symbol")


def spike_interval(symbol: str) -> int:
    """Average ticks between spikes, taken from the number in the name."""
    s = symbol.upper()
    if s in BOOM_CRASH_SYMBOLS:
        return BOOM_CRASH_SYMBOLS[s]
    digits = "".join(c for c in s if c.isdigit())
    if not digits:
        raise ValueError(f"cannot infer spike interval from {symbol!r}")
    return int(digits)


@dataclass
class StrategyConfig:
    name: str = "drift"            # "drift" (ride the drift) or "spike" (catch spikes)
    ema_fast: int = 10
    ema_slow: int = 40
    spike_sigma: float = 8.0       # tick move > spike_sigma * median|move| is a spike
    cooldown_ticks: int = 20       # ticks to wait after a spike before entering
    max_hold_ticks: int = 60       # close the trade after this many ticks


@dataclass
class RiskConfig:
    stake: float = 1.0                  # fixed stake per trade (no martingale)
    multiplier: int = 100
    take_profit: float = 0.30           # in account currency
    stop_loss: float = 0.50             # in account currency
    max_daily_loss: float = 5.0
    daily_profit_target: float = 5.0
    max_trades: int = 50
    max_consecutive_losses: int = 4
    commission_rate: float = 0.0005     # backtest/paper only; live uses the broker's figure


@dataclass
class Config:
    symbol: str = "BOOM1000"
    currency: str = "USD"
    app_id: str = field(default_factory=lambda: os.getenv("DERIV_APP_ID", "1089"))
    token: str | None = field(default_factory=lambda: os.getenv("DERIV_API_TOKEN"))
    ws_url: str = field(default_factory=lambda: os.getenv(
        "DERIV_WS_URL", "wss://ws.derivws.com/websockets/v3?app_id={app_id}"))
    allow_real: bool = False
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)

    @property
    def url(self) -> str:
        return self.ws_url.format(app_id=self.app_id)
