"""
The trader population: a fixed number of SLOTS, each held either by a normal
(scripted) trader or, in the experiment, by an LLM agent.

Design ("replace", not "add"):
  * The market always has M traders with fixed cash. n_llm = 0 is the control
    (all normal traders). Raising n_llm swaps normal traders for LLM agents, so
    total traders and total capital never change: any difference in risk comes
    from WHO DECIDES, not from a bigger market.
  * Fairness rules, enforced by construction and by tests:
      1. The slots (type, cash, leverage, parameters, account id) depend only on
         the seed and the spec, never on n_llm.
      2. The order in which slots are handed to LLM agents is fixed per seed and
         spread proportionally across the trader types.
      3. An LLM agent takes over a slot's account (same id, same cash).
      4. Every normal bot keeps its own random stream (keyed on seed and account
         id), so a bot that is not swapped behaves the same whatever n_llm is.

PLACEHOLDER WARNING: the mix, cash range, leverage range and strategy
parameters below are guesses. Per-account leverage is not visible in public
trade data, so that range stays an assumption to be stress-tested. Only the
noise-trader size shape (median 0.002 BTC, heavy tail) is anchored on real data,
and only on one hour so far.
"""

import math
import random
from dataclasses import dataclass, field

from bots import ContrarianBot, MakerBot, NoiseBot, TrendBot, ValueBot


@dataclass(frozen=True)
class PopulationSpec:
    m_traders: int = 100                      # total number of traders; the same in every condition
    mix: tuple = (                            # PLACEHOLDER shares of each trader type (must sum to 1)
        ("noise", 0.45),
        ("trend", 0.20),
        ("contrarian", 0.10),
        ("value", 0.15),
        ("maker", 0.10),
    )
    cash_range: tuple = (5_000.0, 20_000.0)   # PLACEHOLDER: starting cash, drawn per trader
    leverage_range: tuple = (2.0, 10.0)       # PLACEHOLDER: target leverage, drawn per trader (not observable in public data)
    # noise traders
    noise_size_median: float = 0.002          # [real anchor, 1 hour] median trade size, BTC
    noise_size_sigma: float = 2.5             # [real anchor, 1 hour] spread of sizes (mean/median is about 27)
    noise_size_scale: float = 1.0             # PLACEHOLDER: maps real sizes onto our smaller market
    noise_max_size: float = 5.0               # PLACEHOLDER: cap on one order, BTC
    noise_act_prob: tuple = (0.2, 0.6)        # PLACEHOLDER: chance of trading on a tick
    noise_passive_prob: float = 0.3           # PLACEHOLDER: share of orders that rest in the book
    # strategy traders
    strategy_act_prob: tuple = (0.3, 0.7)     # PLACEHOLDER
    trend_lookback: tuple = (5, 20)           # PLACEHOLDER, in ticks
    trend_threshold: tuple = (0.001, 0.003)   # PLACEHOLDER
    contrarian_threshold: tuple = (0.002, 0.005)  # PLACEHOLDER
    value_threshold: tuple = (0.001, 0.003)   # PLACEHOLDER
    # market makers
    maker_half_spread: tuple = (0.0001, 0.0005)   # PLACEHOLDER: absolute fallback (superseded below)
    maker_half_spread_ticks: tuple = (1.0, 2.5)   # real spread ~1 tick; participant quotes 1-2.5 ticks from mid
    maker_size: tuple = (0.05, 0.3)           # PLACEHOLDER, BTC per quote
    maker_inventory_limit: tuple = (0.5, 2.0) # PLACEHOLDER, BTC


@dataclass(frozen=True)
class Slot:
    index: int
    kind: str
    account_id: str
    cash: float
    leverage: float
    params: dict = field(compare=False)
    swap_rank: int = 0            # 0 = the first slot to be taken over by an LLM agent


@dataclass
class Population:
    bots: list                    # one trader object per slot (normal bot or LLM agent)
    slots: list
    llm_ids: set

    def groups(self) -> dict:
        """account id -> 'llm' or 'normal' (for comparing the two in analysis)."""
        return {s.account_id: ("llm" if s.account_id in self.llm_ids else "normal") for s in self.slots}


# ---- building the slots (never depends on n_llm) -----------------------------------
def _counts(mix: tuple, m: int) -> dict:
    total = sum(share for _, share in mix)
    if abs(total - 1.0) > 1e-9:
        raise ValueError(f"mix shares must sum to 1, got {total}")
    raw = [(kind, round(share * m, 9)) for kind, share in mix]
    counts = {kind: int(math.floor(r)) for kind, r in raw}
    leftover = m - sum(counts.values())
    by_fraction = sorted(range(len(raw)), key=lambda i: (-(raw[i][1] - math.floor(raw[i][1])), i))
    for i in by_fraction[:leftover]:
        counts[raw[i][0]] += 1
    return counts


