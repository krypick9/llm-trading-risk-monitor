"""Unit tests for the background taker flow and the backstop LP.

Run:  python -m unittest discover -s tests -t .
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from engine import BUY, SELL, Exchange, Scenario, SimConfig  # noqa: E402
from engine.flow import BackgroundTakerFlow, FLOW_ID         # noqa: E402
from bots import MakerBot, build_reference_population        # noqa: E402


def make_scenario(**ov):
    base = dict(name="t", regime="normal", sigma_hourly=0.0026,
                jump_rate_per_hour=0.8, jump_mean=-0.0017, jump_std=0.0007,
                shock_frac=None, shock_return=0.0,
                bg_depth_btc=0.5, bg_half_spread=8.8e-7, bg_walkaway=False)
    base.update(ov)
    return Scenario(**base)


def run_bare(cfg, scenario):
    """No participant bots at all: whatever trades must come from the flow."""
    ex = Exchange(cfg, scenario)
    for _ in range(cfg.n_steps):
        ex.step([])
    return ex


class TestBackgroundFlow(unittest.TestCase):
    def test_flow_is_off_by_default(self):
        cfg = SimConfig(n_steps=50, seed=1)
        ex = run_bare(cfg, make_scenario())
        self.assertEqual(sum(r.volume for r in ex.records), 0.0)

    def test_flow_creates_trades(self):
        cfg = SimConfig(n_steps=120, seed=7, bg_flow_enabled=True)
        ex = run_bare(cfg, make_scenario())
        volume = sum(r.volume for r in ex.records)
        self.assertGreater(volume, 0.0)
        flat = sum(1 for r in ex.records if r.n_trades == 0)
        self.assertLess(flat / len(ex.records), 0.10)

    def test_volume_hits_the_calibration_target(self):
        cfg = SimConfig(n_steps=600, seed=3, bg_flow_enabled=True,
                        bg_flow_btc_per_min=0.194, bg_flow_orders_per_min=2.19)
        ex = run_bare(cfg, make_scenario())
        per_min = sum(r.volume for r in ex.records) / len(ex.records)
        self.assertAlmostEqual(per_min, 0.194, delta=0.194 * 0.25)

    def test_index_path_is_unaffected_by_flow(self):
        off = run_bare(SimConfig(n_steps=80, seed=5), make_scenario())
        on = run_bare(SimConfig(n_steps=80, seed=5, bg_flow_enabled=True),
                      make_scenario())
        self.assertEqual(off.index_history, on.index_history)

    def test_flow_is_deterministic_per_seed(self):
        a = run_bare(SimConfig(n_steps=60, seed=11, bg_flow_enabled=True),
                     make_scenario())
        b = run_bare(SimConfig(n_steps=60, seed=11, bg_flow_enabled=True),
                     make_scenario())
        self.assertEqual([r.volume for r in a.records],
                         [r.volume for r in b.records])

    def test_flow_inventory_stays_bounded(self):
        cfg = SimConfig(n_steps=600, seed=2, bg_flow_enabled=True,
                        bg_flow_inventory_cap_btc=5.0)
        ex = run_bare(cfg, make_scenario())
        flow = ex.bg_flow
        self.assertIsNotNone(flow)
        self.assertLessEqual(abs(flow.inventory_btc), 5.0 + cfg.lot_size)


class TestBackstopLP(unittest.TestCase):
    def test_backstop_quotes_outside_participant_quotes(self):
        cfg = SimConfig(n_steps=10, seed=9, bg_flow_enabled=False,
                        bg_backstop_only=True, bg_backstop_extra=0.0002)
        ex = Exchange(cfg, make_scenario())
        bots = [MakerBot(f"maker{i}", seed=9, half_spread_ticks=2.0, size=0.2,
                         inventory_limit=1.0) for i in range(3)]
        for b in bots:
            ex.add_account(b.account_id, collateral=1000.0)
        for _ in range(cfg.n_steps):
            acts = []
            for b in bots:
                acts += b.act(ex.view(b.account_id))
            ex.step(acts)

        part_bids, part_asks, bg_bids, bg_asks = [], [], [], []
        for price, queue in list(ex.book.bids.items()) + list(ex.book.asks.items()):
            for o in queue:
                (bg_bids if o.account == "__background__" else part_bids).append(price) \
                    if o.side == BUY else \
                    (bg_asks if o.account == "__background__" else part_asks).append(price)
        self.assertTrue(part_bids, "no participant bids rested")
        self.assertTrue(bg_bids, "no backstop bids rested")
        self.assertLess(max(bg_bids), max(part_bids))
        self.assertGreater(min(bg_asks), min(part_asks))

    def test_backstop_off_keeps_legacy_ladder(self):
        cfg = SimConfig(n_steps=5, seed=9, bg_backstop_only=False)
        ex = Exchange(cfg, make_scenario())
        ex.step([])
        bg = [(o.side, price) for price, q in list(ex.book.bids.items()) + list(ex.book.asks.items())
              for o in q if o.account == "__background__"]
        self.assertTrue(bg)

    def test_full_population_with_flow_has_live_market(self):
        """End-to-end: reference population (NO NoiseBot) + background flow."""
        cfg = SimConfig(n_steps=300, seed=7, bg_flow_enabled=True,
                        bg_flow_orders_per_min=40.0, bg_flow_btc_per_min=0.194)
        ex = Exchange(cfg, make_scenario())
        bots = build_reference_population(seed=7, n_total=50)
        for b in bots:
            ex.add_account(b.account_id, collateral=1000.0)
        for _ in range(cfg.n_steps):
            acts = []
            for b in bots:
                acts += b.act(ex.view(b.account_id))
            ex.step(acts)
        recs = ex.records
        flat = sum(1 for r in recs if r.price_return == 0.0) / len(recs)
        # Mechanism gate: flow must break the stale market (the pre-fix
        # reference population sat at ~52-60% flat minutes). Matching the
        # real zero-return rate (3.8%) is a calibration/validation-gate
        # metric over many seeds -- not a unit-test invariant.
        self.assertLess(flat, 0.40)
        self.assertGreater(sum(r.n_trades for r in recs) / len(recs), 1.0)


if __name__ == "__main__":
    unittest.main()
