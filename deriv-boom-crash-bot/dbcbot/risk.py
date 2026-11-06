"""Session-level risk limits. The bot stops trading once any limit is hit."""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import RiskConfig


@dataclass
class RiskManager:
    cfg: RiskConfig
    pnl: float = 0.0
    trades: int = 0
    wins: int = 0
    consecutive_losses: int = 0
    halted_reason: str | None = None
    history: list[float] = field(default_factory=list)

    def can_trade(self) -> bool:
        if self.halted_reason:
            return False
        c = self.cfg
        if self.pnl <= -abs(c.max_daily_loss):
            self.halted_reason = f"max daily loss reached ({self.pnl:.2f})"
        elif self.pnl >= abs(c.daily_profit_target):
            self.halted_reason = f"daily profit target reached ({self.pnl:.2f})"
        elif self.trades >= c.max_trades:
            self.halted_reason = f"max trades reached ({self.trades})"
        elif self.consecutive_losses >= c.max_consecutive_losses:
            self.halted_reason = f"{self.consecutive_losses} losses in a row"
        # never stake more than the remaining loss budget
        elif self.pnl - c.stake < -abs(c.max_daily_loss) - 1e-9 and c.stop_loss >= c.stake:
            self.halted_reason = "remaining loss budget is smaller than one stake"
        return self.halted_reason is None

    def record(self, profit: float) -> None:
        self.trades += 1
        self.pnl += profit
        self.history.append(profit)
        if profit > 0:
            self.wins += 1
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1

    def summary(self) -> str:
        wr = (self.wins / self.trades * 100) if self.trades else 0.0
        s = f"trades={self.trades} wins={self.wins} win_rate={wr:.1f}% pnl={self.pnl:+.2f}"
        if self.halted_reason:
            s += f" | halted: {self.halted_reason}"
        return s
