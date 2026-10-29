# Results

Experiment outputs are stored here. Large files are gitignored.

## Structure

```
results/
├── phase1/              # Phase 1: Risk discovery
│   ├── metrics/         #   Risk metrics (vol amplification, drawdown, Gini)
│   ├── mechanism/       #   Mechanism metrics (correlation, depth decline, spread)
│   ├── mri/             #   Composite Market Risk Index + risk levels
│   └── thresholds/      #   Change-point detection results
├── phase2/              # Phase 2: Intervention testing
│   ├── effectiveness/   #   Risk reduction per dimension
│   ├── re_adapt/        #   Post-adaptation results
│   ├── combos/          #   Intervention combinations
│   ├── liquidity/       #   Liquidity cost and recovery
│   └── interactions/    #   Interaction indices I_ij
├── phase3/              # Phase 3: Detection & prediction
│   ├── model_metrics/   #   Trained model performance
│   ├── calibration/     #   Probability calibration
│   └── early_warnings/  #   EW indicator analysis
├── validation/          # Validation results
│   ├── sim_validation/  #   Unseen seeds
│   ├── real_events/     #   Real stress events
│   └── cross_market/    #   Equity transferability
└── README.md            # This file
```

## Convention

- Small summary files (CSV, JSON < 1MB): commit to git
- Large raw outputs (Parquet, PKL > 1MB): store in shared Google Drive, link here
- Every result file should have a corresponding config in `configs/`
