"""CLI:  python -m dbcbot {backtest|paper|live} [options]"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .backtest import run_backtest
from .config import BOOM_CRASH_SYMBOLS, Config
from .simulator import synthetic_ticks


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dbcbot", description="Deriv Boom/Crash trading bot")
    p.add_argument("mode", choices=["backtest", "paper", "live"])
    p.add_argument("--symbol", default="BOOM1000", help=", ".join(BOOM_CRASH_SYMBOLS))
    p.add_argument("--strategy", choices=["drift", "spike"], default="drift")
    p.add_argument("--stake", type=float, default=1.0)
    p.add_argument("--multiplier", type=int, default=100)
    p.add_argument("--take-profit", type=float, default=0.30)
    p.add_argument("--stop-loss", type=float, default=0.50)
    p.add_argument("--max-daily-loss", type=float, default=5.0)
    p.add_argument("--profit-target", type=float, default=5.0)
    p.add_argument("--max-trades", type=int, default=50)
    p.add_argument("--max-consecutive-losses", type=int, default=4)
    p.add_argument("--max-hold", type=int, default=60, help="ticks")
    p.add_argument("--cooldown", type=int, default=20, help="ticks after a spike (drift)")
    p.add_argument("--spike-wait", type=float, default=1.0,
                   help="spike strategy: enter after N x average spike interval of quiet")
    p.add_argument("--commission", type=float, default=0.0005,
                   help="commission rate for backtest/paper (fraction of stake x multiplier)")
    p.add_argument("--allow-real", action="store_true", help="permit a REAL-money account")
    bt = p.add_argument_group("backtest")
    bt.add_argument("--source", choices=["synthetic", "deriv"], default="synthetic")
    bt.add_argument("--ticks", type=int, default=100_000)
    bt.add_argument("--seed", type=int, default=None)
    bt.add_argument("--no-limits", action="store_true",
                    help="ignore session risk limits (to measure raw edge)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def config_from_args(a: argparse.Namespace) -> Config:
    cfg = Config(symbol=a.symbol.upper(), allow_real=a.allow_real)
    s, r = cfg.strategy, cfg.risk
    s.name, s.max_hold_ticks, s.cooldown_ticks = a.strategy, a.max_hold, a.cooldown
    r.stake, r.multiplier = a.stake, a.multiplier
    r.take_profit, r.stop_loss = a.take_profit, a.stop_loss
    r.max_daily_loss, r.daily_profit_target = a.max_daily_loss, a.profit_target
    r.max_trades, r.max_consecutive_losses = a.max_trades, a.max_consecutive_losses
    r.commission_rate = a.commission
    return cfg


async def _fetch(cfg: Config, n: int) -> list[float]:
    from .deriv_client import DerivClient
    async with DerivClient(cfg.url) as c:
        return await c.tick_history(cfg.symbol, n)


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s")
    cfg = config_from_args(a)

    if a.mode == "backtest":
        if a.source == "deriv":
            prices = asyncio.run(_fetch(cfg, a.ticks))
            print(f"fetched {len(prices)} real ticks for {cfg.symbol}")
        else:
            prices = synthetic_ticks(cfg.symbol, a.ticks, a.seed)
        res = run_backtest(cfg, prices, a.spike_wait, enforce_limits=not a.no_limits)
        print(res.report())
        return 0

    from .bot import TradingBot
    bot = TradingBot(cfg, paper=(a.mode == "paper"), spike_wait_factor=a.spike_wait)
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        print("\ninterrupted:", bot.risk.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
