"""
Scripted trading bots: simple rule-followers, NO AI and NO API cost.

They exist only to test the exchange (can leveraged traders set off a
liquidation cascade?). In Phase 1 the LLM agents replace them. The contract
is the same one the LLM agents will use:

    bot.act(view)  ->  a list of actions (OrderRequest / CancelAll)

where `view` is what Exchange.view(account_id) returns: only public market
information plus the bot's own account.
"""

import math
import random

from engine import BUY, SELL, CancelAll, OrderRequest

MIN_TRADE = 0.002      # ignore adjustments smaller than this many BTC
MIN_LOT = 0.001
                          # mini-market: must be > lot_size (0.001) but small
                          # enough that bots with $1000 collateral can trade


class Bot:
    kind = "bot"

    def __init__(self, account_id: str, seed: int, act_prob: float = 1.0):
        self.account_id = account_id
        self.rng = random.Random(f"{seed}|bot|{account_id}")
        self.act_prob = act_prob          # chance of acting on any given step

    def act(self, view) -> list:
        if self.rng.random() > self.act_prob:
            return []
        return self.decide(view)

    def decide(self, view) -> list:
        raise NotImplementedError

    # ---- helpers ------------------------------------------------------------
    def _go_to(self, view, target: float, slip: float = 0.003) -> list:
        """Trade towards a target position using an aggressive limit order
        (crosses the spread, but never pays more than `slip` beyond the quote)."""
        delta = target - view.own.position
        if abs(delta) < MIN_TRADE:
            return []
        side = BUY if delta > 0 else SELL
        ref = (view.best_ask if side == BUY else view.best_bid) or view.last_price
        price = ref * (1 + slip) if side == BUY else ref * (1 - slip)
        return [OrderRequest(self.account_id, side, abs(delta), price=price, tif="IOC")]

    @staticmethod
    def _size_for(view, leverage: float) -> float:
        return leverage * max(view.own.equity, 0.0) / view.mark_price

class NoiseBot(Bot):
    """The everyday trader with no view: sends small random buys and sells.

    This type supplies most of the trade count and volume in a real market.
    Its order sizes are heavy-tailed (many tiny orders, a few large ones):
    a log-normal with median `size_median` and spread `size_sigma`. The shape
    anchors come from one real hour (10 Oct 2025, 20:00 UTC: median trade
    0.002 BTC, mean 0.054 BTC); they are still to be re-fitted on more data,
    and `size_scale` maps real sizes onto our smaller market.

    Each order is either aggressive (crosses the spread, fills now or vanishes)
    or passive (rests near the middle for 3 steps). To keep its exposure
    bounded it only trades the reducing way once it is at `target_leverage`."""
    kind = "noise"

    def __init__(self, account_id, seed, act_prob=0.4, size_median=0.002, size_sigma=2.5,
                 size_scale=1.0, max_size=5.0, passive_prob=0.3, target_leverage=5.0,
                 slip=0.003):
        super().__init__(account_id, seed, act_prob)
        self.size_median, self.size_sigma = size_median, size_sigma
        self.size_scale, self.max_size = size_scale, max_size
        self.passive_prob, self.target_leverage, self.slip = passive_prob, target_leverage, slip

    def decide(self, view):
        side = BUY if self.rng.random() < 0.5 else SELL
        own = view.own
        if own.position != 0 and own.leverage >= self.target_leverage \
                and (side > 0) == (own.position > 0):
            side = -side                                  # already at the limit: only reduce
        size = self.size_scale * self.size_median * math.exp(self.size_sigma * self.rng.gauss(0.0, 1.0))
        size = min(self.max_size, max(MIN_LOT, size))
        mid = view.mid or view.last_price
        if self.rng.random() < self.passive_prob:
            offset = self.rng.uniform(0.0, 0.0005)        # up to 5 bp away from the middle
            price = mid * (1 - offset) if side == BUY else mid * (1 + offset)
            return [OrderRequest(self.account_id, side, size, price=price, tif="GTC", ttl_steps=3)]
        ref = (view.best_ask if side == BUY else view.best_bid) or view.last_price
        price = ref * (1 + self.slip) if side == BUY else ref * (1 - self.slip)
        return [OrderRequest(self.account_id, side, size, price=price, tif="IOC")]



