# Validation report (held-out days, microstructure, crash)

Status: draft for the group presentation. Numbers come from the files in `data/processed/`
(`validation_heldout.json`, `cascade_validation_report.json`, `validation_microstructure.json`).
Commit reviewed: `691a237`.

## 1. One-paragraph summary

We tuned the simulator on calm days, then tested it on four calm days it never saw.
It **did not pass** (2 of 5 rules). On top of that, the two rules it passed are weaker evidence than they look
(section 3). A new side-by-side check against real data (section 4) shows the simulator's price impact is much
weaker than real, its order book empties far more deeply after a shock, and its spread has a heavy tail the real
market does not have. We therefore claim only **relative** comparisons between agent mixes in the same simulator,
never absolute levels.

## 2. Setup

| Set | Days | Used for |
|---|---|---|
| Tuning | 24 Sep - 9 Oct (16 calm days) | choose the dials |
| Held-out | 11, 12, 13, 20 Oct (4 days) | one-shot test, never tuned |
| Crash | 10 Oct | stress replay path only, never tuned |

Held-out days are more volatile and thinner than tuning days (0.43%/h vs 0.27%/h, 73 vs 120 BTC depth), so we
scaled three dials by the real ratios (volatility x1.57, depth x0.61, spread x1.04). Depth is held at a 1.5 BTC floor
because the mini-market breaks below that. The pass rules were written before the run, 10 fresh seeds were used,
and a control run with unscaled dials was added.

## 3. Held-out result: 2 of 5, not validated

| Rule | Sim | Real | Result | Is it independent evidence? |
|---|---|---|---|---|
| Volatility within 0.7-1.3x | 0.00212 | 0.00427 | FAIL (0.50x) | Yes |
| Vol clustering within +/-0.15 | 0.263 | 0.166 | PASS | Yes (not in the calibration score) |
| Tails within 1/3x-3x | 32.8 | 7.6 | FAIL (4.3x) | Yes |
| Flat minutes 1%-6% | 0.50% | 0.56% | FAIL (rule wrong) | Rule was copied from tuning days (3.5%) |
| Spread within 2x | 1.02e-6 | 0.9e-6 | PASS (1.13x) | **No** - spread is a dial we set to the real value |

Four points to say out loud:

1. **Spread is a pass by construction.** We set the spread dial from real data and scaled it by the real ratio.
   Counting only independent checks, the simulator passes 1 of 4.
2. **The flat-minutes rule was mis-written.** The simulator (0.50%) actually matches the real held-out days (0.56%).
   We keep the original verdict and disclose it. Even if the rule were corrected, the score would be 3 of 5, still
   below the 4 needed.
3. **The control run is almost identical to the main run** (volatility 0.00208 vs 0.00212). Raising the volatility
   dial by 57% barely moved simulated volatility, so realised volatility is driven by trader flow and book depth,
   not the index volatility dial.
4. **The simulator cannot represent the thinner book.** Depth was floored at 1.5 BTC.

## 4. Beyond return statistics: price impact, depth, recovery

Return statistics do not test what the research question depends on. `validate_microstructure_vs_real.py`
applies the same definitions to the real minute data and to the simulator (10 fresh seeds).

| Metric | Real held-out days | Sim thick (4.0 BTC) | Sim thin (1.5 BTC) |
|---|---|---|---|
| Price impact: return per one typical minute of volume | 5.1e-4 | 0.4e-4 (0.07x) | 0.6e-4 (0.12x) |
| Impact R2 (how much of 1-min return flow explains) | 0.36 | 0.06 | 0.04 |
| Depth after a >2-sigma move (lowest / prior median) | 0.90 | 0.33 | 0.44 |
| Minutes for depth to return to 90% | 1 | 3.1 | 2.8 |
| Share of minutes with spread above 1 bp | 0.6% | 13.8% | 26.4% |

Reading it:

- **Price impact is far too weak** and flow explains far less of the return than in real data. Returns in the
  simulator are driven mostly by the index process, not by order flow.
- **The book thins far more than real.** After an ordinary shock the real book keeps about 90% of its depth;
  the simulator keeps a third or less.
- **The spread median matches, but the tail does not.** 14-26% of simulated minutes have a spread above 1 bp,
  against under 1% in real data. The median-based spread check cannot see this.
- **Recovery looks fast in both** (1 vs about 3 minutes), but the simulator starts from a much deeper dip.

These tolerance bands were set after a first run, so treat this as a diagnostic, not a pass/fail test.
Impact depends on how the volume is normalised; the definition is in the script header.

## 5. Crash and cascade

