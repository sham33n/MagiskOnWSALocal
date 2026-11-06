# Deriv Boom & Crash bot

A small Python bot that trades Deriv's **Boom** and **Crash** synthetic indices
with **Multiplier** contracts through the Deriv WebSocket API. It has three
modes: offline **backtest**, **paper** (live prices, simulated trades, no
token needed) and **live** (real orders, demo account by default).

> **Read this first.** Boom/Crash prices come from Deriv's own random number
> generator. Deriv says each tick is independent, so a long wait without a
> spike does **not** make a spike more likely. No strategy in this repo has
> been shown to have an edge. The backtests below lose money once commission
> is included. Use a **demo account**. Losses on real accounts are your own,
> and synthetic indices are not offered in every country (they are restricted
> in the EU and UK, for example).

---

## 1. Research summary: how Boom/Crash trading works on Deriv

### What the indices are
| Index | Normal drift | Spike | Average spike frequency |
|---|---|---|---|
| Boom 300/500/600/900/1000 | slow **down** | sharp **up** | about 1 per N ticks (N = number in the name) |
| Crash 300/500/600/900/1000 | slow **up** | sharp **down** | about 1 per N ticks |

* They are synthetic: an algorithm generates the prices. No news, sessions or
  real asset is behind them, and they trade 24/7.
* API symbols include `BOOM300N`, `BOOM500`, `BOOM1000`, `CRASH300N`,
  `CRASH500` and `CRASH1000`, plus `BOOM600/900` and `CRASH600/900` where
  offered. The bot checks your symbol against `active_symbols` at start-up.

### Where you can trade them
* **Deriv Trader / Deriv Bot (DBot)**, using Multipliers. This is the contract
  the bot uses.
* **Deriv MT5 / cTrader**, as CFDs. MT5 needs an MT5 EA (MQL5), which this
  repo does not include.
* **Deriv API (WebSocket)**, which is what this bot talks to.

### Multiplier contracts (MULTUP / MULTDOWN)
* You choose a **stake** and a **multiplier**. P/L is about
  `stake x multiplier x % price change`, minus a commission.
* Your loss can never exceed the stake: the contract is stopped out when the
  loss reaches the stake.
* You can attach `take_profit` and `stop_loss` amounts in your account
  currency. **On Boom/Crash a spike can jump past your stop**, so the
  stop-loss fill can be worse than the level you set. Deriv's Traders Academy
  has a whole article on this.
* Each symbol allows only certain multipliers. The bot reads the allowed list
  (`multiplier_range` from `contracts_for`) and moves to the nearest allowed
  value.

### Typical API flow (what `dbcbot/bot.py` does)
```jsonc
{"authorize": "<API token>"}                                  // live mode only
{"active_symbols": "brief"}                                   // validate the symbol
{"contracts_for": "BOOM1000", "currency": "USD"}              // allowed multipliers
{"ticks": "BOOM1000", "subscribe": 1}                         // price stream
{"proposal": 1, "amount": 1, "basis": "stake", "contract_type": "MULTDOWN",
 "currency": "USD", "symbol": "BOOM1000", "multiplier": 100,
 "limit_order": {"take_profit": 0.3, "stop_loss": 0.5}}       // get a quote
{"buy": "<proposal id>", "price": 1}                          // execute
{"proposal_open_contract": 1, "contract_id": 123, "subscribe": 1}  // track it
{"sell": 123, "price": 0}                                     // close at market
{"ticks_history": "BOOM1000", "end": "latest", "count": 5000, "style": "ticks"}
```

### Note about Deriv's 2026 API changes
Deriv is replacing its legacy API (`wss://ws.derivws.com/websockets/v3?app_id=…`)
with a new one at developers.deriv.com, which uses PAT/OAuth tokens and a
`Deriv-App-ID` header. The legacy API is being retired and Deriv says the new
one is **not** fully compatible with it. This bot uses the legacy JSON message
format, and the endpoint can be changed through `DERIV_WS_URL`. If your
account only works with the new API, check the current docs and adjust
`deriv_client.py`.

