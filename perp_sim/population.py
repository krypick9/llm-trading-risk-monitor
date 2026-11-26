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

What was CUT from Phase-0: the five-type mix, the random cash_range/leverage_range
draws, and the per-type parameter draws. Everything here is a fixed, documented
value. One archetype, uniform cash (the mini-market $1000), one leverage, and a
`long_bias` knob -- so the only things that vary between runs are the agent mix
and the scenario.
"""

import random
from dataclasses import dataclass, field

from agents import PositionTaker


@dataclass(frozen=True)
class PopulationSpec:
    m_traders: int = 50               # total traders; the same in every condition
    collateral: float = 1000.0        # uniform mini-market cash per trader
    target_leverage: float = 5.0      # uniform target leverage (raise it to stress)
    act_prob: float = 0.4
    long_bias: float = 0.5            # share of traders that are long (0.5 = balanced)
    momentum: float = 0.0             # 0 = fixed-direction; >0 adds a trend tilt


@dataclass(frozen=True)
class Slot:
    index: int
    account_id: str
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


def make_slots(seed: int, spec: PopulationSpec) -> list:
    rng = random.Random(f"{seed}|population")
    n_long = round(spec.long_bias * spec.m_traders)
    directions = [1] * n_long + [-1] * (spec.m_traders - n_long)
    rng.shuffle(directions)
    slots = [Slot(index=i, account_id=f"t{i:02d}", cash=spec.collateral,
                  leverage=spec.target_leverage, direction=directions[i])
             for i in range(spec.m_traders)]
    # fixed, seed-determined swap order
    order = list(range(spec.m_traders))
    rng.shuffle(order)
    rank = {idx: r for r, idx in enumerate(order)}
    return [Slot(index=s.index, account_id=s.account_id, cash=s.cash,
                 leverage=s.leverage, direction=s.direction, swap_rank=rank[s.index])
            for s in slots]


def make_scripted(slot: Slot, seed: int, spec: PopulationSpec) -> PositionTaker:
    return PositionTaker(slot.account_id, seed, target_leverage=slot.leverage,
                         act_prob=spec.act_prob, direction=slot.direction,
                         momentum=spec.momentum)


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