| | Real 10 Oct (12:00 UTC, 600 min) | Sim stress-thick | Sim stress-thin |
|---|---|---|---|
| Lowest depth / pre-crash depth | 0.04 | 0.00 | 0.00 |
| Widest spread (bp) | 8.5 | 73 | 151 |
| Worst 1-minute return | -5.13% | -1.80% | -2.40% |
| Max drawdown | -15.47% | -12.2% | -13.4% |
| Liquidations per run | not in the data | 11.0 | 16.9 (84% in chains, runs up to ~3.4) |

- The real book also collapsed (to 4% of pre-crash depth), but the simulator's book goes fully empty and its spread
  is 9-18 times wider.
- **Check the "2.4x" figure before presenting it.** In the committed code the simulator's worst 1-minute return
  is about -2.4%, which is roughly **half** of the real -5.13% minute, not 2.4 times larger. The index follows the
  real path exactly (-5.13%, -15.47%); the traded price is smoother. Cascade size may therefore be **understated**
  on that dimension while the empty book pushes the other way, so we cannot call it an upper bound.
- The reference data contain no liquidation feed, so liquidation counts and cascade shape cannot be validated
  against real data. The bad debt figure ($135 +/- $229) is noisy, and the replay path is identical across seeds,
  so this is effectively one scenario.
- `validate_cascade_impact.py` marks a run as passing if there are zero liquidations, and its recovery check only
  asks for "under 60 steps". Do not quote those passes as validation.

## 6. Why calibration and validation use different statistics (TA Q6)

- **Calibration** sets dials with statistics the dials control directly: volatility, jump rate, spread, depth,
  worst-minute drop.
- **Validation** uses statistics we did not tune: tails, volatility clustering, flat minutes and, new here,
  impact and depth behaviour. If a statistic were used for both, a good match would mean nothing.
- Exceptions to say openly: volatility is both tuned and validated (validated on different days); spread is both and
  is set directly, so its pass is not independent.

## 7. What we claim and do not claim

| We claim | We do not claim |
|---|---|
| The simulator is a controlled environment where only the traders' decisions change | The simulator is validated |
| Relative differences across agent mixes under identical settings | Absolute loss, bad-debt or liquidation levels |
| Volatility clustering matches on unseen days | Fat tails, price impact or liquidity depth match real |
| The crash path is the real 10 Oct index path | Cascade size equals the real event |

## 8. Next step: sensitivity check

Repeat the key comparison at 2-3 settings of the weak spots (depth 1.5 and 4.0 BTC; normal and crash paths;
and, if time allows, the liquidity-provider assumptions: withdrawal fraction and anchoring). If the ranking of
agent mixes is the same, the conclusion does not depend on the parts that failed validation. If it changes, report
the conclusion as conditional.

## 9. Reproduce

```
cd data/scripts
py validate_heldout.py
py validate_microstructure_vs_real.py --seeds 10
cd ../../perp_sim
python -m unittest discover -s tests -t .      # 66 tests
```

## 10. Presenter script (about 6 minutes)

**Opening (30 s).** "Calibration shows we can fit days we tuned on. That proves little. So I'll show what happened on
days the simulator had never seen, and I'll tell you which checks failed."

**The test (60 s).** "We tuned on 16 calm days, 24 Sep to 9 Oct. We held out four later days. The crash day, 10 Oct,
is never tuned; it is only the stress path. We wrote five pass rules before running and used ten fresh seeds."

**The result (90 s).** "It passed 2 of 5, so it is not validated. Volatility clustering passed. Spread passed, but
we set that dial from real data, so it is not independent evidence. Volatility came out about half of real and
the tails about four times too fat. One rule, flat minutes, was wrong: the simulator matches reality but our rule
was written from the tuning days. We kept the original verdict."

**What we learned (60 s).** "The control run with unscaled dials gave the same volatility. So volatility in our
simulator is driven by trader flow and book depth, not by the volatility dial."

**Does it matter for the research question (90 s).** "Our question is about impact, liquidity and cascades, so we
compared those with real data. Price impact is about a tenth of real. The book thins about three times more after a
shock. The spread median matches but about one minute in five is far too wide. In the crash replay we get about 11 to 17
liquidations per run, mostly in chains, but we have no real liquidation data to check that against."

**Why we still proceed (60 s).** "We compare agent mixes in the same simulator, so relative differences are what we
measure. We will not quote absolute losses. Next we repeat the key experiment at a few settings of the weak spots.
If the conclusion holds, it does not depend on them."

### Likely questions

- *Why not retune after failing?* It would turn the held-out days into tuning days and destroy the test.
- *Is a failed validation a problem?* It limits what we claim; the claims above are set accordingly.
- *Why is spread not a real pass?* It is a dial we set from real data.
- *Why are tails too fat?* We do not know yet. Thin depth and the trader mix are candidates, and we have not tested them.
