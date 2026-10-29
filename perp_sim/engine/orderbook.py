"""
The order book: the list of everyone who wants to buy or sell, and the
matching rule.

Rule (same as a real exchange): the best price goes first; if two orders have
the same price, the one that arrived first goes first. A trade always happens
at the price of the order that was already resting in the book.

Self-trade protection: if an incoming order would trade against the same
account's own resting order, the resting order is cancelled instead.
"""

from bisect import bisect_left, insort
from collections import defaultdict, deque
from typing import NamedTuple, Optional

BUY = 1
SELL = -1
EPS = 1e-9


class Order:
    __slots__ = ("oid", "account", "side", "qty", "remaining", "price",
                 "tif", "reduce_only", "step", "expires")

    def __init__(self, oid, account, side, qty, price, tif="GTC",
                 reduce_only=False, step=0, expires=None):
        self.oid = oid
        self.account = account
        self.side = side              # BUY (+1) or SELL (-1)
        self.qty = qty
        self.remaining = qty
        self.price = price            # None = market order (takes whatever is there)
        self.tif = tif                # "GTC" = may rest in the book, "IOC" = fill now or vanish
        self.reduce_only = reduce_only
        self.step = step
        self.expires = expires        # step at which a resting order is removed


class Fill(NamedTuple):
    price: float
    qty: float
    taker_order: int
    taker_account: str
    taker_side: int
    maker_order: int
    maker_account: str


class OrderBook:
    def __init__(self, tick_size: float):
        self.tick = tick_size
        self.bids = {}            # price -> deque of resting buy orders (oldest first)
        self.asks = {}            # price -> deque of resting sell orders
        self._bid_prices = []     # sorted ascending; best bid is the last one
        self._ask_prices = []     # sorted ascending; best ask is the first one
        self.by_id = {}
        self.by_account = defaultdict(dict)

    # ---- prices -----------------------------------------------------------
    def round_price(self, price: float) -> float:
        return round(round(price / self.tick) * self.tick, 8)

    def best_bid(self) -> Optional[float]:
        return self._bid_prices[-1] if self._bid_prices else None

    def best_ask(self) -> Optional[float]:
        return self._ask_prices[0] if self._ask_prices else None

    def mid(self) -> Optional[float]:
        bid, ask = self.best_bid(), self.best_ask()
        return (bid + ask) / 2 if bid is not None and ask is not None else None

    # ---- submitting and matching -----------------------------------------
    def submit(self, order: Order) -> list:
        fills = []
        opposite = self.asks if order.side == BUY else self.bids
        while order.remaining > EPS:
            best = self.best_ask() if order.side == BUY else self.best_bid()
            if best is None:
                break
            if order.price is not None:
                if order.side == BUY and best > order.price + 1e-12:
                    break
                if order.side == SELL and best < order.price - 1e-12:
                    break
            maker = opposite[best][0]
            if maker.account == order.account:       # self-trade protection
                self._remove(maker)
                continue
            qty = min(order.remaining, maker.remaining)
            fills.append(Fill(best, qty, order.oid, order.account, order.side,
                              maker.oid, maker.account))
            order.remaining -= qty
            maker.remaining -= qty
            if maker.remaining <= EPS:
                self._remove(maker)
        if order.remaining > EPS and order.price is not None and order.tif == "GTC":
            self._rest(order)
        return fills

    # ---- resting, removing, cancelling -----------------------------------
    def _rest(self, order: Order) -> None:
        side_book = self.bids if order.side == BUY else self.asks
        prices = self._bid_prices if order.side == BUY else self._ask_prices
        if order.price not in side_book:
            side_book[order.price] = deque()
            insort(prices, order.price)
        side_book[order.price].append(order)
        self.by_id[order.oid] = order
        self.by_account[order.account][order.oid] = order

    def _remove(self, order: Order) -> None:
        side_book = self.bids if order.side == BUY else self.asks
        prices = self._bid_prices if order.side == BUY else self._ask_prices
        level = side_book.get(order.price)
        if level is not None:
            level.remove(order)
            if not level:
                del side_book[order.price]
                del prices[bisect_left(prices, order.price)]
        self.by_id.pop(order.oid, None)
        self.by_account[order.account].pop(order.oid, None)

    def cancel(self, oid: int) -> None:
        order = self.by_id.get(oid)
        if order is not None:
            self._remove(order)

    def cancel_account(self, account: str) -> int:
        orders = list(self.by_account.get(account, {}).values())
        for order in orders:
            self._remove(order)
        return len(orders)

    def expire(self, step: int) -> None:
        stale = [o for o in self.by_id.values()
                 if o.expires is not None and o.expires <= step]
        for order in stale:
            self._remove(order)

    # ---- reading the book -------------------------------------------------
    def resting_qty(self, account: str, side: int) -> float:
        return sum(o.remaining for o in self.by_account.get(account, {}).values()
                   if o.side == side)

    def open_orders(self, account: str) -> int:
        return len(self.by_account.get(account, {}))

    def top_size(self, side: int) -> float:
        price = self.best_bid() if side == BUY else self.best_ask()
        if price is None:
            return 0.0
        book = self.bids if side == BUY else self.asks
        return sum(o.remaining for o in book[price])

    def depth(self, band: float) -> tuple:
        """Total resting size within `band` (e.g. 0.005 = 0.5%) of the mid price,
        returned as (buy side, sell side). Zero if either side is empty."""
        mid = self.mid()
        if mid is None:
            return 0.0, 0.0
        bid_total = 0.0
        for price in reversed(self._bid_prices):
            if price < mid * (1 - band):
                break
            bid_total += sum(o.remaining for o in self.bids[price])
        ask_total = 0.0
        for price in self._ask_prices:
            if price > mid * (1 + band):
                break
            ask_total += sum(o.remaining for o in self.asks[price])
        return bid_total, ask_total
