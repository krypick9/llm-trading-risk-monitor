# Detector (Phase 3)

The detection and prediction system that reads only public market signals.

## Structure

```
detector/
├── features/            # Feature engineering
│   ├── pipeline.py      #   Rolling windows, rates of change, funding/liquidation/OI
│   └── tests/
├── models/              # Trained models
│   ├── logreg.pkl       #   Logistic regression (transparent benchmark)
│   ├── rf.pkl           #   Random forest (multi-label)
│   ├── gbm.pkl          #   Gradient boosting
│   └── escalation.py    #   P(escalation within k steps)
├── evaluation/          # Validation
│   ├── validate_sim.py  #   Unseen simulation seeds
│   ├── validate_real.py #   Real stress events
│   └── cross_market.py  #   Equity transferability check
├── viz/                 # Visualizations
│   ├── heatmap.py       #   Risk heatmap across dimensions
│   └── risk_curves.py   #   Risk-density curves
├── demo/                # Deployment demo
│   └── live_demo.py     #   Live data feed → detector → intervention suggestion
└── tests/
```

## Key Constraint

> The detector may only read the 28 PUBLIC_FIELDS from the step log.
> The 8 SIM_ONLY_FIELDS supply training labels but are NEVER detector inputs.
> This keeps the detector transferable to a real exchange.

See `perp_sim/engine/records.py` for the field definitions.
