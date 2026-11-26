"""
The trader population: a fixed number of SLOTS, each held by a scripted
position-taker or, in the experiment, by an LLM agent.

The good part of the Phase-0 design is KEPT -- "replace, not add":
  * The market always has M traders with fixed cash. n_llm = 0 is the control
    (all scripted). Raising n_llm SWAPS scripted traders for LLM agents, so total
    traders and total capital never change -- any difference in risk comes from
    WHO DECIDES, not from a bigger market.
  * Slots (cash, leverage, direction, account id) depend only on the seed, never
    on n_llm. The swap order is fixed per seed. A scripted trader that is not
    swapped behaves identically whatever n_llm is (its own RNG stream).

What was CUT from Phase-0: the five-type mix, the random cash_range draws, and
the per-type parameter draws. One archetype and uniform cash (the mini-market
$1000), so the only things that vary between runs are the agent mix and the
scenario.

Leverage, however, IS drawn per trader (see `leverage_sigma`). That is not a
Phase-0 leftover but a fix: a crowd on one leverage has one liquidation
threshold, which makes the risk metric all-or-nothing.
"""

import math
import random
from dataclasses import dataclass, field

from agents import MakerAgent, PositionTaker


@dataclass(frozen=True)
class PopulationSpec:
    m_traders: int = 50               # total traders; the same in every condition
    collateral: float = 1000.0        # uniform mini-market cash per trader
    target_leverage: float = 5.0      # MEDIAN target leverage (raise it to stress)
    act_prob: float = 0.4
    long_bias: float = 0.5            # share of traders that are long (0.5 = balanced)
    momentum: float = 0.0             # 0 = fixed-direction; >0 adds a trend tilt
    rebalance: str = "hold"           # "hold" or "constant_leverage" (see agents.py)

    # ---- how much of the crowd provides liquidity ------------------------
    # Makers quote from their own capital and may widen or withdraw, so near
    # depth becomes something the POPULATION supplies and can take away, rather
    # than a constant the exchange's background LP posts whatever happens. The
    # LP becomes a backstop behind them (SimConfig.bg_backstop_only).
    # maker_share = 0 reproduces the taker-only market.
    maker_share: float = 0.2          # share of slots that are makers
    maker_half_spread_ticks: float = 1.5   # real BTCUSDT spread is ~1 tick
    # Makers own the TOUCH; the background LP's ladder provides the DEPTH
    # behind them. Quote size is deliberately small: at 0.05 BTC each, ten
    # makers put 0.5 BTC at the touch against ~0.23 BTC/min of flow, which
    # cannot get through it -- trades then all happen at the same two prices and
    # 25% of minutes close unchanged (real: 3.8%). Phase 0 hit the same wall and
    # noted it. Swept 0.003-0.05; 0.003 gives flat minutes 5.8% and the best
    # volume dispersion.
    maker_quote_size: float = 0.003   # per side, per maker
    maker_inventory_limit: float = 0.015
    maker_vol_withdraw: float = 0.003      # widen above this 5-step move, leave above 2x

    # ---- spread of leverage across the crowd -----------------------------
    # Leverage decides a trader's liquidation price, so a crowd on ONE leverage
    # has ONE threshold and the risk metric can only answer all-or-nothing. With
    # maintenance margin at 1%, a held long needs roughly a (1/L - 1%) drop to
    # breach: 48% at 2x, 19% at 5x, 9.1% at 10x, 4.0% at 20x. The stress path's
    # index drawdown is ~8%, which sits between the 10x and 20x thresholds --
    # so a uniform crowd gave 0 liquidations up to 10x and ~460 at 20x, and every
    # breach landed in the SAME step (max liquidations per step equalled the
    # number of distinct accounts liquidated).
    #
    # Drawing leverage per trader spreads the thresholds out, so raising the
    # median moves progressively more of the crowd across its own threshold and
    # the metric responds smoothly. Lognormal: median = target_leverage, with a
    # long right tail (a few much more aggressive traders), clipped to the range
    # below. leverage_sigma = 0 reproduces a uniform crowd.
    leverage_sigma: float = 0.6
    leverage_min: float = 1.0
    leverage_max: float = 20.0        # keep at or below SimConfig.max_leverage


@dataclass(frozen=True)
class Slot:
    index: int
    account_id: str
    kind: str                         # "taker" or "maker"
    cash: float
    leverage: float
    direction: int
    swap_rank: int = 0                # 0 = first slot an LLM agent takes over


