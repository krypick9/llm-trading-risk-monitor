# perp_sim — minimal BTC perpetual-futures simulator

The simulator for the FYP *"Risk Monitoring for LLM Trading Agents"*. It is a
deliberately small rewrite of the Phase-0 engine (archived, and still runnable,
at `legacy/perp_sim_phase0/`). The goal of the cut is **fewer moving parts**: when a
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
and confirm liquidations respond monotonically *and across a usable range*. It
counts **distinct accounts liquidated**, not raw liquidation events — one trader
that rebuilds and is liquidated again inflates the raw count without any extra
risk having happened — and it requires at least 4 of 7 leverage levels to
respond, because a metric that only fires at 20× cannot show an agent effect at a
leverage anyone would run. *If the instrument can't register a known
perturbation, it can't register an agent effect.* **Run this before any Phase-1
agent experiment.**

Current output (5 seeds / 3 seeds):

    A. fat tails      kurtosis   = 12.96  PASS   (real BTC ~10.6)
       vol clustering sq-ret ACF1= 0.338  PASS   (real ~0.275)
       price impact   slope      > 0      PASS
       volume varies  coeff of var= 1.12  PASS   (real ~1.0)
       volume ~ |ret| correlation = 0.48  PASS   (>0)
       impact R^2     (info)      = 0.202         (real 0.36)
    B.  median lev  accts liq  liq steps  max/step   bad debt
                 2        0.0        0.0       0.0          0
                 3        0.7        0.7       0.3          0
                 5        2.0        1.0       2.0        115
                 8        9.0        4.3       6.7        889
                10       18.0        7.3      15.7       2252
                15       27.0        9.3      24.7       7444
                20       31.3       10.3      30.7      11752
        monotone: True ; levels with liquidations: 6/7 -> PASS

### Where the market now sits vs real BTC

Not targets — these are not fitted — but worth recording, because the capital-scale
fix moved them a long way. Normal scenario, 600 steps, 5 seeds:

| | Phase-0 engine | here | real BTC (measured in Phase 0) |
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
- **Volatility clustering and fat tails now come mostly from order flow, not from
  the index process**, and both slightly overshoot: ACF1 0.338 against a real
  0.275, kurtosis 13.0 against 10.6. That is a better place to be than the
  earlier 0.109 / 9.8, where the clustering came from a hand-set AR(1) and the
  flow contributed nothing. The earlier note here said a one-parameter vol
  process could not hit clustering and kurtosis together — true, but it turned
  out not to be the binding constraint; the flow was.
- **The risk metric now responds across a usable range**, after the leverage fix
  below: distinct accounts liquidated goes 0 → 0.7 → 2 → 9 → 18 → 27 → 31 as
  median leverage goes 2× → 20×. It was 0 at everything below 15×.

- **The cascade amplifies, but only as deep as the book reaches.** Fixed in
  "the ladder, and why its shape is load-bearing" below. Forced selling now
  moves the price (−1.3 pp of extra dip at 20×, negative on every seed), but the
  dip saturates at about −3% because that is where the quoted ladder runs out.
  Cascades deeper than that are still not representable; widening the reach is a
  one-dial change with a documented trade-off (see the table below).

- **Funding is restored but still small**, because the premium it is computed from
  is near zero — the LP anchors the exchange price to the index at
  `bg_anchor_strength=0.8`, i.e. it erases 80% of any dislocation each minute.
  The cap is now right (`test_the_cap_is_the_real_venue_order_of_magnitude`
  guards it); the anchor is the remaining pin, and it is worth a sensitivity run
  before Phase 1.

