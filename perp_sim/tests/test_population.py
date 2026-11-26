"""The replace-not-add guarantee -- the one the causal claim rests on.

Ported back from the Phase-0 suite after review. The minimal version shipped
only a determinism check (it built n_llm=0 twice), which does NOT test the
invariant that matters: that the slots, and every un-swapped trader, are
identical whatever n_llm is. These tests exercise n_llm > 0.

If any test here fails, a measured difference between an n_llm=0 control and an
n_llm=k treatment could come from the market changing rather than from who is
deciding -- which would invalidate the Phase-1 result.
"""

import unittest

from population import PopulationSpec, build_population, make_slots
from run import run


class InertAgent:
    """Stands in for an LLM agent: holds the slot's account and never trades."""

    kind = "llm"

    def __init__(self, account_id):
        self.account_id = account_id

    def act(self, view):
        return []


def inert_factory(slot, seed):
    return InertAgent(slot.account_id)


def signature(slots):
    return [(s.index, s.account_id, s.cash, s.leverage, s.direction, s.swap_rank)
            for s in slots]


SPEC = PopulationSpec()


def _fake_view_sequence(account_id, n=60):
    """A fixed, condition-independent view sequence to drive a trader through.

    Deliberately not taken from a live run: the point is to hold the market
    constant so any difference in behaviour must come from the trader.
    """
    from engine import MarketView, OwnView

    views, price = [], 100_000.0
    history = [price]
    for step in range(1, n + 1):
        price *= 1.0 + (0.0004 if step % 3 else -0.0006)   # deterministic zig-zag
        history.append(price)
        views.append(MarketView(
            step=step, minute=float(step), index_price=price, last_price=price,
            mark_price=price, best_bid=price - 0.1, best_ask=price + 0.1, mid=price,
            depth_bid=0.5, depth_ask=0.5, funding_rate_est=0.0, last_funding_rate=0.0,
            steps_to_funding=60 - (step % 60), open_interest=1.0,
            liq_count_last_step=0, liq_notional_last_step=0.0,
            price_history=list(history),
            own=OwnView(account_id=account_id, collateral=1000.0, equity=1000.0,
                        position=0.0, entry_price=0.0, leverage=0.0,
                        margin_buffer=None, liquidation_price=None, open_orders=0),
        ))
    return views


def _as_tuple(actions):
    return tuple((a.account_id, a.side, round(a.qty, 8),
                  None if a.price is None else round(a.price, 4), a.tif)
                 for a in actions)


class SlotTests(unittest.TestCase):
    def test_slot_count_and_unique_ids(self):
        slots = make_slots(1, SPEC)
        self.assertEqual(len(slots), SPEC.m_traders)
        self.assertEqual(len({s.account_id for s in slots}), len(slots))

    def test_slots_depend_only_on_the_seed(self):
        self.assertEqual(signature(make_slots(3, SPEC)), signature(make_slots(3, SPEC)))
        self.assertNotEqual([s.direction for s in make_slots(3, SPEC)],
                            [s.direction for s in make_slots(4, SPEC)])

    def test_slots_do_not_depend_on_how_many_llm_agents(self):
        """The core invariant: swapping in agents must not change the slots."""
        control = build_population(3, SPEC, n_llm=0)
        for k in (1, 10, SPEC.m_traders):
            swapped = build_population(3, SPEC, n_llm=k, llm_factory=inert_factory)
            self.assertEqual(signature(control.slots), signature(swapped.slots),
                             f"slots changed at n_llm={k}")

    def test_total_capital_is_constant_across_conditions(self):
        for k in (0, 5, 25, SPEC.m_traders):
            pop = build_population(3, SPEC, n_llm=k, llm_factory=inert_factory)
            self.assertAlmostEqual(sum(s.cash for s in pop.slots),
                                   SPEC.m_traders * SPEC.collateral, places=6)

    def test_long_short_balance_is_constant_across_conditions(self):
        control = sum(s.direction for s in build_population(3, SPEC, n_llm=0).slots)
        for k in (5, 25):
            pop = build_population(3, SPEC, n_llm=k, llm_factory=inert_factory)
            self.assertEqual(sum(s.direction for s in pop.slots), control)


