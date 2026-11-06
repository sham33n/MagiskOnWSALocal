import asyncio
import json

import pytest

from dbcbot.backtest import run_backtest
from dbcbot.config import Config, RiskConfig, StrategyConfig, index_kind, spike_interval
from dbcbot.risk import RiskManager
from dbcbot.simulator import SimPosition, synthetic_ticks
from dbcbot.strategy import DOWN, UP, SpikeDetector, Strategy


def test_symbol_helpers():
    assert index_kind("BOOM1000") == "boom"
    assert index_kind("crash500") == "crash"
    assert spike_interval("BOOM300N") == 300
    with pytest.raises(ValueError):
        index_kind("R_100")


def test_spike_detector_flags_only_spikes_in_index_direction():
    d = SpikeDetector("boom", sigma=8)
    p = 100.0
    for i in range(100):
        p -= 0.01 if i % 2 else 0.012
        assert not d.update(p)
    assert d.update(p + 1.0)            # up-spike on Boom
    assert not d.update(p - 1.0)        # down move on Boom is not a "spike"


def test_drift_strategy_waits_for_cooldown_and_trades_with_drift():
    cfg = StrategyConfig(name="drift", ema_fast=3, ema_slow=8, cooldown_ticks=10)
    s = Strategy("BOOM1000", cfg)
    p, signals = 1000.0, []
    for _ in range(60):
        p -= 0.01
        signals.append(s.on_tick(p)[1])
    assert DOWN in signals and UP not in signals
    s.on_tick(p + 5)                    # spike resets cooldown
    assert all(s.on_tick(p + 5 - 0.01 * i)[1] is None for i in range(1, 9))


def test_spike_strategy_direction():
    s = Strategy("CRASH500", StrategyConfig(name="spike", ema_slow=5), spike_wait_factor=0.1)
    p, sig = 1000.0, None
    for _ in range(200):
        p += 0.01
        sig = s.on_tick(p)[1] or sig
    assert sig == DOWN


def test_position_tp_sl_and_stop_out():
    r = RiskConfig(stake=1, multiplier=100, take_profit=0.3, stop_loss=0.5, commission_rate=0)
    pos = SimPosition(UP, 100.0, r)
    assert pos.update(100.31, 99)[1] == "take_profit"
    pos = SimPosition(DOWN, 100.0, r)
    profit, reason = pos.update(100.6, 99)
    assert reason == "stop_loss" and profit == pytest.approx(-0.6)   # gapped past SL
    pos = SimPosition(DOWN, 100.0, r)
    assert pos.update(102.0, 99) == (-1, "stop_out")                  # loss capped at stake
    pos = SimPosition(UP, 100.0, r)
    assert pos.update(100.0, 1)[1] == "max_hold"


def test_risk_manager_halts():
    rm = RiskManager(RiskConfig(max_consecutive_losses=2, max_daily_loss=100))
    rm.record(-0.5); rm.record(-0.5)
    assert not rm.can_trade() and "in a row" in rm.halted_reason
    rm = RiskManager(RiskConfig(max_daily_loss=1.0, stake=1.0, stop_loss=1.0))
    rm.record(-1.0)
    assert not rm.can_trade()
    rm = RiskManager(RiskConfig(daily_profit_target=1.0))
    rm.record(1.5)
    assert not rm.can_trade()


def test_synthetic_ticks_are_deterministic_and_spiky():
    a = list(synthetic_ticks("BOOM500", 20000, seed=3))
    assert a == list(synthetic_ticks("BOOM500", 20000, seed=3))
    ups = sum(1 for x, y in zip(a, a[1:]) if (y - x) / x > 5e-4)
    assert 20 < ups < 70                 # ~1 spike per 500 ticks


def test_backtest_respects_limits():
    cfg = Config(symbol="BOOM1000")
    cfg.risk.max_trades = 5
    res = run_backtest(cfg, synthetic_ticks("BOOM1000", 50000, seed=1))
    assert res.risk.trades <= 5


# --------------------------------------------------- client against fake server
def test_client_request_and_subscription():
    websockets = pytest.importorskip("websockets")
    from dbcbot.deriv_client import DerivAPIError, DerivClient

    async def handler(ws):
        async for raw in ws:
            req = json.loads(raw)
            rid = req["req_id"]
            if "ping" in req:
                await ws.send(json.dumps({"req_id": rid, "msg_type": "ping", "ping": "pong"}))
            elif "ticks" in req:
                for q in (1.0, 2.0, 3.0):
                    await ws.send(json.dumps({"req_id": rid, "msg_type": "tick",
                                              "tick": {"quote": q},
                                              "subscription": {"id": "abc"}}))
            elif "buy" in req:
                await ws.send(json.dumps({"req_id": rid, "error":
                                          {"code": "InvalidPrice", "message": "nope"}}))

    async def main():
        async with websockets.serve(handler, "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            async with DerivClient(f"ws://127.0.0.1:{port}") as c:
                assert (await c.send({"ping": 1}))["ping"] == "pong"
                quotes = []
                async for m in c.subscribe({"ticks": "BOOM1000"}):
                    quotes.append(m["tick"]["quote"])
                    if len(quotes) == 3:
                        break
                assert quotes == [1.0, 2.0, 3.0]
                with pytest.raises(DerivAPIError):
                    await c.send({"buy": "x", "price": 1})

    asyncio.run(main())


def test_paper_bot_end_to_end_against_fake_server():
    websockets = pytest.importorskip("websockets")
    from dbcbot.bot import TradingBot

    prices = list(synthetic_ticks("BOOM1000", 30000, seed=5))

    async def handler(ws):
        async for raw in ws:
            req = json.loads(raw)
            rid = req["req_id"]
            if "active_symbols" in req:
                await ws.send(json.dumps({"req_id": rid, "active_symbols": [
                    {"symbol": "BOOM1000", "display_name": "Boom 1000 Index",
                     "exchange_is_open": 1}]}))
            elif "contracts_for" in req:
                await ws.send(json.dumps({"req_id": rid, "contracts_for": {"available": [
                    {"contract_type": "MULTUP", "multiplier_range": [100, 200, 300]}]}}))
            elif "ticks" in req:
                for q in prices:
                    await ws.send(json.dumps({"req_id": rid, "tick": {"quote": q},
                                              "subscription": {"id": "t"}}))

    async def main():
        async with websockets.serve(handler, "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            cfg = Config(symbol="BOOM1000", ws_url=f"ws://127.0.0.1:{port}")
            cfg.risk.multiplier = 150             # not allowed -> snapped to 100 or 200
            cfg.risk.max_trades = 3
            bot = TradingBot(cfg, paper=True)
            rm = await asyncio.wait_for(bot.run(), 30)
            assert cfg.risk.multiplier in (100, 200)
            assert rm.trades == 3 and rm.halted_reason

    asyncio.run(main())
