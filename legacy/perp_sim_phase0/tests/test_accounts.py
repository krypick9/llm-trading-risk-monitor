import unittest

from engine.accounts import Account
from engine.orderbook import BUY, SELL

MM = 0.025


def new_account(collateral=10_000.0):
    return Account("a", collateral, max_leverage=20.0)


class AccountTests(unittest.TestCase):
    def test_adding_blends_the_entry_price(self):
        a = new_account()
        a.apply_fill(BUY, 1.0, 100.0, 0.0)
        a.apply_fill(BUY, 1.0, 110.0, 0.0)
        self.assertAlmostEqual(a.position, 2.0)
        self.assertAlmostEqual(a.entry_price, 105.0)

    def test_reducing_realises_profit(self):
        a = new_account()
        a.apply_fill(BUY, 2.0, 100.0, 0.0)
        a.apply_fill(SELL, 1.0, 110.0, 0.0)
        self.assertAlmostEqual(a.position, 1.0)
        self.assertAlmostEqual(a.entry_price, 100.0)       # unchanged when reducing
        self.assertAlmostEqual(a.collateral, 10_010.0)

    def test_short_profits_when_price_falls(self):
        a = new_account()
        a.apply_fill(SELL, 1.0, 100.0, 0.0)
        a.apply_fill(BUY, 1.0, 90.0, 0.0)
        self.assertEqual(a.position, 0.0)
        self.assertAlmostEqual(a.collateral, 10_010.0)

    def test_flipping_side_resets_entry_to_the_fill_price(self):
        a = new_account()
        a.apply_fill(BUY, 1.0, 100.0, 0.0)
        a.apply_fill(SELL, 3.0, 120.0, 0.0)                # closes 1 (+20), opens short 2
        self.assertAlmostEqual(a.position, -2.0)
        self.assertAlmostEqual(a.entry_price, 120.0)
        self.assertAlmostEqual(a.collateral, 10_020.0)

    def test_fees_come_out_of_cash(self):
        a = new_account()
        a.apply_fill(BUY, 1.0, 100.0, 4.5)
        self.assertAlmostEqual(a.collateral, 10_000.0 - 4.5)

    def test_equity_follows_the_price(self):
        a = new_account()
        a.apply_fill(BUY, 1.0, 100.0, 0.0)
        self.assertAlmostEqual(a.equity(90.0), 9_990.0)

    def test_liquidation_price_is_where_equity_meets_the_requirement(self):
        for side, qty in ((BUY, 1.5), (SELL, 1.5)):
            a = new_account()
            a.apply_fill(side, qty, 100_000.0, 0.0)
            p = a.liquidation_price(MM)
            self.assertIsNotNone(p)
            self.assertAlmostEqual(a.equity(p), MM * abs(a.position) * p, places=6)
            if side == BUY:
                self.assertLess(p, 100_000.0)              # long is liquidated below entry
            else:
                self.assertGreater(p, 100_000.0)           # short is liquidated above entry

    def test_breach_detection(self):
        a = new_account()
        a.apply_fill(BUY, 1.5, 100_000.0, 0.0)             # 15x
        p = a.liquidation_price(MM)
        self.assertFalse(a.is_breached(p * 1.001, MM))
        self.assertTrue(a.is_breached(p * 0.999, MM))

    def test_five_x_needs_a_much_bigger_move_than_twenty_x(self):
        low, high = new_account(), new_account()
        low.apply_fill(BUY, 0.5, 100_000.0, 0.0)           # 5x
        high.apply_fill(BUY, 1.9, 100_000.0, 0.0)          # 19x
        drop_low = 1 - low.liquidation_price(MM) / 100_000.0
        drop_high = 1 - high.liquidation_price(MM) / 100_000.0
        self.assertGreater(drop_low, 0.15)                 # about 17.5%
        self.assertLess(drop_high, 0.03)                   # about 2.7%


if __name__ == "__main__":
    unittest.main()
