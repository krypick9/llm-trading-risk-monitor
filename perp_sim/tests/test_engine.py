"""Engine invariants ported back from the Phase-0 suite after review.

The minimal version kept 7 tests and dropped coverage of the order book's
lifecycle, the leverage clip, reduce-only, bad-debt accounting, the background
flow, and (because the feature was gone) funding. Those are back here.

The style is Phase-0's: a quiet, deterministic world with no random wiggles, so
a failure points at one mechanism rather than at the random seed.
"""

import unittest

from engine import (BG_ID, BUY, SELL, Account, Exchange, Order, OrderBook,
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
            self.assertEqual(a.orders_this_step(t, hist, 0.0026),
                             b.orders_this_step(t, hist, 0.0026))

    def test_flow_inventory_stays_bounded(self):
        cfg = SimConfig()
        flow = BackgroundTakerFlow(cfg, 2)
        hist = [100_000.0] * 10
        for t in range(1, 400):
            flow.orders_this_step(t, hist, 0.0026)
        self.assertLess(abs(flow.inventory_btc),
                        cfg.bg_flow_inventory_cap_btc + cfg.bg_flow_max_size_btc + 1e-9)

    def test_per_minute_volume_is_not_constant(self):
        """The core regression guard.

        The old flow drew heavy-tailed sizes and then rescaled them so each
        minute's total equalled bg_flow_btc_per_min, which pinned volume at
        0.170 BTC/min at BOTH 2.2 and 80 orders/min. A market whose volume has
        no variance has no volume/volatility relationship and gives a
        price-impact regression nothing to explain."""
        import statistics as stats
        from run import run as run_sim
        ex, _ = run_sim("normal", 600, seed=0)
        volumes = [r.volume for r in ex.records]
        mean = sum(volumes) / len(volumes)
        self.assertGreater(stats.pstdev(volumes) / mean, 0.5,
                           "per-minute volume is barely dispersed")

    def test_volume_responds_to_the_arrival_rate(self):
        """Under the old renormalisation, doubling the rate changed nothing."""
        cfg_low = SimConfig(bg_flow_btc_per_min=None, bg_flow_orders_per_min=4.0)
        cfg_high = SimConfig(bg_flow_btc_per_min=None, bg_flow_orders_per_min=16.0)
        hist = [100_000.0] * 10

        def volume(cfg):
            flow = BackgroundTakerFlow(cfg, 3)
            return sum(qty for t in range(1, 300)
                       for _, qty in flow.orders_this_step(t, hist, 0.0026))

        self.assertGreater(volume(cfg_high), 2.0 * volume(cfg_low))

    def test_the_arrival_rate_is_derived_from_the_volume_target(self):
        cfg = SimConfig()
        flow = BackgroundTakerFlow(cfg, 1)
        self.assertAlmostEqual(flow.mean_size_btc * flow.orders_per_min,
                               cfg.bg_flow_btc_per_min, places=3)

    def test_the_excitement_multiplier_is_mean_preserving(self):
        """Excitement must change WHEN volume arrives, not how much.

        Uncentred, its mean was 1 + 2.0*E|z| = 2.60 and measured volume came out
        2.57x the target. A calm index must therefore draw LESS than the base
        rate, not the same."""
        cfg = SimConfig()
        flow = BackgroundTakerFlow(cfg, 5)
        calm = [100_000.0] * 10            # no move at all -> excite = 1 < mean
        wild = [100_000.0 * (1 + 0.01 * i) for i in range(10)]

        def volume(history):
            f = BackgroundTakerFlow(cfg, 5)
            return sum(qty for t in range(1, 400)
                       for _, qty in f.orders_this_step(t, history, 0.0026))

        base = cfg.bg_flow_btc_per_min * 399
        self.assertLess(volume(calm), base, "a calm market must draw below the base rate")
        self.assertGreater(volume(wild), volume(calm))

    def test_no_order_is_below_the_lot_size(self):
        """The renormalisation pushed ~73% of draws below the lot at 80/min,
        where they were silently dropped."""
        cfg = SimConfig()
        flow = BackgroundTakerFlow(cfg, 9)
        hist = [100_000.0] * 10
        sizes = [qty for t in range(1, 300)
                 for _, qty in flow.orders_this_step(t, hist, 0.0026)]
        self.assertTrue(sizes)
        self.assertTrue(all(q >= cfg.lot_size - 1e-12 for q in sizes))

    def test_volume_and_absolute_return_move_together(self):
        """A basic stylized fact the constant-volume flow could not show."""
        from run import run as run_sim
        ex, _ = run_sim("normal", 600, seed=0)
        xs = [r.volume for r in ex.records]
        ys = [abs(r.price_return) for r in ex.records]
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        cov = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
        sx = sum((x - mx) ** 2 for x in xs) ** 0.5
        sy = sum((y - my) ** 2 for y in ys) ** 0.5
        self.assertGreater(cov / (sx * sy), 0.2)

    def test_signed_volume_explains_part_of_the_return(self):
        """Price-impact R^2. Phase-0 validation measured 0.04 in the simulator
        against 0.36 in real data; with volume constant by construction that was
        close to unavoidable. Guards the improvement, not the real value."""
        from run import run as run_sim
        ex, _ = run_sim("normal", 600, seed=0)
        xs = [r.signed_volume for r in ex.records]
        ys = [r.price_return for r in ex.records]
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        slope = sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / sxx
        intercept = my - slope * mx
        ss_res = sum((ys[i] - (intercept + slope * xs[i])) ** 2 for i in range(n))
        ss_tot = sum((y - my) ** 2 for y in ys)
        self.assertGreater(1 - ss_res / ss_tot, 0.05)

    def test_flow_makes_the_market_trade(self):
        ex = Exchange(SimConfig(n_steps=30), get_scenario("normal"))
        for _ in range(30):
            ex.step([])
        self.assertGreater(sum(r.n_trades for r in ex.records), 0)
        flat = sum(1 for r in ex.records if r.price_return == 0.0)
        self.assertLess(flat / len(ex.records), 0.5)


# ---- the LP ladder's shape ----------------------------------------------
class LadderShapeTests(unittest.TestCase):
    """The ladder's price REACH decides whether a cascade is representable.

    Phase 0 and the first minimal version quoted 5 flat levels 0.0002 apart, so
    the whole book spanned 0.08% and forced selling produced 0.00 pp of
    endogenous amplification -- the price could not fall further than the book
    reached. These tests guard the fix.
    """

    def _exchange(self, **kw):
        return Exchange(SimConfig(n_steps=5, **kw), get_scenario("normal"))

    def test_the_ladder_reaches_percent_scale(self):
        ladder = self._exchange()._bg_ladder(4.4e-07, 0.5)
        reach = ladder[-1][0]
        self.assertGreater(reach, 0.01, "the ladder must reach percent scale")
        # the old uniform ladder reached 0.08%; anything near that cannot host
        # a cascade at all
        self.assertGreater(reach, 10 * 0.0008)

    def test_near_depth_does_not_depend_on_the_level_count(self):
        """Reach and near depth must be settable independently. Phase 0 divided
        one total by the level count, so 'more levels' silently meant
        'uniformly thinner' and the two dials were tangled."""
        band = SimConfig().depth_band
        for levels in (6, 20, 40):
            ladder = self._exchange(bg_levels=levels)._bg_ladder(4.4e-07, 0.5)
            in_band = sum(size for offset, size in ladder if offset <= band)
            self.assertAlmostEqual(in_band, 0.5, places=6,
                                   msg=f"near depth moved at bg_levels={levels}")

    def test_size_decays_away_from_the_touch(self):
        ladder = self._exchange()._bg_ladder(4.4e-07, 0.5)
        sizes = [size for _, size in ladder]
        self.assertTrue(all(a >= b for a, b in zip(sizes, sizes[1:])))
        self.assertLess(sizes[-1], sizes[0])

    def test_offsets_are_strictly_increasing(self):
        offsets = [o for o, _ in self._exchange()._bg_ladder(4.4e-07, 0.5)]
        self.assertTrue(all(a < b for a, b in zip(offsets, offsets[1:])))


class LPInventoryTests(unittest.TestCase):
    """The LP must be exhaustible, or forced selling never runs out of bids."""

    def _exchange(self, **kw):
        return Exchange(SimConfig(n_steps=5, **kw), get_scenario("normal"))

    def test_no_limit_means_the_old_unlimited_behaviour(self):
        ex = self._exchange(bg_inventory_limit_btc=None)
        ex.accounts[BG_ID].position = 500.0
        self.assertEqual(ex._bg_inventory_scales(), (1.0, 1.0))

    def test_a_long_lp_shows_fewer_bids_and_unchanged_offers(self):
        ex = self._exchange(bg_inventory_limit_btc=10.0)
        ex.accounts[BG_ID].position = 5.0
        bid, ask = ex._bg_inventory_scales()
        self.assertAlmostEqual(bid, 0.5)
        self.assertAlmostEqual(ask, 1.0)

    def test_at_the_limit_the_lp_stops_quoting_that_side(self):
        ex = self._exchange(bg_inventory_limit_btc=10.0)
        ex.accounts[BG_ID].position = 10.0
        self.assertEqual(ex._bg_inventory_scales()[0], 0.0)      # no bids left
        ex.accounts[BG_ID].position = -20.0                      # beyond, short
        self.assertEqual(ex._bg_inventory_scales()[1], 0.0)      # no offers left

    def test_the_limit_must_exceed_what_ordinary_flow_pushes_onto_the_lp(self):
        """Regression guard. The LP is the background flow's counterparty, so it
        routinely carries up to bg_flow_inventory_cap_btc with nothing wrong. A
        limit at or below that made it stop quoting in CALM markets: one-sided
        book 9% of steps, median spread ~1000x real, local volatility ~11x."""
        cfg = SimConfig()
        self.assertIsNotNone(cfg.bg_inventory_limit_btc)
        self.assertGreater(cfg.bg_inventory_limit_btc, 2 * cfg.bg_flow_inventory_cap_btc)


class MakerAgentTests(unittest.TestCase):
    """Finite, strategic liquidity -- the thing one unbounded LP could not be."""

    def _maker(self, **kw):
        from agents import MakerAgent
        return MakerAgent("m00", 1, **kw)

    def _view(self, maker, prices, position=0.0, equity=1000.0):
        from tests.test_population import _fake_view_sequence
        from engine import OwnView
        views = _fake_view_sequence(maker.account_id, n=len(prices))
        view = views[-1]
        view.price_history = list(prices)
        view.last_price = view.mid = view.index_price = view.mark_price = prices[-1]
        view.own = OwnView(account_id=maker.account_id, collateral=equity, equity=equity,
                           position=position, entry_price=prices[-1], leverage=0.0,
                           margin_buffer=None, liquidation_price=None, open_orders=0)
        return view

    def test_quotes_sit_a_tick_or_two_either_side(self):
        from engine import OrderRequest
        maker = self._maker(half_spread_ticks=1.5)
        flat = [100_000.0] * 10
        orders = [a for a in maker.act(self._view(maker, flat)) if isinstance(a, OrderRequest)]
        mid = 100_000.0
        for order in orders:
            offset_ticks = abs(order.price - mid) / 0.1
            self.assertGreater(offset_ticks, 0.5)
            self.assertLess(offset_ticks, 6.0)

    def test_withdraws_entirely_on_a_big_move(self):
        from engine import OrderRequest
        maker = self._maker(vol_withdraw=0.002)
        crash = [100_000.0 * (1 - 0.004 * i) for i in range(10)]   # ~1.6% over 5 steps
        actions = maker.act(self._view(maker, crash))
        self.assertEqual([a for a in actions if isinstance(a, OrderRequest)], [],
                         "a maker must be able to leave the market")

    def test_widens_before_it_leaves(self):
        from engine import OrderRequest
        maker = self._maker(vol_withdraw=0.002, widen_factor=3.0)
        flat = [100_000.0] * 10
        mild = [100_000.0 * (1 - 0.0005 * i) for i in range(10)]   # between 1x and 2x
        calm = [a for a in maker.act(self._view(maker, flat)) if isinstance(a, OrderRequest)]
        wide = [a for a in maker.act(self._view(maker, mild)) if isinstance(a, OrderRequest)]
        self.assertTrue(calm and wide)
        self.assertGreater(abs(wide[0].price - mild[-1]), abs(calm[0].price - flat[-1]))

    def test_stops_quoting_the_side_that_would_breach_its_inventory(self):
        from engine import OrderRequest
        maker = self._maker(inventory_limit=0.02)
        flat = [100_000.0] * 10
        long_full = [a for a in maker.act(self._view(maker, flat, position=0.02))
                     if isinstance(a, OrderRequest)]
        self.assertTrue(all(a.side == SELL for a in long_full))
        short_full = [a for a in maker.act(self._view(maker, flat, position=-0.02))
                      if isinstance(a, OrderRequest)]
        self.assertTrue(all(a.side == BUY for a in short_full))

    def test_skews_away_from_inventory_before_the_limit(self):
        from engine import OrderRequest
        maker = self._maker(inventory_limit=0.02, skew=0.6)
        flat = [100_000.0] * 10
        orders = {a.side: a for a in maker.act(self._view(maker, flat, position=0.01))
                  if isinstance(a, OrderRequest)}
        mid = 100_000.0
        self.assertGreater(mid - orders[BUY].price, orders[SELL].price - mid,
                           "a long maker should bid further away than it offers")

    def test_a_wiped_out_maker_stops_quoting(self):
        from engine import OrderRequest
        maker = self._maker()
        flat = [100_000.0] * 10
        actions = maker.act(self._view(maker, flat, equity=0.0))
        self.assertEqual([a for a in actions if isinstance(a, OrderRequest)], [])

    def test_the_lp_stands_behind_the_participant_quotes(self):
        """bg_backstop_only: the touch must belong to the makers."""
        cfg = SimConfig(n_steps=5, bg_backstop_only=True)
        ex = Exchange(cfg, get_scenario("normal"))
        ex.add_account("mk")
        tight = ex.book.round_price(ex.last_price * (1 - 1e-6))
        ex.book.submit(Order(ex._next_oid(), "mk", BUY, 0.01, tight, "GTC"))
        ex.book.submit(Order(ex._next_oid(), "mk", SELL, 0.01,
                             ex.book.round_price(ex.last_price * (1 + 1e-6)), "GTC"))
        before = ex.book.best_bid()
        ex._requote_background(1, _Stats_stub())
        self.assertLessEqual(ex.book.best_bid(), before + 1e-9,
                             "the LP must not outbid the participant makers")


class _Stats_stub:
    def __init__(self):
        self.volume = self.signed = 0.0
        self.n_trades = self.n_orders = self.n_buys = self.n_sells = self.n_rejected = 0


class CascadeAmplificationTests(unittest.TestCase):
    def test_forced_selling_now_deepens_the_dip(self):
        """The whole point of the ladder fix: the same run with liquidations on
        must push the exchange price further below the index than with them off.

        Before the fix this difference was 0.00 pp at every setting tried. It is
        now negative on every seed checked (-0.34 to -2.38 pp over seeds 0-5);
        seed 0 is asserted with a margin well inside that range."""
        from population import PopulationSpec as Spec
        from run import run as run_sim

        def dip(liquidations):
            ex, _ = run_sim("stress", 600, seed=0,
                            spec=Spec(target_leverage=20.0, long_bias=0.7),
                            cfg_overrides={"liquidations_enabled": liquidations})
            return 100 * min((r.last_price - r.index_price) / r.index_price
                             for r in ex.records)

        on, off = dip(True), dip(False)
        self.assertLess(on, off - 0.2,
                        f"forced selling did not move the price: {on:.2f}% vs {off:.2f}%")


# ---- trader behaviour and the leverage response --------------------------
class RebalanceModeTests(unittest.TestCase):
    """Whether a trader can be liquidated at moderate leverage is decided by what
    it does AFTER building its position, not by the leverage number."""

    def _drive(self, trader, prices):
        """Run a trader through a falling market and return its positions."""
        from tests.test_population import _fake_view_sequence
        from engine import OwnView

        views = _fake_view_sequence(trader.account_id, n=len(prices))
        positions, position, collateral, entry = [], 0.0, 1000.0, 0.0
        for view, price in zip(views, prices):
            view.index_price = view.last_price = view.mark_price = price
            view.best_bid, view.best_ask, view.mid = price - 0.1, price + 0.1, price
            equity = collateral + (position * (price - entry) if position else 0.0)
            view.own = OwnView(account_id=trader.account_id, collateral=collateral,
                               equity=equity, position=position, entry_price=entry,
                               leverage=(abs(position) * price / equity) if equity > 0 else 0.0,
                               margin_buffer=None, liquidation_price=None, open_orders=0)
            for action in trader.act(view):
                filled = action.side * action.qty
                if position == 0:
                    entry = price
                position += filled
            positions.append(position)
        return positions

    def test_hold_mode_does_not_sell_into_a_loss(self):
        """The fix. A held position keeps its size as the price falls, so
        leverage RISES and the trader walks into its own liquidation."""
        from agents import PositionTaker
        trader = PositionTaker("t00", 1, target_leverage=10.0, act_prob=1.0,
                               direction=1, rebalance="hold")
        falling = [100_000.0 * (1 - 0.0008 * i) for i in range(60)]
        positions = self._drive(trader, falling)
        peak = max(positions)
        self.assertGreater(peak, 0.0, "the trader never built a position")
        self.assertAlmostEqual(positions[-1], peak, places=6,
                               msg="a holding trader must not shrink its position")

    def test_constant_leverage_mode_sells_into_a_loss(self):
        """The old behaviour, kept as an option and documented. It is a
        volatility-targeting strategy: it self-deleverages, which is why it gave
        0 liquidations at 2x, 5x and 10x."""
        from agents import PositionTaker
        trader = PositionTaker("t00", 1, target_leverage=10.0, act_prob=1.0,
                               direction=1, rebalance="constant_leverage")
        falling = [100_000.0 * (1 - 0.0008 * i) for i in range(60)]
        positions = self._drive(trader, falling)
        self.assertLess(positions[-1], max(positions),
                        "constant-leverage traders must deleverage as they lose")

    def test_an_unknown_rebalance_mode_is_rejected(self):
        from agents import PositionTaker
        with self.assertRaises(ValueError):
            PositionTaker("t00", 1, rebalance="whatever")


class LeverageDispersionTests(unittest.TestCase):
    """A crowd on one leverage has one liquidation threshold, so the risk metric
    can only answer all-or-nothing."""

    def test_leverage_is_dispersed_by_default(self):
        from population import PopulationSpec, make_slots
        levs = [s.leverage for s in make_slots(0, PopulationSpec(target_leverage=5.0))]
        self.assertGreater(len(set(levs)), 10, "leverage must vary across the crowd")
        self.assertGreater(max(levs), 2 * min(levs))

    def test_zero_sigma_reproduces_a_uniform_crowd(self):
        from population import PopulationSpec, make_slots
        spec = PopulationSpec(target_leverage=7.0, leverage_sigma=0.0)
        self.assertEqual({s.leverage for s in make_slots(0, spec)}, {7.0})

    def test_drawn_leverage_stays_inside_the_allowed_range(self):
        from population import PopulationSpec, make_slots
        spec = PopulationSpec(target_leverage=15.0, leverage_sigma=1.5,
                              leverage_min=1.0, leverage_max=20.0)
        for slot in make_slots(0, spec):
            self.assertGreaterEqual(slot.leverage, 1.0)
            self.assertLessEqual(slot.leverage, 20.0)

    def test_the_default_cap_does_not_exceed_the_exchange_cap(self):
        from population import PopulationSpec
        self.assertLessEqual(PopulationSpec().leverage_max, SimConfig().max_leverage)

    def test_raising_median_leverage_liquidates_more_accounts(self):
        """The power check as an assertion: the risk metric must respond at
        moderate leverage, not only at the extreme. Before this commit the
        distinct-accounts count was 0 at 5x AND at 10x."""
        from population import PopulationSpec
        from run import run as run_sim

        def accounts(lev):
            ex, _ = run_sim("stress", 600, seed=0,
                            spec=PopulationSpec(target_leverage=float(lev), long_bias=0.7))
            return len({e.account for e in ex.liquidation_log})

        low, high = accounts(5), accounts(10)
        self.assertGreater(high, low)
        self.assertGreater(high, 0)


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
