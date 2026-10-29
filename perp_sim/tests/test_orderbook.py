import unittest

from engine.orderbook import BUY, SELL, Order, OrderBook


def limit(oid, account, side, qty, price, tif="GTC", expires=None):
    return Order(oid, account, side, qty, price, tif=tif, expires=expires)


class OrderBookTests(unittest.TestCase):
    def setUp(self):
        self.book = OrderBook(tick_size=0.5)

    def test_best_price_goes_first(self):
        self.book.submit(limit(1, "a", SELL, 1, 101.0))
        self.book.submit(limit(2, "b", SELL, 1, 100.0))
        fills = self.book.submit(limit(3, "c", BUY, 1, 101.0, tif="IOC"))
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0].maker_account, "b")
        self.assertEqual(fills[0].price, 100.0)          # trades at the resting order's price

    def test_earlier_order_goes_first_at_same_price(self):
        self.book.submit(limit(1, "first", SELL, 1, 100.0))
        self.book.submit(limit(2, "second", SELL, 1, 100.0))
        fills = self.book.submit(limit(3, "c", BUY, 1, 100.0, tif="IOC"))
        self.assertEqual(fills[0].maker_account, "first")

    def test_partial_fill_rests_the_remainder(self):
        self.book.submit(limit(1, "a", SELL, 1, 100.0))
        fills = self.book.submit(limit(2, "b", BUY, 3, 100.0))
        self.assertEqual(sum(f.qty for f in fills), 1)
        self.assertEqual(self.book.best_bid(), 100.0)
        self.assertAlmostEqual(self.book.resting_qty("b", BUY), 2.0)

    def test_ioc_remainder_vanishes(self):
        self.book.submit(limit(1, "a", SELL, 1, 100.0))
        self.book.submit(limit(2, "b", BUY, 3, 100.0, tif="IOC"))
        self.assertIsNone(self.book.best_bid())
        self.assertEqual(self.book.open_orders("b"), 0)

    def test_market_order_walks_the_book(self):
        self.book.submit(limit(1, "a", SELL, 1, 100.0))
        self.book.submit(limit(2, "b", SELL, 1, 101.0))
        fills = self.book.submit(Order(3, "c", BUY, 5, None, tif="IOC"))
        self.assertEqual([f.price for f in fills], [100.0, 101.0])
        self.assertIsNone(self.book.best_ask())

    def test_no_trade_when_prices_do_not_cross(self):
        self.book.submit(limit(1, "a", SELL, 1, 101.0))
        fills = self.book.submit(limit(2, "b", BUY, 1, 100.0))
        self.assertEqual(fills, [])
        self.assertEqual(self.book.best_bid(), 100.0)
        self.assertEqual(self.book.best_ask(), 101.0)

    def test_self_trade_is_prevented(self):
        self.book.submit(limit(1, "a", SELL, 1, 100.0))
        fills = self.book.submit(limit(2, "a", BUY, 1, 100.0, tif="IOC"))
        self.assertEqual(fills, [])
        self.assertEqual(self.book.open_orders("a"), 0)    # the resting order was cancelled

    def test_expiry(self):
        self.book.submit(limit(1, "a", BUY, 1, 100.0, expires=5))
        self.book.expire(4)
        self.assertEqual(self.book.open_orders("a"), 1)
        self.book.expire(5)
        self.assertEqual(self.book.open_orders("a"), 0)

    def test_cancel_account(self):
        self.book.submit(limit(1, "a", BUY, 1, 99.0))
        self.book.submit(limit(2, "a", SELL, 1, 101.0))
        self.book.submit(limit(3, "b", BUY, 1, 98.0))
        self.assertEqual(self.book.cancel_account("a"), 2)
        self.assertEqual(self.book.best_bid(), 98.0)
        self.assertIsNone(self.book.best_ask())

    def test_depth_only_counts_inside_the_band(self):
        self.book.submit(limit(1, "a", BUY, 2, 99.9))      # inside 0.5% of mid 100
        self.book.submit(limit(2, "a", BUY, 5, 90.0))      # far outside
        self.book.submit(limit(3, "b", SELL, 3, 100.1))
        bid, ask = self.book.depth(0.005)
        self.assertAlmostEqual(bid, 2.0)
        self.assertAlmostEqual(ask, 3.0)


if __name__ == "__main__":
    unittest.main()
