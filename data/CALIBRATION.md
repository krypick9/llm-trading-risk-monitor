# Calibration data and results

## Data sources

| Source | Type | Days | Used for |
|--------|------|------|----------|
| Binance Vision | aggTrades (tick-level) | Sep 15-17, Oct 9-10, Aug 25 (6 days) | sigma, jumps, kurtosis, order flow |
| Binance Vision | bookDepth (% band snapshots, 30s) | Same 6 days | spread, depth |
| Hyperliquid API | Funding history | Sep-Nov 2025 | funding rate calibration |
| Hyperliquid API | Live orderbook snapshot | Nov 2025 | depth reference (L2) |

### Why these 6 days?

The 6 days were selected to cover two distinct market regimes:

| Day | Regime | Rationale | BTC close |
|-----|--------|-----------|-----------|
| Sep 15 | Normal | Calm trading day, low volatility | ~115,300 |
| Sep 16 | Normal | Continuation of calm period | ~116,700 |
| Sep 17 | Normal | Slight fat-tail activity (kurtosis 17.8) | ~116,400 |
| Oct 9 | Stress | Significant decline day (-1.3%) | ~121,600 |
| Oct 10 | Extreme stress | BTC crash -7.3% in one day (121k→112k) | ~112,700 |
| Aug 25 | Stress | Independent stress sample from different month (-3%) | ~110,000 |

- 3 normal + 3 stress days correspond to the simulator's two regimes
- Oct 10 is a canonical crash day — needed to observe real liquidation cascade parameters
- Selecting stress days from different months avoids single-event bias

### Relationship to Phase 1 experiments

Calibration data and Phase 1 experiment data are **completely different**:

| | Calibration | Phase 1 experiments |
|---|---|---|
| **Data** | Real historical data (these 6 days) | Simulator-generated new data |
| **Purpose** | Match simulator parameters to reality | Run LLM agent experiments with calibrated sim |
| **Flow** | Real data → extract stats → grid-search params | Calibrated sim → add N LLM agents → run 600 steps → log |
| **Output** | Parameter values in `config.py` | CSV logs (price, trades, liquidations per step) |

The teammate's 22-day reference data (`data/reference/`) serves yet another purpose: Phase 3 detector validation against real market crash windows.

## Pipeline

1. `data/scripts/download_data.py` — downloads raw data from Binance Vision + Hyperliquid (free, no API key)
2. `data/scripts/compute_calibration_targets.py` — extracts statistical targets from raw data
3. `data/scripts/run_calibration.py` — grid-search matches simulator parameters to targets
4. `data/scripts/verify_stylized_facts.py` — validates fat tails, volatility clustering, order flow autocorrelation

```bash
# Full pipeline (from scratch)
python data/scripts/download_data.py
python data/scripts/compute_calibration_targets.py
python data/scripts/run_calibration.py          # ~5 min, 200 combos
python data/scripts/verify_stylized_facts.py    # ~30 sec
```

## Results

| File | Description |
|------|-------------|
| `data/processed/calibration_targets.json` | Real-data statistics (sigma, jumps, spread, depth, funding) |
| `data/processed/calibration_result.json` | Grid-search results: best-fit params per regime with fit scores |
| `data/processed/stylized_facts_report.json` | Stylized facts verification (fat tails, vol clustering, order flow ACF) |
| `perp_sim/engine/calibrated_config.py` | Generated SimConfig with calibrated values |
| `perp_sim/engine/calibrated_scenarios.py` | Generated scenario parameters with calibrated values |
| `docs/reports/calibration_report.md` | Full calibration report (for mid-term submission) |

## Applied to

Calibrated values have been applied to the simulator's default config:

- `perp_sim/engine/config.py` — all PLACEHOLDER values replaced with calibrated values
- `perp_sim/engine/scenarios.py` — both regime parameters replaced with real-data-matched values
- `perp_sim/bots.py` — `MIN_TRADE` lowered from 0.01 to 0.002 and `ramp_steps` from 20 to 5 for mini-market compatibility

## Key calibration changes

| Parameter | Old (placeholder) | New (calibrated) | Change |
|-----------|-------------------|-------------------|--------|
| sigma_hourly (normal) | 0.005 | 0.002875 | -42% |
| sigma_hourly (stress) | 0.012 | 0.003098 | -74% |
| shock_return (stress) | -0.06 | -0.1315 | +119% |
| maintenance_margin_rate | 2.5% | 1% | -60% |
| initial_collateral | $10,000 | $1,000 | mini-market |
| bg_depth_btc (thick) | 12.0 | 8.0 (normal) / 1.0 (stress) | recalibrated |
| bg_half_spread | 0.0001 | 0.0001 | unchanged |

## Stylized facts verification (all pass)

| Fact | Normal | Stress | Threshold |
|------|--------|--------|-----------|
| Excess kurtosis (fat tails) | 10.30 | 5.73 | > 0 |
| Volatility clustering (lag-1 ACF) | 0.61 | 0.60 | > 0.1 |
| Order flow autocorrelation | 0.28 | 0.47 | > 0 |
