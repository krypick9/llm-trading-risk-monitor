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

MIN_TRADE = 0.01      # ignore adjustments smaller than this many BTC


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


class CrowdBot(Bot):
    """Deliberately artificial: builds a leveraged LONG over `ramp_steps` and
    then holds it. Several of these together create the 'crowded leveraged
    longs' situation that a cascade needs. In Phase 1 the question is whether
    LLM agents produce such crowding on their own."""
    kind = "crowd"

    def __init__(self, account_id, seed, leverage, ramp_steps=20, act_prob=0.7):
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
