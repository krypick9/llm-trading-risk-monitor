# Risk Monitoring for LLM Trading Agents

> Detection, prediction and intervention evaluation from a market supervisor's perspective.
> Target market: Crypto perpetual futures (BTC / ETH perp).

## Team

| Role | Member | Responsibility |
|------|--------|---------------|
| Simulator Lead | _Member A_ | Build & calibrate the synthetic perpetual-futures market simulator |
| Experiment Lead | _Member B_ | Run Phase 1 risk discovery + Phase 2 intervention experiments |
| ML Lead | _Member C_ | Build Phase 3 detector/predictor + validation |

## Project Structure

```
fyp-llm-risk-monitoring/
├── README.md                  ← you are here
├── .gitignore
├── .env.example               # template for API keys (never commit .env)
├── requirements.txt           # Python dependencies
├── perp_sim/                  # the simulator (minimal engine; see perp_sim/README.md)
│   ├── engine/                #   orderbook, accounts, exchange, flow, index price
│   ├── agents.py              #   one scripted archetype + the LLM agent hook
│   ├── population.py          #   fixed slots, replace-not-add swap
│   ├── run.py                 #   run one simulation
│   ├── sanity.py              #   stylized facts + power check (run before Phase 1)
│   ├── tests/                 #   63 tests
│   └── output/                #   run CSVs (gitignored)
├── legacy/                    # frozen Phase-0 material, not in the pipeline
│   ├── README.md              #   what is here and why the engine was replaced
│   └── perp_sim_phase0/       #   the Phase-0 engine + its 80 tests
├── docs/
│   ├── proposal/              # Final proposal PDF + slides
│   ├── meeting-notes/         # Professor & TA meeting notes (dated)
│   ├── reports/               # Mid-term, final report drafts
│   └── design-docs/           # Architecture decisions, API specs
├── simulator/                 # Phase 1+: LLM agents, experiment runner, interventions
│   ├── agents/                #   LLM agent implementations
│   ├── interventions/         #   Phase 2 intervention modules
│   ├── calibration/           #   Calibration scripts & configs
│   └── tests/
├── detector/                  # Phase 3: Detection & prediction model
│   ├── features/              #   Feature engineering
│   ├── models/                #   Classifier, logistic regression, escalation
│   ├── evaluation/            #   Validation, cross-market check
│   ├── viz/                   #   Visualizations (heatmap, etc.)
│   └── tests/
├── data/
│   ├── scripts/               # Data acquisition scripts
│   └── README.md              # Data dictionary, sources, access info
├── notebooks/                 # Exploratory analysis, visualization
├── configs/                   # YAML configs for experiments
├── results/                   # Experiment outputs (large files gitignored)
└── tests/                     # Integration tests across modules
```

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/krypick9/llm-trading-risk-monitor.git
cd llm-trading-risk-monitor

# 2. Set up Python environment
python3 -m venv venv
source venv/bin/activate    # macOS/Linux
# venv\Scripts\activate     # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the simulator
cd perp_sim
python3 run.py --scenario stress --steps 600 --seed 0

# 5. Check the instrument can see risk at all (run this before any experiment)
python3 sanity.py

# 6. Run the tests
python3 -m pytest tests/ -q
```

## Phase Overview

| Phase | Question | What perp_sim provides |
|-------|----------|----------------------|
| 1. Risk discovery | In a fixed population, how does replacing role-matched scripted decision-makers with LLMs change risk? | The replace-not-add harness and its fairness tests. LLM agents and the Phase-1 runner are still not implemented, and the cascade channel is not yet live (see below). |
| 2. Intervention testing | Which rules (leverage cap, circuit breaker) reduce risk? | Leverage and depth dials + the forced-closing switch. The circuit breaker was removed: it was a no-op in Phase 0 and must be rebuilt to also block aggressive limit orders and liquidation orders. |
| 3. Detection & prediction | Can public signals warn us before a cascade? | Public / sim-only log column split (`PUBLIC_FIELDS`, `SIM_ONLY_FIELDS`); sim-only columns are labels, not inputs. |

## Important Notes

- **The simulator is no longer calibrated to real BTC in absolute terms, on
  purpose.** The project compares risk *relatively* — same market, same settings,
  only the agent mix changes — so the market has to be reasonable and held fixed,
  not fitted. The calibration pipeline and its data stay in the repo as Phase-0
  evidence (`data/scripts/`, `data/processed/`) but the simulator no longer
  depends on them. `legacy/README.md` records why: the calibration never applied
  to the market the experiment ran, and it was not identified.
- **The simulator is a working test bench; the experiment is not ready.** The
  causal spine runs end to end — a dispersed, holding crowd builds leverage, the
  price falls, accounts breach at different thresholds, forced selling hits a
  book with reach that can be exhausted, the mark drags, more accounts breach.
  Forced selling moves the price by −2.9 pp of extra dip, and distinct accounts
  liquidated responds across 6 of 7 leverage levels. Always run
  `cd perp_sim && python3 sanity.py` before an experiment: it is the go/no-go
  gate, and it fails if the risk metric stops responding.
- **The four commits that got it there have not been independently reviewed.**
  They were written in one session. Before results are built on them, someone
  else should read `perp_sim/engine/flow.py`, `_bg_ladder` and
  `_bg_inventory_scales` in `perp_sim/engine/exchange.py`, and
  `agents.PositionTaker`, and re-run the parameter sweeps in
  `perp_sim/README.md`. The defaults are documented with evidence but they are
  still judgement calls.
- **LLM agents and the Phase 1 runner are not implemented**, and that — not the
  simulator — is what gates Phase 1. There is an `act(view)` hook and a skeleton
  in `perp_sim/agents.py`, no agent, no API wiring and no cost model. 50 traders
  × 600 steps is 30,000 agent decisions per run; measure cost and wall-clock on
  one small run before committing to an experiment design.
- **Pre-register Phase 1 before running it.** This market has now been inspected
  extensively — scenarios, seeds, leverage sweeps, which settings give which
  numbers. Write down the scenarios, seed count, `n_llm` points, primary metric
  and statistical test, then do not change them. The Phase-0 held-out days are
  already only a diagnostic for exactly this reason.
- **One visible validation miss**: median quoted spread is ~138× real, because
  the background LP only re-quotes once per minute while the flow trades
  throughout it. Not a dependent variable, but it is the first thing a validation
  table invites a question about. Diagnosed and scoped in `perp_sim/README.md`.
- Numbers in `data/processed/*.json` are stale: they were produced by the Phase-0
  engine at settings it no longer ships. See `legacy/README.md`.
- API keys must never be committed. Use `.env` (see `.env.example`).

## Data Sources

| Source | What | Access |
|--------|------|--------|
| CryptoHFTData | L2 book Parquet (Hyperliquid, Binance, OKX, Bybit) | Free, API key |
| Hyperliquid WS | Live L2 book, trades, funding, liquidations | Free |
| Binance bookDepth | Depth by ±% band, 30s, from Jan 2023 | Free |
| Tardis.dev | Incremental L2, 69 venues, from 2019 | Free on 1st of each month |
| 0xArchive | L2/L4 order data for Hyperliquid | ~$3/GB (crisis windows only) |

## License

This project is for academic purposes (Final Year Project, HKUST).