@dataclass
class Population:
    traders: list
    slots: list
    llm_ids: set

    def groups(self) -> dict:
        """account id -> 'llm' or 'normal' (for comparing the two in analysis)."""
        return {s.account_id: ("llm" if s.account_id in self.llm_ids else "normal")
                for s in self.slots}


def _draw_leverage(spec: PopulationSpec, rng: random.Random) -> float:
    """Lognormal around the median target, clipped to the allowed range."""
    if spec.leverage_sigma <= 0:
        value = spec.target_leverage
    else:
        value = spec.target_leverage * math.exp(spec.leverage_sigma * rng.gauss(0.0, 1.0))
    return max(spec.leverage_min, min(spec.leverage_max, value))


def make_slots(seed: int, spec: PopulationSpec) -> list:
    rng = random.Random(f"{seed}|population")
    if not 0.0 <= spec.maker_share <= 1.0:
        raise ValueError(f"maker_share must be in [0, 1], got {spec.maker_share}")

    n_makers = round(spec.maker_share * spec.m_traders)
    kinds = ["maker"] * n_makers + ["taker"] * (spec.m_traders - n_makers)
    rng.shuffle(kinds)

    n_takers = spec.m_traders - n_makers
    n_long = round(spec.long_bias * n_takers)
    directions = [1] * n_long + [-1] * (n_takers - n_long)
    rng.shuffle(directions)
    direction_iter = iter(directions)

    slots = [Slot(index=i, account_id=f"t{i:02d}", kind=kinds[i], cash=spec.collateral,
                  leverage=_draw_leverage(spec, rng),
                  direction=(next(direction_iter) if kinds[i] == "taker" else 0))
             for i in range(spec.m_traders)]

    # Fixed, seed-determined swap order, spread EVENLY ACROSS KINDS so that
    # raising n_llm replaces a representative mix rather than all the makers
    # first. Without this, a treatment run could differ from its control by
    # having lost its liquidity providers, which is not the thing under study.
    rank = {}
    for kind in ("taker", "maker"):
        members = [s.index for s in slots if s.kind == kind]
        rng.shuffle(members)
        for position, index in enumerate(members):
            # interleave: a maker's place in the queue is scaled to its share
            rank[index] = (position + 0.5) / max(1, len(members))
    order = sorted(rank, key=lambda i: (rank[i], i))
    final = {index: r for r, index in enumerate(order)}

    return [Slot(index=s.index, account_id=s.account_id, kind=s.kind, cash=s.cash,
                 leverage=s.leverage, direction=s.direction, swap_rank=final[s.index])
            for s in slots]


def make_scripted(slot: Slot, seed: int, spec: PopulationSpec):
    if slot.kind == "maker":
        return MakerAgent(slot.account_id, seed,
                          half_spread_ticks=spec.maker_half_spread_ticks,
                          quote_size=spec.maker_quote_size,
                          inventory_limit=spec.maker_inventory_limit,
                          vol_withdraw=spec.maker_vol_withdraw)
    return PositionTaker(slot.account_id, seed, target_leverage=slot.leverage,
                         act_prob=spec.act_prob, direction=slot.direction,
                         momentum=spec.momentum, rebalance=spec.rebalance)


def build_population(seed: int, spec: PopulationSpec = None, n_llm: int = 0,
                     llm_factory=None) -> Population:
    """n_llm = 0 gives the all-scripted control.

    llm_factory(slot, seed) must return an agent whose `account_id` equals the
    slot's and that has an `act(view)` method (same contract as PositionTaker).
    """
    spec = spec or PopulationSpec()
    if not 0 <= n_llm <= spec.m_traders:
        raise ValueError(f"n_llm must be between 0 and {spec.m_traders}, got {n_llm}")
    if n_llm > 0 and llm_factory is None:
        raise ValueError("n_llm > 0 needs an llm_factory (no LLM agents are wired up yet)")
    slots = make_slots(seed, spec)
    taken = {s.index for s in sorted(slots, key=lambda s: s.swap_rank)[:n_llm]}
    traders, llm_ids = [], set()
    for slot in slots:
        if slot.index in taken:
            agent = llm_factory(slot, seed)
            if agent.account_id != slot.account_id:
                raise ValueError(f"the agent must use the slot's account id {slot.account_id}")
            traders.append(agent)
            llm_ids.add(slot.account_id)
        else:
            traders.append(make_scripted(slot, seed, spec))
    return Population(traders=traders, slots=slots, llm_ids=llm_ids)


def add_accounts(exchange, population: Population) -> None:
    for slot in population.slots:
        exchange.add_account(slot.account_id, collateral=slot.cash)