def _draw_params(kind: str, spec: PopulationSpec, rng: random.Random) -> dict:
    if kind == "noise":
        return dict(act_prob=rng.uniform(*spec.noise_act_prob),
                    size_median=spec.noise_size_median, size_sigma=spec.noise_size_sigma,
                    size_scale=spec.noise_size_scale, max_size=spec.noise_max_size,
                    passive_prob=spec.noise_passive_prob)
    if kind == "trend":
        return dict(lookback=rng.randint(*spec.trend_lookback),
                    threshold=rng.uniform(*spec.trend_threshold),
                    act_prob=rng.uniform(*spec.strategy_act_prob))
    if kind == "contrarian":
        return dict(lookback=rng.randint(*spec.trend_lookback),
                    threshold=rng.uniform(*spec.contrarian_threshold),
                    act_prob=rng.uniform(*spec.strategy_act_prob))
    if kind == "value":
        return dict(threshold=rng.uniform(*spec.value_threshold),
                    act_prob=rng.uniform(*spec.strategy_act_prob))
    if kind == "maker":
        return dict(half_spread=None,
                    half_spread_ticks=rng.uniform(*spec.maker_half_spread_ticks),
                    size=rng.uniform(*spec.maker_size),
                    inventory_limit=rng.uniform(*spec.maker_inventory_limit))
    raise ValueError(f"unknown trader type: {kind}")


def make_slots(seed: int, spec: PopulationSpec = None) -> list:
    spec = spec or PopulationSpec()
    rng = random.Random(f"{seed}|population")
    counts = _counts(spec.mix, spec.m_traders)
    slots = []
    for kind, _ in spec.mix:
        for n in range(counts[kind]):
            slots.append(dict(
                index=len(slots), kind=kind, account_id=f"{kind}{n:02d}",
                cash=rng.uniform(*spec.cash_range), leverage=rng.uniform(*spec.leverage_range),
                params=_draw_params(kind, spec, rng),
                # position inside its own type, jittered, so sorting by this key
                # spreads each type evenly through the swap order
                key=(n + rng.random()) / counts[kind],
            ))
    order = sorted(range(len(slots)), key=lambda i: slots[i]["key"])
    rank = {i: r for r, i in enumerate(order)}
    return [Slot(index=s["index"], kind=s["kind"], account_id=s["account_id"], cash=s["cash"],
                 leverage=s["leverage"], params=s["params"], swap_rank=rank[s["index"]])
            for s in slots]


# ---- turning a slot into a trader --------------------------------------------------
def make_normal_bot(slot: Slot, seed: int):
    p, a = slot.params, slot.account_id
    if slot.kind == "noise":
        return NoiseBot(a, seed, act_prob=p["act_prob"], size_median=p["size_median"],
                        size_sigma=p["size_sigma"], size_scale=p["size_scale"],
                        max_size=p["max_size"], passive_prob=p["passive_prob"],
                        target_leverage=slot.leverage)
    if slot.kind == "trend":
        return TrendBot(a, seed, leverage=slot.leverage, lookback=p["lookback"],
                        threshold=p["threshold"], act_prob=p["act_prob"])
    if slot.kind == "contrarian":
        return ContrarianBot(a, seed, leverage=slot.leverage, lookback=p["lookback"],
                             threshold=p["threshold"], act_prob=p["act_prob"])
    if slot.kind == "value":
        return ValueBot(a, seed, leverage=slot.leverage, threshold=p["threshold"],
                        act_prob=p["act_prob"])
    if slot.kind == "maker":
        return MakerBot(a, seed, half_spread=p["half_spread"],
                        half_spread_ticks=p["half_spread_ticks"], size=p["size"],
                        inventory_limit=p["inventory_limit"])
    raise ValueError(f"unknown trader type: {slot.kind}")


def build_population(seed: int, spec: PopulationSpec = None, n_llm: int = 0,
                     llm_factory=None) -> Population:
    """Build the traders. n_llm = 0 gives the all-normal control.

    llm_factory(slot, seed) must return an agent with an `account_id` equal to the
    slot's and an `act(view)` method (the same contract as the scripted bots)."""
    spec = spec or PopulationSpec()
    if not 0 <= n_llm <= spec.m_traders:
        raise ValueError(f"n_llm must be between 0 and {spec.m_traders}, got {n_llm}")
    if n_llm > 0 and llm_factory is None:
        raise ValueError("n_llm > 0 needs an llm_factory (no LLM agents exist yet)")
    slots = make_slots(seed, spec)
    taken = {s.index for s in sorted(slots, key=lambda s: s.swap_rank)[:n_llm]}
    bots, llm_ids = [], set()
    for slot in slots:
        if slot.index in taken:
            agent = llm_factory(slot, seed)
            if agent.account_id != slot.account_id:
                raise ValueError(f"the agent must use the slot's account id {slot.account_id}")
            bots.append(agent)
            llm_ids.add(slot.account_id)
        else:
            bots.append(make_normal_bot(slot, seed))
    return Population(bots=bots, slots=slots, llm_ids=llm_ids)


def add_accounts(exchange, population: Population) -> None:
    """Open one account per slot, with the slot's cash (LLM agents get the same cash)."""
    for slot in population.slots:
        exchange.add_account(slot.account_id, collateral=slot.cash)
