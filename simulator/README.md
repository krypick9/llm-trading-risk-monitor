# Simulator (Phase 1+)

This directory contains the LLM agent layer, experiment runner, and intervention modules
that build on top of `perp_sim/` (the Phase 0 market engine).

## Structure

```
simulator/
├── agents/              # LLM agent implementations
│   ├── base_agent.py    #   Wrapper: MarketView → prompt → LLM API → OrderRequest
│   ├── styles/          #   Value, momentum, market-maker, contrarian personas
│   └── tests/
├── interventions/       # Phase 2 intervention modules
│   ├── standard/        #   Order size cap, frequency limit, position cap, etc.
│   ├── perpetual/       #   Leverage cap, funding clamp, margin tier, ADL, etc.
│   └── tests/
├── calibration/         # Calibration scripts & configs
│   ├── targets.py       #   Stylized facts extracted from real data
│   ├── match.py         #   Match simulator params to real data
│   └── tests/
├── run_experiment.py    # Phase 1 experiment runner (extends perp_sim/run_demo.py)
└── tests/
```

## Interface Contract

The agent interface is defined in `perp_sim/engine/exchange.py`:

```python
# What an agent receives:
MarketView: step, minute, prices, order book, funding, liquidations, own account

# What an agent returns:
List[OrderRequest | CancelAll]

# The function signature:
def act(view: MarketView) -> List[OrderRequest | CancelAll]
```

This is the same interface `perp_sim/bots.py` already uses.
LLM agents replace the rule-based logic with an LLM API call.
