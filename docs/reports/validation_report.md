# Validation report (held-out days, microstructure, crash)

> **Rewritten 2026-11-26.** This replaces the earlier version, which reported "2 of 5" and carried a
> presenter script built on it. That framing is invalid (section 2). Do not quote the old numbers.
>
> **The committed output files are stale.** `validation_heldout.json` and `validation_microstructure.json` were
> produced before the background flow was switched on in the validation scripts, and the microstructure file uses
> only 2 seeds. Treat every number in section 4 as history, not as a result for the current simulator, until the
> 10-seed re-run in section 8 is done.

## 1. Status in one paragraph

The simulator is **not validated**. We have a controlled environment for comparing trader mixes, and a set of
diagnostics that show where it differs from real BTC data. All validation scripts now build the simulator through
one shared setup (`data/scripts/sim_setup.py`), and a test fails if any of them drifts from calibration. The
held-out days were inspected before, so even a good score would be a diagnostic, not independent proof.

## 2. What was wrong with the first version (leakage correction)

The original held-out check had four problems. They were found by Emily and are confirmed here.

- **Dials were set using the held-out days.** Volatility (x1.57), depth (x0.61) and spread (x1.04) were scaled by the
  ratio of held-out to training statistics, and the same days were then scored. That is tuning on the test, so a pass
  would have meant little. Our earlier write-up described this as a fair step. It was not.
- **An event was injected** into the test path, so the run was not a no-event comparison.
- **The flat-minutes rule (1% to 6%) was invalid.** It was copied from the tuning days (3.5%) while the real held-out
  value is 0.56%, so even a perfect copy would fail.
- **Spread counted as a pass although we set that dial from real data.** It is not independent evidence.

Corrected method (`validate_heldout.py`): dials are frozen from the training days, no scaling, a synthetic no-event
path, three scored checks (local volatility ratio 0.7 to 1.3, volatility-clustering ACF1 within +/-0.15, kurtosis
ratio 1/3 to 3). Flat minutes and spread are descriptive only. The verdict is labelled "diagnostic only".

## 3. Setup

| Set | Days | Used for |
|---|---|---|
| Training, normal | 24 Sep to 7 Oct (14 days) | choose the dials |
| Training, stress | 8 to 9 Oct | stress-regime dials |
| Crash replay | 10 Oct | replay path only, never tuned |
| Held-out (inspected before) | 11, 12, 13, 20 Oct | frozen-dial diagnostic |
| Reserved | 19 Oct | confirmatory only, see `reserved_confirmatory_dates.md` |

Simulator (from `sim_setup.py`, mirrors `run_calibration.run_one`): 50 scripted traders from the reference
population (trend, contrarian, value, maker; no noise bots), 600 one-minute steps, background taker flow on at
**2.2 orders per minute**, backstop liquidity provider on, AR(1) volatility clustering on, scenario depths 0.5 (thick)
and 0.25 (thin) BTC per side.

The 2.2 per minute is an **assumption** (0.004 x 547 real trades per minute). The engine default of 80 was chosen to
avoid empty minutes, and flat minutes is also a validation statistic, so 80 is circular. Run
`sensitivity_flow_count.py` and report both.

## 4. Last committed outputs (history, stale)

Frozen-dial held-out diagnostic, committed before the flow was enabled in the scripts (frozen dials: sigma 0.0026,
depth 4.0 BTC, spread 8.8e-7, from a one-seed quick fit):

| Check | Result | Note |
|---|---|---|
| Local volatility | 0.74x real | within band, pass |
| Volatility clustering ACF1 | difference -0.163 | outside +/-0.15, fail |
| Kurtosis | 2.51x real | within band, pass |
| Flat minutes | 60.1% vs 0.56% real | descriptive only; this is the pre-flow problem |
| Spread | 2.25x real | descriptive only (dial set from data) |

Microstructure diagnostic, 2 seeds, committed before the flow was enabled, depths of the older scenarios:

| Metric | Real held-out days | Sim thick | Sim thin |
|---|---|---|---|
| Price impact (return per typical minute of volume) | 5.1e-4 | 0.11x real | 0.08x real |
| Depth dip after a >2-sigma move (lowest / prior median) | 0.90 | 0.70 | 0.52 |
| Minutes to recover 90% of depth | 1 | 1.0 | 1.25 |
| Share of minutes with spread above 1 bp | 0.6% | 0.08% | 3.3% |

Crash replay (real 10 Oct): real depth fell to 4% of its pre-crash level; the simulated book went to 0. The simulated
worst one-minute return was 0.77x (thick) and 0.72x (thin) of the real -5.13%, so it is smaller, not larger, than real.
Simulated liquidations were 9 and 10 per run; the reference data has no liquidation feed, so this cannot be checked.

These tolerance bands were set after a first look. They are diagnostics, not pass/fail tests, and must not be
changed after seeing new results.

## 5. Known open issues

- **Flow count** (2.2 vs 80 per minute) is an assumption and is circular if chosen to fix flat minutes.
- **Spread** was reported in PR #1 as about 4.6x the real median (0.04 bp vs 0.0088 bp); the tail of wide-spread
  minutes was not measured by the median.
- **Volatility clustering** was 0.15 in PR #1 against 0.275 real, limited by the clamp on log-volatility.
- **Depth** of 0.25 to 0.5 BTC is below the 1.5 BTC level at which spreads broke in earlier runs.
- **No real liquidation data**, so cascade size and liquidation counts are mechanism checks only.
- **Held-out days were inspected before**; only the reserved day can give a confirmatory result.
- **Citation in `engine/flow.py`** credits "Stoikov 2014" for the queue-reactive model. The paper is Huang, Lehalle and
  Rosenbaum, JASA 110(509), 2015. This is outside the validation files and is for Carol to fix.
- The earlier banner pointed to `docs/reports/environment_validation_update_for_team_en.md`, which is not in the
  repository. The link has been removed.

## 6. What we claim and do not claim

| We claim | We do not claim |
|---|---|
| The simulator is a controlled environment where only the traders' decisions change | The simulator is validated |
| Relative differences across agent mixes under identical settings | Absolute loss, bad-debt or liquidation levels |
| The crash path is the real 10 Oct index path | Cascade size equals the real event |
| Where it differs from real data (section 4 and 5) | Price impact, depth or spread tails match real data |
| The held-out check is a diagnostic | The held-out days are independent validation |

## 7. Rules for every future run

1. Do not change bands, dials or scenario depths after seeing validation results.
2. Report the flow count with every result, and report both 2.2 and 80.
3. Use the reserved day only once, after the simulator version is frozen.
4. Keep calibration and validation statistics separate; where a statistic is both tuned and validated, say so.

## 8. Reproduce

```
python -m unittest discover -s perp_sim/tests -t .
cd data/scripts
py validate_heldout.py
py validate_microstructure_vs_real.py --seeds 10
py sensitivity_flow_count.py --seeds 10
py validate_cascade_impact.py
py verify_stylized_facts.py
```

Run the unit tests first. After the 10-seed re-run, regenerate `validation_heldout.json`,
`validation_microstructure.json` and `sensitivity_flow_count.json`, then replace section 4 with the new numbers.
The presentation slides on validation must be updated from those numbers; the earlier slides used the invalid method.
