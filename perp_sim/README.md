# perp_sim: Phase 0 demo (crypto perpetual-futures market)

A small simulated exchange for the FYP "Risk Monitoring for LLM Trading Agents".
Scripted bots (no AI, no API cost) trade on it so we can check that the exchange
behaves like a perpetual-futures market and that a liquidation cascade is *possible*.
In Phase 1 the LLM agents replace the bots.

## Run it

Needs Python 3.10 or newer. Nothing to install.

    python run_demo.py                                   # the demo (about 10 seconds)
    python -m unittest discover -s tests -t .            # the 41 checks

The demo prints text only and writes the full step-by-step record of one run to
`output/demo_stress-thin-early_seed1.csv`.

## What is in the box

| File | What it does |
|---|---|
| `engine/config.py` | Every dial in one place. **Run length and step length live here** (`n_steps`, `step_minutes`). Change those two numbers to scale the demo up. |
| `engine/orderbook.py` | Order book with price-time priority. |
| `engine/accounts.py` | Cash, position, leverage, margin, liquidation price. |
| `engine/index_price.py` | The outside "reference price" (synthetic jump-diffusion, with one scheduled shock). Can later be swapped for real recorded prices. |
| `engine/exchange.py` | The market loop: price moves, orders, leverage limits, hourly funding, forced liquidation. |
| `engine/scenarios.py` | The 8 scenarios (normal/stress x thick/thin liquidity x early/late shock). |
| `engine/records.py` | The per-step log. Columns are split into PUBLIC (what a real venue shows) and SIM-ONLY, so a future detector can be restricted to public data. |
| `bots.py` | Scripted traders (crowd, trend, contrarian, value, maker). They use the same `act(view)` interface the LLM agents will. |
| `run_demo.py` | The demo. |
| `tests/` | 41 automated checks. |

## Read this before trusting any number

* **Every number is a placeholder**: fees, funding formula, maintenance margin, liquidity depth, volatility, shock sizes. None is calibrated to real data yet.
* The 8-scenario design is a guess pending team confirmation.
* Whether a cascade forms depends heavily on two dials in `config.py` (`bg_anchor_strength`, and how far the background book reaches). Those are the first things to calibrate on real order-book data.
* With a 5x leverage cap and 2.5% maintenance margin, a trader is only liquidated after roughly a 17.5% adverse move, so cascades may be rare at the plan's default cap.
