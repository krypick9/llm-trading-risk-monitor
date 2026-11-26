"""Engine invariants ported back from the Phase-0 suite after review.

The minimal version kept 7 tests and dropped coverage of the order book's
lifecycle, the leverage clip, reduce-only, bad-debt accounting, the background
flow, and (because the feature was gone) funding. Those are back here.

The style is Phase-0's: a quiet, deterministic world with no random wiggles, so
a failure points at one mechanism rather than at the random seed.
"""

import unittest

from engine import (BUY, SELL, Account, Exchange, Order, OrderBook,
                    OrderRequest, Scenario, SimConfig, get_scenario)
from engine.flow import BackgroundTakerFlow


def quiet_scenario(shock_return=-0.10, shock_frac=0.5, walkaway=False, depth=12.0):
    """No volatility, no jumps, no clustering -- just one scheduled shock."""
    return Scenario(name="test", regime="stress", sigma_hourly=0.0,
                    jump_rate_per_hour=0.0, jump_mean=0.0, jump_std=0.0,
                    bg_depth_btc=depth, bg_half_spread=0.0001,
                    bg_walkaway=walkaway, shock_frac=shock_frac,
                    shock_return=shock_return)


def buy_ioc(account, qty, price):
    return OrderRequest(account, BUY, qty, price=price, tif="IOC")


# ---- order book lifecycle ------------------------------------------------
class OrderBookTests(unittest.TestCase):
    def setUp(self):
        self.book = OrderBook(tick_size=0.1)

    def test_earlier_order_goes_first_at_the_same_price(self):
        self.book.submit(Order(1, "first", SELL, 1.0, 100.0))
        self.book.submit(Order(2, "second", SELL, 1.0, 100.0))
        fills = self.book.submit(Order(3, "taker", BUY, 1.0, 100.0))
        self.assertEqual(fills[0].maker_account, "first")

    def test_partial_fill_rests_the_remainder(self):
        self.book.submit(Order(1, "mk", SELL, 0.4, 100.0))
        self.book.submit(Order(2, "tk", BUY, 1.0, 100.0))
        self.assertEqual(self.book.best_bid(), 100.0)
        self.assertAlmostEqual(self.book.top_size(BUY), 0.6)

    def test_ioc_remainder_vanishes(self):
        self.book.submit(Order(1, "mk", SELL, 0.4, 100.0))
        self.book.submit(Order(2, "tk", BUY, 1.0, 100.0, tif="IOC"))
        self.assertIsNone(self.book.best_bid())

    def test_market_order_walks_the_book(self):
        self.book.submit(Order(1, "mk", SELL, 0.5, 100.0))
        self.book.submit(Order(2, "mk", SELL, 0.5, 101.0))
        fills = self.book.submit(Order(3, "tk", BUY, 1.0, None, tif="IOC"))
        self.assertEqual([f.price for f in fills], [100.0, 101.0])

    def test_no_trade_when_prices_do_not_cross(self):
        self.book.submit(Order(1, "mk", SELL, 1.0, 101.0))
        self.assertEqual(self.book.submit(Order(2, "tk", BUY, 1.0, 100.0)), [])

    def test_expiry_removes_stale_orders(self):
        self.book.submit(Order(1, "mk", SELL, 1.0, 100.0, expires=5))
        self.book.expire(4)
        self.assertEqual(self.book.best_ask(), 100.0)
        self.book.expire(5)
        self.assertIsNone(self.book.best_ask())

    def test_cancel_account_clears_only_that_account(self):
        self.book.submit(Order(1, "a", SELL, 1.0, 101.0))
        self.book.submit(Order(2, "b", SELL, 1.0, 102.0))
        self.assertEqual(self.book.cancel_account("a"), 1)
        self.assertEqual(self.book.best_ask(), 102.0)

    def test_depth_only_counts_inside_the_band(self):
        self.book.submit(Order(1, "mk", BUY, 1.0, 99_990.0))    # 0.01% inside
        self.book.submit(Order(2, "mk", BUY, 2.0, 99_000.0))    # 1% away, outside
        self.book.submit(Order(3, "mk", SELL, 1.0, 100_010.0))
        bid, ask = self.book.depth(0.001)                        # +-0.10%
        self.assertAlmostEqual(bid, 1.0)
        self.assertAlmostEqual(ask, 1.0)


