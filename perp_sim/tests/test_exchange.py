import dataclasses
import unittest

from engine import (BUY, SELL, SCENARIOS, Exchange, OrderRequest, Scenario, SimConfig)

# A calm, deterministic world: no random wiggles, no jumps, one scheduled shock.
def quiet_scenario(shock_return=-0.10, shock_frac=0.5, walkaway=False, depth=12.0):
    return Scenario(name="test", regime="stress", sigma_hourly=0.0, jump_rate_per_hour=0.0,
                    jump_mean=0.0, jump_std=0.0, shock_frac=shock_frac,
                    shock_return=shock_return, bg_depth_btc=depth, bg_half_spread=0.0001,
                    bg_walkaway=walkaway)


def buy_ioc(account, qty, price):
    return OrderRequest(account, BUY, qty, price=price, tif="IOC")


class LeverageTests(unittest.TestCase):
    def test_order_is_clipped_to_the_leverage_cap(self):
        cfg = SimConfig(n_steps=20, max_leverage=5.0)
        ex = Exchange(cfg, quiet_scenario())
        ex.add_account("a")                                  # 10,000 cash, 5x cap -> about 0.5 BTC
        ex.step([buy_ioc("a", 10.0, 100_500.0)])
        position = ex.accounts["a"].position
        self.assertGreater(position, 0.4)
        self.assertLessEqual(position, 0.5 + 1e-9)

    def test_cannot_exceed_cap_by_sending_orders_repeatedly(self):
        cfg = SimConfig(n_steps=20, max_leverage=5.0)
        ex = Exchange(cfg, quiet_scenario())
        ex.add_account("a")
        for _ in range(5):
            ex.step([buy_ioc("a", 10.0, 100_500.0)])
        acct = ex.accounts["a"]
        self.assertLessEqual(acct.notional(ex.mark_price()) / acct.equity(ex.mark_price()), 5.05)

    def test_reduce_only_cannot_open_or_grow_a_position(self):
        ex = Exchange(SimConfig(n_steps=20), quiet_scenario())
        ex.add_account("a")
        ex.step([OrderRequest("a", BUY, 1.0, price=100_500.0, tif="IOC", reduce_only=True)])
        self.assertEqual(ex.accounts["a"].position, 0.0)
        self.assertEqual(ex.records[-1].n_rejected_orders, 1)

    def test_reduce_only_closes_but_never_flips(self):
        ex = Exchange(SimConfig(n_steps=20), quiet_scenario())
        ex.add_account("a")
        ex.step([buy_ioc("a", 1.0, 100_500.0)])
        ex.step([OrderRequest("a", SELL, 5.0, price=None, reduce_only=True)])
        self.assertAlmostEqual(ex.accounts["a"].position, 0.0, places=9)

    def test_trades_are_zero_sum_across_accounts(self):
        ex = Exchange(SimConfig(n_steps=20), quiet_scenario())
        for name in ("a", "b"):
            ex.add_account(name)
        ex.step([buy_ioc("a", 1.0, 100_500.0), OrderRequest("b", SELL, 0.5, price=99_500.0, tif="IOC")])
        self.assertAlmostEqual(sum(a.position for a in ex.accounts.values()), 0.0, places=9)


class FundingTests(unittest.TestCase):
    def setUp(self):
        self.ex = Exchange(SimConfig(n_steps=120), quiet_scenario())
        for name in ("long", "short"):
            self.ex.add_account(name)
        self.ex.accounts["long"].apply_fill(BUY, 1.0, 100_000.0, 0.0)
        self.ex.accounts["short"].apply_fill(SELL, 1.0, 100_000.0, 0.0)

    def test_longs_pay_shorts_when_exchange_price_is_above_reference(self):
        self.ex.premium_buffer = [0.01] * 5
        cash_before = {k: a.collateral for k, a in self.ex.accounts.items()}
        self.ex._settle_funding()
        rate = self.ex.last_funding_rate
        self.assertGreater(rate, 0)
        self.assertLess(self.ex.accounts["long"].collateral, cash_before["long"])
        self.assertGreater(self.ex.accounts["short"].collateral, cash_before["short"])

    def test_shorts_pay_longs_when_exchange_price_is_below_reference(self):
        self.ex.premium_buffer = [-0.05] * 5
        cash_before = {k: a.collateral for k, a in self.ex.accounts.items()}
        self.ex._settle_funding()
        self.assertLess(self.ex.last_funding_rate, 0)
        self.assertGreater(self.ex.accounts["long"].collateral, cash_before["long"])
        self.assertLess(self.ex.accounts["short"].collateral, cash_before["short"])

    def test_funding_is_zero_sum(self):
        self.ex.premium_buffer = [0.02] * 5
        total_before = sum(a.collateral for a in self.ex.accounts.values())
        self.ex._settle_funding()
        total_after = sum(a.collateral for a in self.ex.accounts.values())
        self.assertAlmostEqual(total_before, total_after, places=3)

    def test_payment_matches_the_formula(self):
        self.ex.premium_buffer = [0.01] * 5
        mark = self.ex.mark_price()
        before = self.ex.accounts["long"].collateral
        self.ex._settle_funding()
        rate = 0.01 * (1 / 8) + 0.0000125
        self.assertAlmostEqual(before - self.ex.accounts["long"].collateral, mark * rate * 1.0, places=4)

    def test_funding_is_capped(self):
        self.ex.premium_buffer = [5.0] * 5
        self.ex._settle_funding()
        self.assertAlmostEqual(self.ex.last_funding_rate, SimConfig().funding_cap_per_hour)

    def test_settles_once_an_hour_at_one_minute_steps(self):
        ex = Exchange(SimConfig(n_steps=130, step_minutes=1.0), quiet_scenario(shock_frac=None))
        settled = [r.step for r in (ex.step([]) for _ in range(130)) if r.funding_settled]
        self.assertEqual(settled, [60, 120])

    def test_timing_follows_the_step_length(self):
        ex = Exchange(SimConfig(n_steps=30, step_minutes=5.0), quiet_scenario(shock_frac=None))
        settled = [r.step for r in (ex.step([]) for _ in range(30)) if r.funding_settled]
        self.assertEqual(settled, [12, 24])               # 12 steps x 5 min = 1 hour


