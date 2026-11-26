# perp_sim — minimal BTC perpetual-futures simulator

The simulator for the FYP *"Risk Monitoring for LLM Trading Agents"*. It is a
deliberately small rewrite of the Phase-0 engine (archived at
`legacy/perp_sim_phase0/`). The goal of the cut is **fewer moving parts**: when a
risk number looks wrong, you should be able to tell which component caused it.

Phase-0's real problem was not its size -- ~1,800 lines is small -- but the
number of states it could be in: three parameter sets, two scenario builders, and
a `bg_flow_enabled` default that silently ran the experiment in a market the
calibration never saw. That is what this version removes.

Two cuts were reversed after review: **funding** and **historical replay** are
back (see "Restored after review"). The original claim that "no claim the project
makes is weakened" was not quite true without them -- funding is a
perpetual-futures risk channel, and the replay is the project's only empirical
stress event.

## The one idea that licenses the cuts

The project compares risk **relatively** — same market, same settings, only the
agent mix changes — and does **not** claim the simulator matches real BTC in
absolute terms. So the market only has to be *reasonable and fixed*, not *fitted*.
That means the entire absolute-calibration apparatus (method-of-simulated-moments
grid search, hand-set weights, tolerance bands, held-out "validation") can go. We
set parameters once to plausible values and validate **qualitatively** instead
(`sanity.py`).

## The causal spine (kept — do not cut)

    agent takes a leveraged position
      -> price moves
        -> margin breach
          -> forced liquidation hits the book
            -> book impact drags the mark
              -> next breach   ... = the cascade

Everything needed for that chain is kept:

| File | Role | Status |
|---|---|---|
| `engine/orderbook.py` | the limit order book + matching | **verbatim** from Phase-0 |
| `engine/accounts.py` | cash, position, margin maths | **verbatim** from Phase-0 |
| `engine/exchange.py` | matching + mark price + **liquidation engine** | trimmed |
| `engine/index_price.py` | exogenous reference price | GBM + jumps only |
| `engine/flow.py` | anonymous aggressive flow (demand) | trimmed |
| `agents.py` | one liquidatable archetype + LLM hook | new (replaces the bot zoo) |
| `population.py` | fixed slots + replace-not-add swap | trimmed |

The liquidation **mechanism** (multi-round forced closing, bad-debt sweep,
`mark = median(index, last, book-mid)` so a falling book feeds the cascade) is
unchanged — that is the phenomenon under study.

## What was cut, and why

| Cut | Why it's safe |
|---|---|
| **The simulator's dependence on the calibration pipeline** (MSM grid, scale calibration) | It only served absolute claims we don't make, and it was the source of the contradictory artifacts. The *scripts and data stay in the repo* as Phase-0 evidence for the report — see `legacy/README.md`. What is gone is the dependency, not the record. |
| **One of two liquidity sources** (MakerBots) | The exchange background LP is now the *only* resting liquidity. Phase-0 had LP + MakerBots + taker flow — that overlap caused the flow/depth scale conflict. See "Known open issues": this also removed the only liquidity that can *choose* to withdraw. |
| **Four of five bot archetypes** | Trend/contrarian/value/crowd + their invented thresholds collapse to one leveraged position-taker, so a risk move is attributable to the agent mix, not a four-type interaction. |
| **Circuit breaker** | That's a Phase-2 *intervention*, not part of the baseline model. It was also a verified no-op in Phase 0 (it rejected only `price is None` market orders, and no bot ever sent one; liquidation orders bypassed the check entirely). Re-add it as a toggle when you test interventions — and test that it actually blocks something. |
| **Scenario matrix** (2×2×3 = 12) | Collapsed to three: `normal`, `stress`, `replay-extreme`. |
| **The 5–20× trader capital** | Phase-0's `PopulationSpec` drew $5k–20k per trader while the market scale assumed $1,000, so 100 traders held $1.27M against a $50k book. Uniform $1,000 × 50 is the mini-market scale the depth and flow numbers were derived for. This single change is what fixed the microstructure (see below). |

### Restored after review

| Restored | Why the cut was wrong |
|---|---|
| **Funding** (`funding_enabled`, default on) | The project is about *perpetual* futures, and funding is what drains a crowded side's collateral between liquidations. It looked negligible in Phase 0 because `funding_cap_per_hour` was `1.25e-5`, about **1/20** of a real venue's 0.75%/8h. The cap was the problem, not the mechanism; it is now `0.0009375` = 0.75%/8h. |
| **Historical replay** (`replay-extreme`) | It was the only empirical anchor and the only stress event not of our own invention. Unlike Phase 0 it no longer silently falls back to synthetic GBM when the stored path is missing — a missing or too-short path raises. |
| **The `n_llm > 0` fairness tests** | The minimal suite only built `n_llm=0` twice, so it tested determinism, not the replace-not-add invariant the causal claim rests on. |
| **Engine coverage** (book lifecycle, leverage clip, reduce-only, bad debt, flow) | 7 tests was too few for the parts a wrong risk number would route through. Now 63. |