# ---- accounts and margin -------------------------------------------------
class AccountTests(unittest.TestCase):
    def test_adding_blends_the_entry_price(self):
        a = Account("x", 10_000.0, 20.0)
        a.apply_fill(BUY, 1.0, 100.0, 0.0)
        a.apply_fill(BUY, 1.0, 200.0, 0.0)
        self.assertAlmostEqual(a.entry_price, 150.0)

    def test_reducing_realises_profit(self):
        a = Account("x", 10_000.0, 20.0)
        a.apply_fill(BUY, 1.0, 100.0, 0.0)
        a.apply_fill(SELL, 0.5, 120.0, 0.0)
        self.assertAlmostEqual(a.realized_pnl, 10.0)
        self.assertAlmostEqual(a.position, 0.5)

    def test_short_profits_when_the_price_falls(self):
        a = Account("x", 10_000.0, 20.0)
        a.apply_fill(SELL, 1.0, 100.0, 0.0)
        self.assertAlmostEqual(a.unrealized_pnl(90.0), 10.0)

    def test_flipping_side_resets_entry_to_the_fill_price(self):
        a = Account("x", 10_000.0, 20.0)
        a.apply_fill(BUY, 1.0, 100.0, 0.0)
        a.apply_fill(SELL, 2.0, 110.0, 0.0)
        self.assertAlmostEqual(a.position, -1.0)
        self.assertAlmostEqual(a.entry_price, 110.0)

    def test_fees_come_out_of_cash(self):
        a = Account("x", 1_000.0, 20.0)
        a.apply_fill(BUY, 1.0, 100.0, 0.45)
        self.assertAlmostEqual(a.collateral, 999.55)

    def test_higher_leverage_breaks_on_a_smaller_move(self):
        low, high = Account("l", 1_000.0, 20.0), Account("h", 1_000.0, 20.0)
        low.apply_fill(BUY, 0.05, 100_000.0, 0.0)     # 5x
        high.apply_fill(BUY, 0.20, 100_000.0, 0.0)    # 20x
        self.assertGreater(low.liquidation_price(0.01), 0.0)
        self.assertLess(low.liquidation_price(0.01), high.liquidation_price(0.01))


# ---- leverage clip and reduce-only ---------------------------------------
class OrderGuardTests(unittest.TestCase):
    def _exchange(self, **kw):
        cfg = SimConfig(n_steps=20, max_leverage=5.0, initial_collateral=10_000.0, **kw)
        ex = Exchange(cfg, quiet_scenario(shock_frac=None))
        ex.add_account("a")
        return ex

    def test_order_is_clipped_to_the_leverage_cap(self):
        ex = self._exchange()
        ex.step([buy_ioc("a", 100.0, ex.last_price * 1.05)])
        cap = 5.0 * 10_000.0 / ex.mark_price()
        self.assertLessEqual(ex.accounts["a"].position, cap + 1e-6)

    def test_cap_holds_across_repeated_orders(self):
        ex = self._exchange()
        for _ in range(6):
            ex.step([buy_ioc("a", 10.0, ex.last_price * 1.05)])
        acct = ex.accounts["a"]
        cap = 5.0 * acct.equity(ex.mark_price()) / ex.mark_price()
        self.assertLessEqual(acct.position, cap + 1e-6)

    def test_reduce_only_cannot_open_a_position(self):
        ex = self._exchange()
        ex.step([OrderRequest("a", BUY, 0.1, price=ex.last_price * 1.05, reduce_only=True)])
        self.assertEqual(ex.accounts["a"].position, 0.0)
        self.assertGreater(ex.records[-1].n_rejected_orders, 0)

    def test_reduce_only_closes_but_never_flips(self):
        ex = self._exchange()
        ex.step([buy_ioc("a", 0.1, ex.last_price * 1.05)])
        held = ex.accounts["a"].position
        self.assertGreater(held, 0.0)
        ex.step([OrderRequest("a", SELL, held * 10, price=ex.last_price * 0.95,
                              reduce_only=True, tif="IOC")])
        self.assertGreaterEqual(ex.accounts["a"].position, 0.0)

    def test_trades_are_zero_sum_in_position(self):
        ex = self._exchange()
        ex.add_account("b")
        for _ in range(5):
            ex.step([buy_ioc("a", 0.05, ex.last_price * 1.05),
                     OrderRequest("b", SELL, 0.05, price=ex.last_price * 0.95, tif="IOC")])
        self.assertAlmostEqual(sum(a.position for a in ex.accounts.values()), 0.0, places=6)


