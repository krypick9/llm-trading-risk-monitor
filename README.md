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
├── perp_sim/                  # Phase 0: Synthetic perpetual-futures market
│   ├── engine/                #   core engine (orderbook, accounts, exchange, etc.)
│   ├── bots.py                #   scripted traders (temporary, replaced by LLM agents in Phase 1)
│   ├── run_demo.py            #   demo runner
│   ├── tests/                 #   41 automated checks
│   └── output/                #   demo output CSVs (gitignored)
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

# 4. Run the demo (perp_sim Phase 0)
cd perp_sim
python run_demo.py

# 5. Run the tests
python -m unittest discover -s tests -t .
```

## Phase Overview

| Phase | Question | What perp_sim provides |
|-------|----------|----------------------|
| 1. Risk discovery | How does risk change as the number of AI agents N grows? | The market + step-by-step log |
| 2. Intervention testing | Which rules (leverage cap, circuit breaker) reduce risk? | Config dials + forced-closing switch |
| 3. Detection & prediction | Can public signals warn us before a cascade? | 28 public log columns (sim-only columns are labels, not inputs) |

## Important Notes

- **All simulator numbers are placeholders** until calibrated on real data.
- The 8-scenario design is a placeholder pending team confirmation.
- LLM agents are not yet implemented — scripted bots stand in for them.
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