## What was simplified but kept

- **Exogenous driver:** GBM + jumps + one optional scheduled shock (stress), or a
  real replayed path (`replay-extreme`).
- **Volatility clustering:** restored as a hand-set AR(1) multiplier on the index
  vol (`vol_cluster_rho`, `vol_cluster_vov`) — NOT fitted. It was originally cut
  because Phase-0 couldn't *calibrate* it to 0.27; since we no longer calibrate,
  it only needs to be present and reasonable. It is the temporal structure that
  packs volatility into episodes, which is where cascades build.
- **Mark vs last price:** kept distinct on purpose — liquidating on `last` makes
  cascades trivially self-reinforcing; `mark = median(index, last, mid)` keeps
  them sane. This realism is load-bearing.
- **Replace-not-add swap:** the good part of Phase-0's population design is intact
  — fixed slots, seed-stable swap order, constant total capital, so n_llm is the
  only thing that changes between a control and a treatment run.
- **Queue-reactive-*inspired* flow:** the taker flow keeps two cheap properties of
  the Huang–Lehalle–Rosenbaum model (rate rises with volatility; flow leans to the
  thinner book side) without estimating queue intensities from L2 data.

## Validation — qualitative only, on purpose (`sanity.py`)

Two cheap, honest checks replace the calibration grid:

**A. Stylized facts** (does it behave like a market?) — sign/direction checks, not
"match real BTC to X%": fat tails (kurtosis > 3), volatility clustering
(squared-return ACF1 > 0), positive price impact (buys push up).

**B. Power check** (the important one): with scripted traders only, sweep leverage
and confirm liquidations respond. *If the instrument can't register a known
perturbation, it can't register an agent effect.* **Run this before any Phase-1
agent experiment.**

Current output (5 seeds / 3 seeds):

    A. fat tails      kurtosis   = 8.83   PASS   (real BTC ~10)
       vol clustering sq-ret ACF1= 0.076  PASS   (real ~0.27 — moderate, see caveat)
       price impact   slope      > 0      PASS
    B. leverage  2x ->   0.0 liquidations
       leverage  5x ->   0.0
       leverage 10x ->   0.0
       leverage 20x -> 731.7            monotone + detectable PASS

### Where the market now sits vs real BTC

Not targets — these are not fitted — but worth recording, because the capital-scale
fix moved them a long way. Normal scenario, 600 steps, 5 seeds:

| | Phase-0 engine | here | real BTC (Phase-0 measurement) |
|---|---|---|---|
| steps with a one-sided book | 28–46% | **0.2–0.9%** | ~0% |
| near depth per side | 0.003–0.21 BTC | **0.42 BTC** | 0.49 BTC (scaled) |
| relative spread | 3.4e-6 – 6.8e-5 | **~1e-6** | 8.8e-7 (≈1 tick) |
| hourly volatility | 0.0033–0.0060 | **0.0028** | 0.0026 |
| volume | 0.57–0.81 BTC/min | **0.16 BTC/min** | 0.19 BTC/min (scaled) |
| flat minutes | 6.5% | **0.2%** | 3.8% |

Almost none of this came from deleted code. It came from putting trader capital on
the same scale as the book ($1,000 × 50 instead of $5k–20k × 100).

### Known open issues — read before Phase 1
- **Volatility clustering is moderate, not matched** (ACF ~0.09–0.12 vs real ~0.27).
  It is deliberately *not* pushed to 0.27: with a single AR(1) multiplier, raising
  it to 0.27 drags kurtosis to 14–22 (real ~10). A one-parameter vol process can't
  hit both targets at once — matching both would need a richer (GARCH/regime) vol
  model, which is more than a relative-comparison design needs. Present + plausible
  is the right call here; chasing 0.27 would be re-introducing the fitting we cut.
- **The power check passes but as a cliff** (0 liquidations up to 10×, then ~730 at
  20×). The instrument is insensitive to risk until extreme leverage.