class SwapTests(unittest.TestCase):
    def test_exactly_n_llm_slots_are_swapped_and_they_are_the_first_in_order(self):
        pop = build_population(3, SPEC, n_llm=10, llm_factory=inert_factory)
        first_ten = {s.account_id for s in sorted(pop.slots, key=lambda s: s.swap_rank)[:10]}
        self.assertEqual(pop.llm_ids, first_ten)
        self.assertEqual(sum(isinstance(t, InertAgent) for t in pop.traders), 10)
        self.assertEqual(len(pop.traders), SPEC.m_traders)

    def test_swaps_are_nested(self):
        """n_llm=5 must be a subset of n_llm=10, so neighbouring points on the
        risk curve differ only by the extra agents."""
        small = build_population(3, SPEC, n_llm=5, llm_factory=inert_factory).llm_ids
        large = build_population(3, SPEC, n_llm=10, llm_factory=inert_factory).llm_ids
        self.assertTrue(small < large)

    def test_groups_label_every_slot(self):
        pop = build_population(3, SPEC, n_llm=10, llm_factory=inert_factory)
        groups = pop.groups()
        self.assertEqual(len(groups), SPEC.m_traders)
        self.assertEqual(sum(1 for v in groups.values() if v == "llm"), 10)

    def test_an_agent_must_use_its_slot_account(self):
        with self.assertRaises(ValueError):
            build_population(3, SPEC, n_llm=1,
                             llm_factory=lambda slot, seed: InertAgent("someone-else"))

    def test_rejects_impossible_agent_counts(self):
        with self.assertRaises(ValueError):
            build_population(3, SPEC, n_llm=1)                      # no factory
        with self.assertRaises(ValueError):
            build_population(3, SPEC, n_llm=SPEC.m_traders + 1, llm_factory=inert_factory)
        with self.assertRaises(ValueError):
            build_population(3, SPEC, n_llm=-1, llm_factory=inert_factory)


class UnswappedTradersAreUnaffectedTests(unittest.TestCase):
    def test_an_unswapped_trader_has_the_same_decision_rule_whatever_n_llm_is(self):
        """A scripted trader that was not replaced must be the SAME trader.

        Note what this can and cannot claim. Its orders in a live run are not
        identical across conditions, and should not be: the agents that were
        swapped in trade differently, so the market state the survivor reacts to
        genuinely differs from step 2 onward. What must be invariant is the
        trader itself -- its parameters and its random stream, which is keyed on
        (seed, account id) and never on its position in the list. So we drive the
        two copies through an IDENTICAL view sequence and require identical
        actions. That is the guarantee; a difference here would mean n_llm
        changes the control traders, not just the market."""
        control = build_population(5, SPEC, n_llm=0)
        treated = build_population(5, SPEC, n_llm=10, llm_factory=inert_factory)
        survivor = next(s.account_id for s in treated.slots
                        if s.account_id not in treated.llm_ids)
        a = next(t for t in control.traders if t.account_id == survivor)
        b = next(t for t in treated.traders if t.account_id == survivor)

        self.assertEqual((a.direction, a.target_leverage, a.act_prob, a.momentum),
                         (b.direction, b.target_leverage, b.act_prob, b.momentum))

        views = _fake_view_sequence(survivor, n=60)
        actions_a = [_as_tuple(a.act(v)) for v in views]
        actions_b = [_as_tuple(b.act(v)) for v in views]
        self.assertTrue(any(actions_a), "the trader never acted; test is vacuous")
        self.assertEqual(actions_a, actions_b)

    # NOTE: there is deliberately no test asserting that the BOOK matches across
    # conditions, not even on step 1. The traders that were swapped out were
    # active on step 1, so replacing them with inert agents changes the book
    # immediately. That is the intended effect of the treatment, not a fairness
    # violation. What must stay fixed is the slots, the total capital, the index
    # path and each surviving trader's own decision rule -- all tested above.

    def test_index_path_is_identical_across_conditions(self):
        """The outside world must not notice the agent mix."""
        control, _ = run("normal", n_steps=40, seed=5, spec=SPEC)
        treated, _ = run("normal", n_steps=40, seed=5, spec=SPEC,
                         n_llm=25, llm_factory=inert_factory)
        self.assertEqual([r.index_price for r in control.records],
                         [r.index_price for r in treated.records])

    def test_inert_agents_hold_no_position(self):
        ex, pop = run("normal", n_steps=40, seed=5, spec=SPEC,
                      n_llm=10, llm_factory=inert_factory)
        for account_id in pop.llm_ids:
            self.assertEqual(ex.accounts[account_id].position, 0.0)


if __name__ == "__main__":
    unittest.main()