class LiquidationTests(unittest.TestCase):
    def run_shock(self, leverage_qty, shock=-0.10, thin_after_open=None):
        # pin the dial this test depends on: the exchange price must catch up
        # with the reference price quickly, whatever the global default is
        cfg = SimConfig(n_steps=20, bg_anchor_strength=0.5)
        ex = Exchange(cfg, quiet_scenario(shock_return=shock, shock_frac=0.5))
        ex.add_account("a")
        ex.step([buy_ioc("a", leverage_qty, 100_500.0)])      # step 1: open the position
        if thin_after_open is not None:                       # then let the market thin out
            ex.scenario = dataclasses.replace(ex.scenario, bg_depth_btc=thin_after_open)
        for _ in range(19):
            ex.step([])
        return ex

    def test_a_leveraged_long_is_force_closed_after_a_shock(self):
        ex = self.run_shock(1.5)                              # about 15x
        events = [e for e in ex.liquidation_log if e.account == "a"]
        self.assertTrue(events, "expected a liquidation")
        self.assertEqual(events[0].side_closed, "long")
        self.assertGreaterEqual(events[0].step, 10)           # not before the shock
        self.assertAlmostEqual(ex.accounts["a"].position, 0.0, places=6)

    def test_a_cautious_account_survives_the_same_shock(self):
        ex = self.run_shock(0.3)                              # about 3x
        self.assertEqual([e for e in ex.liquidation_log if e.account == "a"], [])
        self.assertGreater(ex.accounts["a"].position, 0.29)

    def test_liquidation_is_visible_in_the_public_records(self):
        ex = self.run_shock(1.5)
        self.assertGreater(sum(r.liq_count for r in ex.records), 0)
        self.assertGreater(sum(r.liq_qty_long for r in ex.records), 0)
        self.assertEqual(sum(r.liq_qty_short for r in ex.records), 0)

    def test_unfilled_liquidation_is_retried_not_forgotten(self):
        # almost no background liquidity: the forced sale cannot fully fill at once
        ex = self.run_shock(1.5, thin_after_open=0.2)
        events = [e for e in ex.liquidation_log if e.account == "a"]
        self.assertTrue(events)
        self.assertLess(events[0].qty_filled, events[0].qty_requested)
        self.assertGreater(len(events), 1)

    def test_bad_debt_is_recorded_when_losses_exceed_cash(self):
        ex = self.run_shock(1.9, shock=-0.5)
        self.assertGreaterEqual(ex.bad_debt, 0.0)
        self.assertGreaterEqual(ex.accounts["a"].collateral, 0.0)


class ReproducibilityTests(unittest.TestCase):
    def run_once(self, seed, extra_idle_accounts=0):
        cfg = SimConfig(n_steps=40, seed=seed)
        ex = Exchange(cfg, SCENARIOS["stress-thin-early"])
        ex.add_account("a")
        for i in range(extra_idle_accounts):
            ex.add_account(f"idle{i}")
        for t in range(40):
            ex.step([buy_ioc("a", 0.2, ex.last_price * 1.002)] if t % 3 == 0 else [])
        return ex

    def test_same_seed_gives_identical_records(self):
        a, b = self.run_once(7), self.run_once(7)
        self.assertEqual([dataclasses.asdict(r) for r in a.records],
                         [dataclasses.asdict(r) for r in b.records])

    def test_different_seed_gives_a_different_path(self):
        a, b = self.run_once(7), self.run_once(8)
        self.assertNotEqual([r.index_price for r in a.records], [r.index_price for r in b.records])

    def test_reference_price_path_does_not_depend_on_who_trades(self):
        a, b = self.run_once(7, extra_idle_accounts=0), self.run_once(7, extra_idle_accounts=25)
        self.assertEqual([r.index_price for r in a.records], [r.index_price for r in b.records])


class ScenarioTests(unittest.TestCase):
    def test_there_are_eight_distinct_scenarios(self):
        self.assertEqual(len(SCENARIOS), 8)
        self.assertEqual(sum(1 for s in SCENARIOS.values() if s.regime == "normal"), 4)

    def test_shock_timing_scales_with_run_length(self):
        s = SCENARIOS["stress-thin-early"]
        self.assertEqual(s.shock_step(100), 35)
        self.assertEqual(s.shock_step(1000), 350)


if __name__ == "__main__":
    unittest.main()