class CrowdBot(Bot):
    """ Stress test tool only. Deliberately artificial: builds a leveraged LONG over `ramp_steps` and
    then holds it. Several of these together create the 'crowded leveraged
    longs' situation that a cascade needs. In Phase 1 the question is whether
    LLM agents produce such crowding on their own."""
    kind = "crowd"

    def __init__(self, account_id, seed, leverage, ramp_steps=5, act_prob=0.7):
        super().__init__(account_id, seed, act_prob)
        self.leverage = leverage
        self.ramp_steps = ramp_steps
        self.target_btc = None

    def decide(self, view):
        if self.target_btc is None:
            self.target_btc = self._size_for(view, self.leverage)
        goal = self.target_btc * min(1.0, view.step / self.ramp_steps)
        if goal > view.own.position + MIN_TRADE:
            return self._go_to(view, goal)
        return []


class TrendBot(Bot):
    """Momentum: goes long after the price has risen, short after it has fallen."""
    kind = "trend"

    def __init__(self, account_id, seed, leverage=4.0, lookback=10, threshold=0.0015, act_prob=0.5):
        super().__init__(account_id, seed, act_prob)
        self.leverage, self.lookback, self.threshold = leverage, lookback, threshold

    def decide(self, view):
        hist = view.price_history
        if len(hist) <= self.lookback:
            return []
        signal = math.log(hist[-1] / hist[-1 - self.lookback])
        if abs(signal) < self.threshold:
            return []
        sign = 1 if signal > 0 else -1
        return self._go_to(view, sign * self._size_for(view, self.leverage))


class ContrarianBot(Bot):
    """Bets against a sharp recent move."""
    kind = "contrarian"

    def __init__(self, account_id, seed, leverage=3.0, lookback=10, threshold=0.003, act_prob=0.5):
        super().__init__(account_id, seed, act_prob)
        self.leverage, self.lookback, self.threshold = leverage, lookback, threshold

    def decide(self, view):
        hist = view.price_history
        if len(hist) <= self.lookback:
            return []
        signal = math.log(hist[-1] / hist[-1 - self.lookback])
        if abs(signal) < self.threshold:
            return []
        sign = -1 if signal > 0 else 1
        return self._go_to(view, sign * self._size_for(view, self.leverage))


class ValueBot(Bot):
    """Trades the gap between this exchange and the outside reference price."""
    kind = "value"

    def __init__(self, account_id, seed, leverage=3.0, threshold=0.0015, act_prob=0.5):
        super().__init__(account_id, seed, act_prob)
        self.leverage, self.threshold = leverage, threshold

    def decide(self, view):
        gap = (view.last_price - view.index_price) / view.index_price
        if gap < -self.threshold:
            return self._go_to(view, self._size_for(view, self.leverage))
        if gap > self.threshold:
            return self._go_to(view, -self._size_for(view, self.leverage))
        if abs(gap) < self.threshold / 3:
            return self._go_to(view, 0.0)
        return []


class MakerBot(Bot):
    """Posts a buy and a sell just either side of the middle every step, and
    stops quoting the side it is already too heavy on."""
    kind = "maker"

    def __init__(self, account_id, seed, half_spread=0.0003, size=0.2, inventory_limit=1.0):
        super().__init__(account_id, seed, 1.0)
        self.half_spread, self.size, self.inventory_limit = half_spread, size, inventory_limit

    def decide(self, view):
        centre = view.mid or view.last_price
        actions = [CancelAll(self.account_id)]
        pos = view.own.position
        if pos < self.inventory_limit:
            actions.append(OrderRequest(self.account_id, BUY, self.size, price=centre * (1 - self.half_spread)))
        if pos > -self.inventory_limit:
            actions.append(OrderRequest(self.account_id, SELL, self.size, price=centre * (1 + self.half_spread)))
        return actions
