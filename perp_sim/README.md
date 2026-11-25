# perp_sim: calibrated minimarket environment (Phase 0)

`perp_sim` is a research simulator for controlled perpetual-futures experiments. It includes a continuous limit order book, trader accounts and leverage checks, a simplified funding transfer, an exogenous reference-price process or historical replay, a background liquidity provider, forced liquidation, and per-step records.

The current scripted-bot population and calibration scripts are for **environment development**. They do not implement the final 100-trader Phase 1 replacement experiment and do not represent a validated sample of human traders. LLM agents and the Phase 1 runner are not yet implemented.

## Run

From this directory:

```bash
python run_demo.py
python -m unittest discover -s tests -t .
```

From the repository root, calibration examples are:

```bash
python3 data/scripts/run_calibration.py --quick
python3 data/scripts/run_calibration.py --quick --include-crowd
```

The demo writes a step-by-step CSV under `output/`.

## One tick

All traders first receive a view of the prior published state and return actions. The runner collects those actions before applying any trader order. `Exchange.step(actions)` then:

1. advances the external reference-price process;
2. expires orders and refreshes the background liquidity ladder;
3. shuffles and processes trader orders through the price-time-priority book;
4. checks margin and submits forced liquidation orders;
5. samples the exchange/reference premium and settles funding when due;
6. records bad debt and the market/account summary for the tick.

This is a simplified synchronous market model, not a complete real-exchange implementation.

## What the model does and does not claim

- Market parameters are informed by real-market data, and the environment is scaled so a modest number of representative accounts can affect the book.
- **Background taker flow** (`engine/flow.py`, enable with `bg_flow_enabled`): the demand side of the market -- state-dependent aggressive order flow (Queue-Reactive-style volatility-excitement and book-imbalance modulation), calibrated with `data/scripts/calibrate_market_scale.py`. One scale factor sets BOTH flow and near depth so the flow/depth ratio (real ~0.39) -- and hence normalized price impact -- is inherited from real data.
- **Backstop LP** (`bg_backstop_only`): with participant makers present, the background LP ladder starts beyond the best participant quotes instead of overlapping them; measured spread/depth then come from participant makers with the LP as backstop. The ladder is re-fitted to span the +-0.10% near-depth band (`bg_level_gap=0.0002`).
- `n_agent_orders` in the step records includes background flow orders when the flow is enabled; volume/signed-flow metrics always include all taker flow (matching real aggTrades definitions).
- Scenario matrix separates synthetic no-event, synthetic scheduled-event, and historical replay paths. Historical replay is an exogenous reference-price path; agents do not cause that outside price move, and replay scenarios have no extra scheduled shock.
- The background liquidity provider is a single simplified counterparty, not a population of real market makers. Its configured half-spread is only an initial target; with the current $0.10 tick and $100,000 price, round-to-tick behavior determines the effective minimum spread and must be checked against the observed spread.
- Current calibration reports show material gaps in simulated activity/volatility. Read the synchronized implementation plan and current calibration report before using outputs as evidence.
- When configured, `total_trader_collateral_budget` bounds initial trader collateral and excludes the effectively solvent background account. The generic engine default is unbounded; experiment configs should set an explicit budget. It is not a cap on gross futures notional, open interest, or all future PnL/funding balances.

## Current limitations and status

There are 13 scripted bots in the default demo. Calibration scripts construct 50 bots using the Crowd-excluded Phase 1 candidate role mix by default; pass `--include-crowd` to run the legacy Crowd-heavy population as a separate mechanism sensitivity. The `CrowdBot` deliberately builds crowded leveraged long positions and is not a neutral normal trader. The shared population builder can also make a 100-slot candidate mix, but the role-stratified LLM replacement runner, LLM bot, and confirmatory Phase 1 results are not implemented.

See `docs/design-docs/implementation_plan_v3.md` for the current phase gates and unresolved design decisions.
**Validation status:** Index-to-local price transmission has only been partially improved by the provisional LP anchor setting (`bg_anchor_strength=0.8`). After a one-seed quick smoke fit, local traded-price volatility is around 0.74x the held-out target, but about 60% of minutes show no local price change versus 0.56% in real data; clustering is slightly outside tolerance and tails are about 2.5x target. Quotes following the index do not guarantee trades; local last price changes only on fills. Run full multi-seed calibration and fresh, pre-registered validation before treating these settings as accepted.