# ---- funding (restored after review) -------------------------------------
class FundingTests(unittest.TestCase):
    def setUp(self):
        self.ex = Exchange(SimConfig(n_steps=120), quiet_scenario())
        for name in ("long", "short"):
            self.ex.add_account(name)
        self.ex.accounts["long"].apply_fill(BUY, 1.0, 100_000.0, 0.0)
        self.ex.accounts["short"].apply_fill(SELL, 1.0, 100_000.0, 0.0)

    def test_longs_pay_shorts_when_the_exchange_price_is_above_the_index(self):
        self.ex.premium_buffer = [0.01] * 5
        before = {k: a.collateral for k, a in self.ex.accounts.items()}
        self.ex._settle_funding()
        self.assertGreater(self.ex.last_funding_rate, 0)
        self.assertLess(self.ex.accounts["long"].collateral, before["long"])
        self.assertGreater(self.ex.accounts["short"].collateral, before["short"])

    def test_shorts_pay_longs_when_the_exchange_price_is_below_the_index(self):
        self.ex.premium_buffer = [-0.05] * 5
        before = {k: a.collateral for k, a in self.ex.accounts.items()}
        self.ex._settle_funding()
        self.assertLess(self.ex.last_funding_rate, 0)
        self.assertGreater(self.ex.accounts["long"].collateral, before["long"])
        self.assertLess(self.ex.accounts["short"].collateral, before["short"])

    def test_funding_is_zero_sum(self):
        self.ex.premium_buffer = [0.02] * 5
        before = sum(a.collateral for a in self.ex.accounts.values())
        self.ex._settle_funding()
        self.assertAlmostEqual(before, sum(a.collateral for a in self.ex.accounts.values()),
                               places=3)

    def test_payment_matches_the_formula(self):
        cfg = SimConfig(n_steps=120, funding_scale=1 / 8,
                        funding_interest_per_hour=0.0000125, funding_cap_per_hour=0.04)
        ex = Exchange(cfg, quiet_scenario())
        ex.add_account("long")
        ex.accounts["long"].apply_fill(BUY, 1.0, 100_000.0, 0.0)
        ex.premium_buffer = [0.01] * 5
        mark, before = ex.mark_price(), ex.accounts["long"].collateral
        ex._settle_funding()
        rate = 0.01 * (1 / 8) + 0.0000125
        self.assertAlmostEqual(before - ex.accounts["long"].collateral,
                               mark * rate * 1.0, places=4)

    def test_funding_is_capped_both_ways(self):
        cap = SimConfig().funding_cap_per_hour
        self.ex.premium_buffer = [5.0] * 5
        self.ex._settle_funding()
        self.assertAlmostEqual(self.ex.last_funding_rate, cap)
        self.ex.premium_buffer = [-5.0] * 5
        self.ex._settle_funding()
        self.assertAlmostEqual(self.ex.last_funding_rate, -cap)

    def test_the_cap_is_the_real_venue_order_of_magnitude(self):
        """Phase-0 shipped 1.25e-5/hour, about 1/20 of a real venue's 0.75%
        per 8 hours. That is why funding looked irrelevant. Guard the fix."""
        self.assertAlmostEqual(SimConfig().funding_cap_per_hour, 0.0075 / 8, places=9)

    def test_settles_once_an_hour_at_one_minute_steps(self):
        ex = Exchange(SimConfig(n_steps=130), quiet_scenario(shock_frac=None))
        settled = [r.step for r in (ex.step([]) for _ in range(130)) if r.funding_settled]
        self.assertEqual(settled, [60, 120])

    def test_timing_follows_the_step_length(self):
        ex = Exchange(SimConfig(n_steps=30, step_minutes=5.0),
                      quiet_scenario(shock_frac=None))
        settled = [r.step for r in (ex.step([]) for _ in range(30)) if r.funding_settled]
        self.assertEqual(settled, [12, 24])          # 12 steps x 5 min = 1 hour

    def test_funding_can_be_switched_off_as_a_counterfactual(self):
        ex = Exchange(SimConfig(n_steps=130, funding_enabled=False),
                      quiet_scenario(shock_frac=None))
        settled = [r.step for r in (ex.step([]) for _ in range(130)) if r.funding_settled]
        self.assertEqual(settled, [])


