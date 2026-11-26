# legacy/ — Phase-0 material, frozen

Nothing in here is part of the current pipeline. It is kept on purpose, for two
reasons: the FYP report needs to show that real BTC data were measured and that
absolute calibration was attempted, and the argument for *not* calibrating the
current simulator is much stronger when the attempt and its failure modes are on
the record.

## What is here

`perp_sim_phase0/` — the Phase-0 engine and its 80 tests, exactly as they were,
except that four import paths were updated for the move. It still runs:

    cd legacy/perp_sim_phase0 && python -m pytest tests/ -q     # 80 passed

Use it to reproduce a Phase-0 number, or to copy a mechanism back out (that is
how funding and the replay path came back). Do not develop in it.

The calibration and validation scripts stay where they always were, in
`data/scripts/`, and now import this archived engine rather than the live one.
`data/reference/` and `data/processed/` are unchanged.

## Why the Phase-0 engine was replaced

Not because it was too large — ~1,800 lines is small, and the matching engine,
account/margin maths and liquidation loop were correct and carefully done
(self-trade protection, arrival shuffling, per-account RNG streams, leverage
clipping that accounts for resting orders). The problem was the number of states
the system could be in:

1. **The calibration did not apply to the experiment.** `run_calibration.run_one`
   and `run_baseline.run` built different markets. Not one calibrated setting was
   in force when the experiment ran:

   | dial | calibration | experiment |
   |---|---|---|
   | `bg_flow_enabled` | True | **False** (the `SimConfig` default) |
   | `index_vol_clustering` | True | **False** |
   | `bg_backstop_only` | True | **False** |
   | `bg_flow_orders_per_min` | 2.2 | 80 |
   | `n_steps` | 600 | 120 |
   | traders | 50 × $1,000 | 100 × $5,000–20,000 |
   | mix | trend/contrarian/value/maker | 45% noise + 4 others |

2. **The dependent variables were identically zero in the control.** Across all
   12 scenarios × 8 seeds at the default `n_steps=120`: 0 liquidations, 0 bad
   debt. The replay paths hold 600 minutes and the 10-Oct crash is at minute 558,
   so the stress scenarios stopped before the stress. At 600 steps the same
   configuration gave 2.3–3.8% of accounts liquidated.

3. **Three live, mutually inconsistent parameter sets.** `scenarios.py`
   (depth 0.25/0.5, half-spread 8.77e-7), `calibrated_scenarios.py`
   (depth 0.5/12.0, half-spread 1e-4, and still the −13.15% single-minute shock
   the replay was meant to retire), and `calibration_result.json`
   (`"depth": 4.0`). Different validation scripts used different ones.

4. **The calibration was not identified.** Only three moments were scored
   (`sigma_hourly` w=3, `jump_rate_per_hour` w=1, `spread_rel` w=2). Depth was
   searched over six values but never entered the objective.
   `jump_rate_per_hour` was copied from the target into the result, so its error
   was constant across the grid and discriminated nothing — two effective moments
   for three free parameters. `sigma_hourly` was scored on the *index* series
   while the real target was measured on traded prices, and the sigma grid topped
   out at 0.8× the target, so the best attainable score on the
   highest-weighted moment was a 20% error by construction. The spread moment was
   computed on `[r.spread_rel for r in recs if r.spread_rel > 0]`, which drops
   exactly the one-sided-book steps — 28–46% of all steps, and the worst ones.
   The checked-in result is `"mode": "quick"`, `n_seeds: 1`: one sigma, one
   spread, four depths, one seed.

5. **Two features did not do what they claimed.** The circuit breaker was a
   no-op: it rejected only orders with `price is None`, and no bot ever sent a
   market order, while liquidation orders bypassed the check entirely — with it
   on and off, 6 seeds gave identical liquidation counts and drawdowns. All six
   `*-replay` scenarios hardcoded `replay_regime="extreme"`, so the three
   labelled "normal" replayed the crash day.

## Artifacts in data/processed/ are stale

They were produced by this engine at settings it no longer ships, so they do not
describe either simulator. `validation_heldout.json` still reports
`exchange_zero_return_pct: 60.1%` — the flat-minute figure from before the
background flow existed. `validation_microstructure.json` reports sim depth of
3.74/2.15 BTC, which matches neither `scenarios.py` (0.25/0.5) nor the recorded
fit (4.0). Read them as a record of what was run in Phase 0, not as current
validation.

Two numbers in there are worth carrying forward as goals for the live engine,
because they are measured against real data and the live engine has not yet been
checked on either: real price-impact R² is 0.36 against the sim's 0.04, and real
normalised impact is ~9× the sim's.