- **Quoted spread degrades when the flow is aggressive**, and this is the
  once-per-minute LP replenishment limit becoming visible rather than a flow
  problem. `spread_rel` is recorded at the end of a step, after a minute of
  trading against a ladder that is only re-quoted at the START of the next step,
  so it measures "the book after a minute with no replenishment". Real books
  refill in milliseconds. Median spread is 1.2e-4 against a real 8.8e-7. The fix
  is intra-step replenishment (re-quote every k orders, or k sub-steps per
  minute), and it is the next thing I would do.

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
   symmetric at `long_bias=0.5`; raise it for crowded longs. It **holds** its
   position by default rather than re-targeting constant leverage — that choice
   is what makes it liquidatable at all (see "Why the risk metric used to be
   all-or-nothing").
3. **Funding removed.** Re-add as a step in `exchange.step` if you decide perp
   funding matters for your risk metrics.


## The ladder, and why its shape is load-bearing

The LP's quoted ladder is the only resting liquidity, so its **price reach**, not
just its size, decides what the market can represent. The original shape — 5
levels, flat `0.0002` spacing — spanned 0.08%, which meant exhausting the entire
book could move the price at most 0.08%. Forced selling therefore produced
**0.00 pp** of endogenous amplification at every setting tried: the liquidation
engine fired correctly and nothing happened.

Three changes, which only work together:

1. **Geometric spacing** (`bg_level_gap_growth`) so the ladder reaches ~2.5%
   with 20 levels rather than 0.08% with 5.
2. **Geometric size decay** (`bg_size_decay`) with sizes normalised so the levels
   inside the ±0.10% band sum to `bg_depth_btc`. Reach and near depth are now
   independent; Phase 0 divided one total by the level count, so "more levels"
   silently meant "uniformly thinner".
3. **A finite LP inventory** (`bg_inventory_limit_btc`). Quoting tapers with the
   inventory the LP has taken on and stops on the side that would add to a full
   book, so forced selling can actually run out of bids.

### What each part contributes

Stress, 20×, `long_bias=0.7`, 5 seeds, 600 steps:

| | liquidations | bad debt | dip ON | dip OFF | amplification |
|---|---|---|---|---|---|
| old shape, no limit | 728 | $610 | −0.11% | −0.11% | **0.00 pp** |
| old shape, limit 15 | 877 | $661 | −0.11% | −0.12% | **0.00 pp** |
| new shape, no limit | 371 | $5,321 | −3.07% | −2.24% | −0.82 pp |
| **new shape, limit 15** | 446 | $5,496 | −3.07% | −1.80% | **−1.26 pp** |

The reach is what makes amplification possible at all — the inventory limit alone
does nothing (row 2). The limit then roughly doubles it.

### The microstructure did not pay for it

Normal scenario, 5×, `long_bias=0.5`, 5 seeds — the graded levels near the touch
replaced a 200× cliff between level 0 and level 1, so the spread actually got
*much* better:

| | one-sided book | near depth | spread | hourly vol |
|---|---|---|---|---|
| old shape | 0.2% | 0.416 | 4.1e-5 (46× real) | 0.00292 |
| **new shape** | **0.0%** | **0.404** | **1.0e-6 (1.15× real)** | **0.00279** |
| real BTC | ~0% | 0.49 | 8.8e-7 | 0.00260 |

`sanity.py` also moved toward real: kurtosis 8.83 → 11.12 (real ~10), vol
clustering ACF1 0.076 → 0.130 (real ~0.27).

### One trap, documented in the config

`bg_inventory_limit_btc` must sit **above** the inventory ordinary two-way flow
pushes onto the LP and **below** what a cascade delivers. The background flow
caps its own inventory at `bg_flow_inventory_cap_btc` (5 BTC) and the LP is its
counterparty, so the LP routinely carries ±5 BTC with nothing wrong. At a limit
of **3** it stopped quoting in *calm* markets: one-sided book 9% of steps, median
spread 1.0e-3 (~1000× real), local volatility 0.029 (~11× real). At 15 it is
invisible in calm markets and still binds in a cascade; results are flat between
15 and 40. `test_the_limit_must_exceed_what_ordinary_flow_pushes_onto_the_lp`
guards this.

### Why the risk metric used to be all-or-nothing

Two causes, neither of them the leverage number itself.

**Traders sold into their own losses.** `PositionTaker` re-targeted
`leverage × equity / mark` on every act, so as equity fell it *reduced* its
position to restore the target. That is a volatility-targeting strategy: it
self-deleverages and is almost impossible to liquidate. The trace that showed it
(stress, target 10×, seed 0):

    step 301  price 94876  position 0.0940  equity 494.6  leverage 18.02
    step 306  price 92266  position 0.0940  equity 352.6  leverage 24.88  <- worst
    step 376  price 94964  position 0.0510  equity 484.8  leverage  9.99  <- sold

It touched 24.88× and survived, then cut its position almost in half. Only at a
20× target was the margin buffer (1/20 − 1% = 4%) thin enough that a fast move
landed before the trader could rebalance. `rebalance="hold"` is now the default:
size the position once from starting equity, then hold that many BTC, so
leverage rises as equity falls. `rebalance="constant_leverage"` keeps the old
behaviour, because *"do self-deleveraging traders cascade?"* is a real Phase-2
question — it is just the wrong baseline.

**Everyone had the same leverage, so everyone had the same liquidation price.**
With maintenance margin at 1%, a held long needs roughly a (1/L − 1%) drop to
breach: 48% at 2×, 19% at 5×, 9.1% at 10×, 4.0% at 20×. The stress path's index
drawdown is ~8%, which sits *between* the 10× and 20× thresholds — so a uniform
crowd gave nothing up to 10× and everything at 20×, with every breach landing in
the same step (max liquidations per step equalled the number of distinct accounts
liquidated). Leverage is now drawn per trader, lognormal around the median
(`leverage_sigma=0.6`, clipped to 1–20×), which spreads the thresholds out.
`leverage_sigma=0` reproduces a uniform crowd.

Distinct accounts liquidated, stress, `long_bias=0.7`, 3 seeds, 600 steps:

| median leverage | 2× | 3× | 5× | 8× | 10× | 15× | 20× | levels responding |
|---|---|---|---|---|---|---|---|---|
| constant leverage, uniform (old) | 0 | 0 | 0 | 0 | 0 | 20.0 | 35.0 | **2/7** |
| hold, uniform | 0 | 0 | 0 | 0 | 0 | 35.0 | 35.0 | 2/7 |
| **hold + dispersed (new)** | 0 | 0.7 | 2.0 | 9.0 | 18.0 | 27.0 | 31.3 | **6/7** |

Hold mode alone does not help — it makes the cliff *more* severe when it fires
(bad debt $6,544 at 15× against $34 before). Dispersion is what makes the
response graded. Both together also spread the breaches over time: at 10×, 18
accounts liquidate across 7.3 steps with at most 15.7 in any one step, instead of
all of them at once.

Bad debt responds smoothly too: $0, $0, $115, $889, $2,252, $7,444, $11,752.

Calm-market microstructure is unchanged by this (normal, median 5×, 5 seeds:
one-sided 0.0%, near depth 0.403, spread 1.01e-6, hourly vol 0.00273), and
amplification improved, because holding traders no longer push the price down on
their own: at 10× the dip is −2.99% with liquidations and −0.10% without, so
−2.89 pp of it is now attributable to forced selling (it was −1.26 pp).

### What the flow can and cannot reproduce

The background flow used to draw heavy-tailed order sizes and then **renormalise
them so each minute's total equalled `bg_flow_btc_per_min`**. That pinned volume
at 0.170 BTC/min at *both* 2.2 and 80 orders/min, with three consequences: no
volume dispersion and so no volume/volatility relationship; a
volatility-excitement modulator that added no volume at all, only more and
smaller orders; and, at 80 orders/min, ~73% of draws pushed below the lot size
and silently dropped. Phase-0 validation measured a price-impact R² of 0.04
against 0.36 in real data — regressing price change on an order flow whose total
is constant by construction.

Now the **distribution** is calibrated once and each minute's volume is whatever
the draws give. Sizes keep their real shape (median 0.002 BTC = 2 lots; σ = 2.754
= √(2 ln 44.4), the real mean/median ratio) and the arrival rate is *derived* so
expected volume per minute hits the target.

Two bugs surfaced while doing it. The excitement multiplier was never centred, so
it inflated total volume instead of redistributing it across time: for a normal
move E|z| = √(2/π) = 0.798, giving a mean multiplier of 1 + 2.0×0.798 = 2.60, and
measured volume came out **2.57×** the target. It is now mean-preserving, the same
way `index_price.py` already centres its AR(1) vol multiplier. And the derived
rate lands near 7/min rather than the depth-scaled real count of 2.19/min — the
gap is exactly the size tail the cap removes.

### Three anchors that cannot all hold at once

Worth stating plainly, because Phase 0 kept rediscovering it as a bug:

* near depth 0.49 BTC/side (real 123 × the 0.004 scale),
* volume 0.194 BTC/min (real 48.5 × 0.004),
* the real trade-size *shape* (median 0.002 BTC, mean/median 44.4).

`volume/depth` survives the scaling (real 48.5/123 = 0.394; sim 0.194/0.49 =
0.396). `mean trade size / depth` does not: real is 0.0887/123 = 0.00072, and
keeping real-scale sizes in a 0.49 BTC book gives 0.181 — **251× more impactful
per order**. Preserving it instead would need a mean trade of 0.00035 BTC, which
is 0.35 lots: **below the exchange's own grid**. At a 0.004 scale the mini-market
is finer than its 0.001 BTC lot size, and a 0.5 BTC order is already the whole
near book. So the size tail cannot be as heavy as real, and the cap must do the
truncating — it removes about 64% of the distribution's mean.

### The cap is the dial, and here is what it buys

Normal scenario, median 5×, 5 seeds, 600 steps:

| cap | rate | BTC/min | vol CV | vol~\|r\| | impact R² | kurtosis | vol ACF1 | hourly vol | spread |
|---|---|---|---|---|---|---|---|---|---|
| 0.5 | 6.0 | 0.200 | 1.36 | 0.49 | 0.180 | 43.4 | 0.381 | 0.0083 | 1.0e-6 |
| **0.3** | **7.4** | **0.202** | **1.12** | **0.48** | **0.202** | **13.0** | **0.338** | **0.0052** | **1.2e-4** |
| 0.2 | 8.7 | 0.201 | 1.00 | 0.48 | 0.129 | 13.6 | 0.232 | 0.0042 | 2.0e-4 |
| 0.1 | 12.0 | 0.196 | 0.85 | 0.46 | 0.098 | 9.8 | 0.162 | 0.0036 | 1.2e-4 |
| 0.05 | 17.0 | 0.197 | 0.75 | 0.46 | 0.054 | 8.2 | 0.144 | 0.0032 | 4.1e-5 |
| *real BTC* | *2.2* | *0.194* | *~1.0* | *>0* | *0.360* | *10.6* | *0.275* | *0.0026* | *8.8e-7* |
| *old (renormalised)* | *80* | *0.163* | *0.26* | *0.08* | *0.006* | *9.8* | *0.109* | *0.0027* | *1.0e-6* |

0.3 is the default: best impact R² of any setting, kurtosis 13.0 against 43.4 at
the old cap, and the closest volatility clustering. **Be honest about the cost in
the write-up:** against the old flow, hourly volatility goes from 1.05× real to
2.0×, and median spread from 1.15× to 138×. The old spread and volatility
matched *because the flow had no price effect at all* — a market whose order flow
cannot move the price trivially inherits its volatility from the index. Those two
numbers got worse because impact started existing.

The liquidation response is unaffected by the cap (distinct accounts liquidated
at 2×/5×/10×/20×: 0.0/2.7/18.0/31.3 at cap 0.5, 0.0/3.0/18.0/31.3 at cap 0.1),
because liquidations are driven by the index path and the median mark rule, not
by local flow noise. So this change does not move the headline risk metric.

### Reach sets the maximum representable cascade depth

The dip floor tracks the ladder's reach almost exactly (stress, 20×, 3 seeds):

| growth | levels | reach | dip ON | dip OFF | amplification |
|---|---|---|---|---|---|
| 1.18 | 20 | 2.47% | −3.07% | −1.80% | −1.27 pp |
| 1.25 | 20 | 5.47% | −6.74% | −5.36% | −1.38 pp |
| 1.18 | 28 | 9.58% | −11.70% | −6.94% | −4.75 pp |

The default stays at 2.47% because it gives the best calm-market microstructure.
If you need deeper cascades — the real 10 Oct drawdown was 15% — raise
`bg_level_gap_growth` or `bg_levels`, and re-check the calm-market table above,
because a wider reach puts more of the book in the tail.