# ---- liquidation and bad debt --------------------------------------------
class LiquidationTests(unittest.TestCase):
    def run_shock(self, leverage_btc, shock=-0.10, depth=12.0):
        cfg = SimConfig(n_steps=20, initial_collateral=10_000.0, max_leverage=50.0)
        ex = Exchange(cfg, quiet_scenario(shock_return=shock, shock_frac=0.5, depth=depth))
        ex.add_account("a")
        for _ in range(20):
            ex.step([buy_ioc("a", leverage_btc, ex.last_price * 1.05)]
                    if ex.accounts["a"].position < leverage_btc - 1e-9 else [])
        return ex

    def test_a_leveraged_long_is_force_closed_after_a_shock(self):
        ex = self.run_shock(1.9, shock=-0.30)
        self.assertGreater(len(ex.liquidation_log), 0)

    def test_a_cautious_account_survives_the_same_shock(self):
        ex = self.run_shock(0.02, shock=-0.30)
        self.assertEqual(len(ex.liquidation_log), 0)

    def test_liquidation_is_visible_in_the_public_records(self):
        ex = self.run_shock(1.9, shock=-0.30)
        self.assertTrue(any(r.liq_count > 0 for r in ex.records))
        self.assertTrue(any(r.liq_qty_long > 0 for r in ex.records))

    def test_bad_debt_is_recorded_and_never_leaves_cash_negative(self):
        ex = self.run_shock(1.9, shock=-0.5, depth=0.5)
        self.assertGreaterEqual(ex.bad_debt, 0.0)
        for a in ex.accounts.values():
            if not a.background and a.position == 0:
                self.assertGreaterEqual(a.collateral, 0.0)

    def test_liquidations_can_be_disabled_as_a_control(self):
        cfg = SimConfig(n_steps=20, initial_collateral=10_000.0, max_leverage=50.0,
                        liquidations_enabled=False)
        ex = Exchange(cfg, quiet_scenario(shock_return=-0.30))
        ex.add_account("a")
        for _ in range(20):
            ex.step([buy_ioc("a", 1.9, ex.last_price * 1.05)]
                    if ex.accounts["a"].position < 1.9 - 1e-9 else [])
        self.assertEqual(len(ex.liquidation_log), 0)