---

## 2. The strategies

| `--strategy` | Idea | Boom trade | Crash trade |
|---|---|---|---|
| `drift` (default) | Ride the slow drift with a tight SL, and skip `--cooldown` ticks after each spike | MULTDOWN | MULTUP |
| `spike` | After a long quiet stretch (`--spike-wait` x average interval), bet on the next spike | MULTUP | MULTDOWN |

Spikes are detected as a tick move larger than `spike_sigma` (default 8) times
the median tick move.

**Session risk controls**, all applied by default: fixed stake with **no
martingale**, a max daily loss, a daily profit target, a max number of trades,
a max number of losses in a row, and only one open contract at a time. A real
account is refused unless you pass `--allow-real`.

### What the offline backtests show
Backtests run on the built-in simulator (300k ticks, `--no-limits`). The
simulator is a fair game: its drift exactly cancels its spikes.

| | Win rate | P/L, commission 0 | P/L, default commission |
|---|---|---|---|
| drift, BOOM1000 | ~71% | **negative** (spikes jump past the SL) | more negative |
| spike, BOOM1000 | ~29% | about 0 | negative |

The drift strategy wins most of its trades and still loses money, because the
few spikes against it cost more than all the small wins. This is the usual way
Boom/Crash accounts get wiped out.

The simulator is **not** Deriv's algorithm. Use `--source deriv` to backtest on
real recent ticks (this needs a network connection to Deriv).

---

## 3. Usage

```bash
cd deriv-boom-crash-bot
pip install -r requirements.txt

# offline backtest
python -m dbcbot backtest --symbol BOOM1000 --strategy drift --ticks 200000 --seed 1

# backtest on real Deriv tick history (up to 5000 ticks per request, paged)
python -m dbcbot backtest --source deriv --symbol CRASH500 --ticks 20000

# paper trading on live prices (no token, no orders)
python -m dbcbot paper --symbol BOOM500 --strategy drift

# live trading on a DEMO account
export DERIV_API_TOKEN=...      # Deriv > Settings > API token, scopes: Read + Trade
export DERIV_APP_ID=1089        # or your own registered app id
python -m dbcbot live --symbol BOOM1000 --stake 1 --multiplier 100 \
       --take-profit 0.3 --stop-loss 0.5 --max-daily-loss 5
```

Run `python -m dbcbot -h` to see every option. Environment variables:
`DERIV_API_TOKEN`, `DERIV_APP_ID`, `DERIV_WS_URL`.

Tests (they use a local fake WebSocket server, so no network is needed):
```bash
pip install pytest && python -m pytest -q
```

### Layout
```
dbcbot/config.py        settings and symbol helpers
dbcbot/strategy.py      spike detector, EMA, drift/spike signals (pure logic)
dbcbot/risk.py          session risk limits
dbcbot/simulator.py     synthetic Boom/Crash ticks and a multiplier P/L model
dbcbot/backtest.py      offline backtester
dbcbot/deriv_client.py  async WebSocket client (req_id routing, subscriptions)
dbcbot/bot.py           paper/live trading loop
```

## Sources
* Deriv: [Crash/Boom indices](https://deriv.com/derived-indices/crash-boom-indices)
* Deriv Traders Academy: [Why stop-loss closes at a different price on Boom/Crash](https://traders-academy.deriv.be/trading-guides/stop-loss-execution-boom-crash-indices)
* Deriv blog: [Synthetic index families](https://blog.deriv.com/blog/synthetic-indices-overview-volatility-crash-boom-jump)
* Deriv KID: [Multipliers on Crash/Boom](https://docs.deriv.com/regulatory/kid/kid_deriv_multipliers_crash_boom.pdf)
* Deriv developers: [API overview](https://developers.deriv.com/docs/intro/api-overview), [authentication](https://developers.deriv.com/docs/intro/authentication), [product updates](https://developers.deriv.com/product-updates/)
