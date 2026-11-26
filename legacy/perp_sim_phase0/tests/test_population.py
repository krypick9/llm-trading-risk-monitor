import dataclasses
import unittest
from collections import Counter

from engine import SCENARIOS
from population import PopulationSpec, add_accounts, build_population, make_slots
from run_baseline import run


class InertAgent:
    """Stands in for an LLM agent in tests: holds the slot's account and never trades."""
    kind = "llm"

    def __init__(self, account_id):
        self.account_id = account_id

    def act(self, view):
        return []


def inert_factory(slot, seed):
    return InertAgent(slot.account_id)


def slot_signature(slots):
    return [(s.index, s.kind, s.account_id, s.cash, s.leverage, s.params, s.swap_rank) for s in slots]


class SlotTests(unittest.TestCase):
    def test_counts_follow_the_mix(self):
        slots = make_slots(1)
        self.assertEqual(len(slots), 100)
        self.assertEqual(Counter(s.kind for s in slots),
                         {"noise": 45, "trend": 20, "contrarian": 10, "value": 15, "maker": 10})

    def test_account_ids_are_unique(self):
        slots = make_slots(1)
        self.assertEqual(len({s.account_id for s in slots}), len(slots))

    def test_an_awkward_size_still_adds_up(self):
        self.assertEqual(len(make_slots(1, PopulationSpec(m_traders=37))), 37)

    def test_a_mix_that_does_not_add_to_one_is_rejected(self):
        with self.assertRaises(ValueError):
            make_slots(1, PopulationSpec(mix=(("noise", 0.5), ("trend", 0.4))))

    def test_slots_depend_only_on_the_seed(self):
        self.assertEqual(slot_signature(make_slots(3)), slot_signature(make_slots(3)))
        self.assertNotEqual([s.cash for s in make_slots(3)], [s.cash for s in make_slots(4)])

    def test_slots_do_not_depend_on_how_many_llm_agents(self):
        control = build_population(3)
        swapped = build_population(3, n_llm=10, llm_factory=inert_factory)
        self.assertEqual(slot_signature(control.slots), slot_signature(swapped.slots))


class SwapTests(unittest.TestCase):
    def test_exactly_n_llm_slots_are_swapped_and_they_are_the_first_in_order(self):
        pop = build_population(3, n_llm=10, llm_factory=inert_factory)
        first_ten = {s.account_id for s in sorted(pop.slots, key=lambda s: s.swap_rank)[:10]}
        self.assertEqual(pop.llm_ids, first_ten)
        self.assertEqual(sum(isinstance(b, InertAgent) for b in pop.bots), 10)
        self.assertEqual(len(pop.bots), 100)

    def test_swaps_are_nested_so_neighbouring_points_differ_only_by_the_extra_agents(self):
        small = build_population(3, n_llm=5, llm_factory=inert_factory).llm_ids
        large = build_population(3, n_llm=10, llm_factory=inert_factory).llm_ids
        self.assertTrue(small < large)

    def test_swaps_are_spread_across_trader_types(self):
        pop = build_population(3, n_llm=10, llm_factory=inert_factory)
        kind_of = {s.account_id: s.kind for s in pop.slots}
        swapped = Counter(kind_of[a] for a in pop.llm_ids)
        for kind, share in PopulationSpec().mix:
            self.assertLessEqual(abs(swapped[kind] - share * 10), 1.0, kind)

    def test_control_has_no_llm_agents(self):
        pop = build_population(3)
        self.assertEqual(pop.llm_ids, set())
        self.assertEqual(set(pop.groups().values()), {"normal"})

    def test_bad_requests_are_refused(self):
        with self.assertRaises(ValueError):
            build_population(3, n_llm=1)                       # no factory
        with self.assertRaises(ValueError):
            build_population(3, n_llm=101, llm_factory=inert_factory)
        with self.assertRaises(ValueError):
            build_population(3, n_llm=-1, llm_factory=inert_factory)

    def test_an_agent_must_use_the_slot_account(self):
        with self.assertRaises(ValueError):
            build_population(3, n_llm=1, llm_factory=lambda slot, seed: InertAgent("someone-else"))

    def test_llm_agents_start_with_the_same_cash_as_the_traders_they_replace(self):
        from engine import Exchange, SimConfig
        cash = {}
        for n in (0, 10):
            pop = build_population(3, n_llm=n, llm_factory=inert_factory)
            ex = Exchange(SimConfig(n_steps=5, seed=3), SCENARIOS["normal-thick-synthetic-no-event"])
            add_accounts(ex, pop)
            cash[n] = {a: acct.collateral for a, acct in ex.accounts.items()}
        self.assertEqual(cash[0], cash[10])


class BaselineBehaviourTests(unittest.TestCase):
    def test_traders_that_are_not_swapped_act_the_same_in_every_condition(self):
        control, _ = run("stress-thin-synthetic-no-event", 3, n_steps=1)
        swapped, pop = run("stress-thin-synthetic-no-event", 3, n_llm=10, n_steps=1, llm_factory=inert_factory)

        def orders(exchange, keep):
            return sorted(o for o in exchange.order_log if o[1] in keep)

        kept = {s.account_id for s in pop.slots} - pop.llm_ids
        self.assertTrue(orders(control, kept))                  # they really did trade
        self.assertEqual(orders(control, kept), orders(swapped, kept))

    def test_noise_traders_send_small_heavy_tailed_balanced_orders(self):
        spec = PopulationSpec(m_traders=40, mix=(("noise", 1.0),))
        exchange, _ = run("normal-thick-synthetic-no-event", 1, spec=spec, n_steps=60)
        sizes = sorted(o[3] for o in exchange.order_log)
        self.assertGreater(len(sizes), 200)
        median, mean = sizes[len(sizes) // 2], sum(sizes) / len(sizes)
        self.assertTrue(0.001 <= median <= 0.01, median)
        self.assertGreater(mean, 5 * median)                    # many tiny orders, a few large
        self.assertGreaterEqual(min(sizes), 0.001 - 1e-9)
        self.assertLessEqual(max(sizes), 5.0 + 1e-9)
        buys = sum(1 for o in exchange.order_log if o[2] > 0) / len(exchange.order_log)
        self.assertTrue(0.4 <= buys <= 0.6, buys)

    def test_control_never_contains_the_artificial_crowd(self):
        kinds = {b.kind for b in build_population(3).bots}
        self.assertEqual(kinds, {"noise", "trend", "contrarian", "value", "maker"})

    def test_control_is_reproducible(self):
        a, _ = run("stress-thin-synthetic-no-event", 5, n_steps=30)
        b, _ = run("stress-thin-synthetic-no-event", 5, n_steps=30)
        self.assertEqual([dataclasses.asdict(r) for r in a.records],
                         [dataclasses.asdict(r) for r in b.records])

    def test_control_runs_in_all_eight_scenarios(self):
        for name in SCENARIOS:
            exchange, _ = run(name, 2, n_steps=20)
            self.assertEqual(len(exchange.records), 20, name)


if __name__ == "__main__":
    unittest.main()