# ---- background taker flow -----------------------------------------------
class FlowTests(unittest.TestCase):
    def test_flow_is_deterministic_per_seed(self):
        a = BackgroundTakerFlow(SimConfig(), 11)
        b = BackgroundTakerFlow(SimConfig(), 11)
        hist = [100_000.0] * 10
        for t in range(1, 20):
            self.assertEqual(a.orders_this_step(t, hist, 0.0026, 0.5, 0.5),
                             b.orders_this_step(t, hist, 0.0026, 0.5, 0.5))

    def test_flow_inventory_stays_bounded(self):
        cfg = SimConfig()
        flow = BackgroundTakerFlow(cfg, 2)
        hist = [100_000.0] * 10
        for t in range(1, 400):
            flow.orders_this_step(t, hist, 0.0026, 0.5, 0.5)
        self.assertLess(abs(flow.inventory_btc),
                        cfg.bg_flow_inventory_cap_btc + cfg.bg_flow_max_size_btc + 1e-9)

    def test_flow_makes_the_market_trade(self):
        ex = Exchange(SimConfig(n_steps=30), get_scenario("normal"))
        for _ in range(30):
            ex.step([])
        self.assertGreater(sum(r.n_trades for r in ex.records), 0)
        flat = sum(1 for r in ex.records if r.price_return == 0.0)
        self.assertLess(flat / len(ex.records), 0.5)


# ---- reproducibility and the replay path ---------------------------------
class ReproducibilityTests(unittest.TestCase):
    def run_once(self, seed, idle=0):
        cfg = SimConfig(n_steps=40, seed=seed)
        ex = Exchange(cfg, get_scenario("normal"))
        ex.add_account("a")
        for i in range(idle):
            ex.add_account(f"idle{i}")
        for t in range(40):
            ex.step([buy_ioc("a", 0.02, ex.last_price * 1.002)] if t % 3 == 0 else [])
        return ex

    def test_same_seed_gives_identical_records(self):
        a, b = self.run_once(7), self.run_once(7)
        self.assertEqual([r.last_price for r in a.records],
                         [r.last_price for r in b.records])

    def test_different_seed_gives_a_different_path(self):
        self.assertNotEqual([r.index_price for r in self.run_once(7).records],
                            [r.index_price for r in self.run_once(8).records])

    def test_index_path_does_not_depend_on_who_trades(self):
        self.assertEqual([r.index_price for r in self.run_once(7).records],
                         [r.index_price for r in self.run_once(7, idle=20).records])


class ReplayTests(unittest.TestCase):
    def test_replay_follows_the_stored_path_and_ignores_scheduled_shocks(self):
        scenario = get_scenario("replay-extreme")
        self.assertTrue(scenario.use_replay)
        self.assertIsNone(scenario.shock_step(600))
        ex = Exchange(SimConfig(n_steps=50, seed=0), scenario)
        self.assertEqual(ex.index.prices[: 51][0], ex.cfg.initial_price)

    def test_replay_is_identical_for_every_seed(self):
        """Replay is data, not a random draw -- so a replay scenario needs more
        seeds than a synthetic one to say anything about the price path."""
        scenario = get_scenario("replay-extreme")
        a = Exchange(SimConfig(n_steps=50, seed=1), scenario)
        b = Exchange(SimConfig(n_steps=50, seed=2), scenario)
        self.assertEqual([a.index.next(t) for t in range(1, 50)],
                         [b.index.next(t) for t in range(1, 50)])

    def test_a_missing_regime_raises_instead_of_falling_back(self):
        """Phase-0 printed a warning and silently ran synthetic GBM instead."""
        from engine.index_price import ReplayIndexPrice
        import random
        bad = Scenario(name="bad", regime="stress", sigma_hourly=0.0043,
                       jump_rate_per_hour=0.0, jump_mean=0.0, jump_std=0.0,
                       bg_depth_btc=0.5, bg_half_spread=4.4e-07, bg_walkaway=True,
                       use_replay=True, replay_regime="no-such-regime")
        with self.assertRaises(KeyError):
            ReplayIndexPrice(SimConfig(n_steps=10), bad, random.Random(0))

    def test_a_run_longer_than_the_stored_path_raises(self):
        from engine.index_price import ReplayIndexPrice
        import random
        with self.assertRaises(ValueError):
            ReplayIndexPrice(SimConfig(n_steps=5_000),
                             get_scenario("replay-extreme"), random.Random(0))


if __name__ == "__main__":
    unittest.main()