- **The cascade does not amplify — this is the blocker.** At 20×/`long_bias=0.7`,
  stress, 5 seeds: 722 liquidations, 35 accounts, $549 bad debt, and yet

        liquidations ON : max drawdown 7.77%, dip vs index -0.11%
        liquidations OFF: max drawdown 7.78%, dip vs index -0.11%
        → endogenous amplification: 0.00 pp

  700+ forced liquidations move the price not at all, so the causal spine above
  never closes its loop. It is not the liquidation engine — that fires correctly.
  It is the shape of the book: the LP ladder spans only
  `bg_half_spread + (bg_levels-1) × bg_level_gap` = **0.08%**, so exhausting the
  entire book can push the price at most 0.08% below centre (the observed dip is
  -0.11%). Beyond that the book is empty and the last price stops rather than
  gapping down. Widening the span switches the mechanism back on
  (`bg_anchor_strength=0.2`, 20×, `long_bias=0.7`, 5 seeds):

        levels  gap     span%   liqs  accts  badDebt   dipON%  dipOFF%  ampDip
             5  0.0002   0.08    446   32.8     $321    -0.41    -0.41   +0.01
             5  0.0020   0.80    619   34.4   $5,081    -3.06    -2.39   -0.67
            20  0.0020   3.80   1894   50.0 $176,187   -15.37   -11.61   -3.75
            40  0.0050  19.50   1598   50.0 $641,429   -53.91   -46.48   -7.43

  Fixing it properly means three things, and they are the next commit, not this
  one: (1) a span of roughly 1–4% rather than 0.08%; (2) a decaying per-level
  size, because `size = bg_depth_btc / bg_levels` currently makes "more levels"
  mean "uniformly thinner", so span and depth cannot be set independently;
  (3) finite LP inventory — at `collateral=1e12`, non-liquidatable, and
  re-centring 80% of every dislocation each minute, the LP is an infinitely deep
  mean-reverting force that nothing can exhaust.

- **Funding is restored but still small**, because the premium it is computed from
  is near zero — the LP pins the exchange price to the index. That is the *same*
  root cause as the missing cascade, not a separate problem; funding will start to
  matter once the basis can actually move. The cap is now right, and
  `test_the_cap_is_the_real_venue_order_of_magnitude` guards it.

- **The flow's per-minute volume is constant by construction.** `flow.py` draws
  heavy-tailed sizes and then renormalises them so the total equals
  `bg_flow_btc_per_min`, so measured over 600 minutes the volume is 0.170 BTC/min
  at *both* 2.2 and 80 orders/min. Three consequences: volatility excitement adds
  no volume (just more, smaller orders); there is no volume–volatility
  correlation; and at 80 orders/min about 73% of draws fall below lot size and are
  silently dropped. Phase-0's price-impact R² of 0.04 (vs 0.36 real) is most
  likely this. Fix: scale `bg_flow_size_median_btc` and delete the renormalisation.

- **Liquidation mechanics are simplified in a direction that inflates severity**:
  full-size market IOC at any price, with no bankruptcy-price cap, no partial
  liquidation, no insurance fund and no ADL. Combined with
  `mark = median(index, last, mid)` — real venues use index-anchored marks
  precisely to *prevent* this feedback — cascade magnitudes here are an upper
  bound, not an estimate. State this as an assumption and publish an
  index-anchored-mark sensitivity run beside any cascade result.

- **Small known defects**, all one-liners, none fixed here: `_open_interest()`
  does not filter background accounts (~18% of reported OI is LP and flow
  inventory, and OI is a Phase-3 detector feature); `_sweep_bad_debt` only fires
  at `position == 0`, so an account with negative equity and an unfilled residual
  records no bad debt until it fully closes; `spread`/`spread_rel` are written as
  `0.0` when the book is one-sided, which biases any average that does not filter
  them out.

## Run it

    python run.py --scenario normal  --steps 600 --seed 0 --out run.csv
    python run.py --scenario stress  --steps 600 --seed 0
    python run.py --scenario replay-extreme --steps 299 --seed 0
    python sanity.py            # stylized facts + power check
    python -m pytest tests/ -q  # 63 tests

The replay scenario starts 300 minutes into the stored 2025-10-10 path (the worst
minute there is 558, near the end of the window), so its length must satisfy
`steps <= 599 - replay_offset`. A replay run is identical for every seed — the
path is data — so vary seeds to study trader randomness, not the price path.

## Plugging in LLM agents (Phase 1)

`population.build_population(seed, spec, n_llm=k, llm_factory=...)` swaps the first
`k` slots for agents. The factory returns an object with `account_id` and
`act(view) -> [OrderRequest/CancelAll]` — same contract as `agents.PositionTaker`.
See the skeleton at the bottom of `agents.py`. `n_llm=0` is the all-scripted
control; nothing else about the market changes when you raise it.

## Decisions I made (override if you disagree)
1. **Background LP = the only resting liquidity** (dropped MakerBots). If you'd
   rather the scripted crowd provide liquidity, reinstate a maker archetype in
   `agents.py` and `population.py`.
2. **One archetype = leveraged position-taker** with a `long_bias` knob. It's
   symmetric at `long_bias=0.5`; raise it for crowded longs.
3. **Funding removed.** Re-add as a step in `exchange.step` if you decide perp
   funding matters for your risk metrics.
