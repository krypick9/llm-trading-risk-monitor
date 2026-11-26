"""
Traders.

The minimal market has ONE scripted archetype -- a leveraged position-taker --
plus the hook an LLM agent plugs into. Phase-0 had five archetypes (noise, trend,
contrarian, value, maker) with invented thresholds and random cash/leverage
draws; those are collapsed here so that when a risk metric moves you know it is
the agent mix, not an interaction between four crowd types.

Why a position-taker and not a maker: resting liquidity now comes entirely from
the exchange's background LP (engine._requote_background), so the only thing the
*traders* need to do is take leveraged directional positions that can be
liquidated -- which is exactly the behaviour an LLM agent would replace, and the
source of cascades.

Action contract (same as an LLM agent must follow):
    trader.act(view) -> list of OrderRequest / CancelAll
where `view` is Exchange.view(account_id): public market state + own account.
"""

import math
import random

from engine import BUY, SELL, OrderRequest

MIN_TRADE = 0.002


class PositionTaker:
    """A leveraged directional trader.

    Each agent holds a persistent direction (drawn at construction; `long_bias`
    sets the share that are long) and ramps toward `target_leverage`, then holds.
    Because it is leveraged it can be force-closed on an adverse move -- a drop
    liquidates the longs, which is the baseline cascade setup. With long_bias=0.5
    the crowd is balanced; raise it to create crowded longs, raise target_leverage
    to make the crowd fragile (both are the knobs the power check sweeps).

    `momentum` adds a trend tilt so positions can also build endogenously from
    price moves rather than only from the fixed bias.
    """

    kind = "position_taker"

    def __init__(self, account_id, seed, target_leverage=5.0, act_prob=0.4,
                 direction=1, momentum=0.0, lookback=10, slip=0.003):
        self.account_id = account_id
        self.rng = random.Random(f"{seed}|agent|{account_id}")
        self.target_leverage = target_leverage
        self.act_prob = act_prob
        self.direction = direction          # +1 long, -1 short
        self.momentum = momentum            # 0 = pure bias; >0 = add a trend tilt
        self.lookback = lookback
        self.slip = slip

    def act(self, view):
        if self.rng.random() > self.act_prob:
            return []
        own = view.own
        sign = self.direction
        if self.momentum > 0:
            hist = view.price_history
            if len(hist) > self.lookback:
                signal = math.log(hist[-1] / hist[-1 - self.lookback])
                if abs(signal) > 0:
                    sign = 1 if (self.direction + self.momentum * (1 if signal > 0 else -1)) >= 0 else -1
        target = sign * self.target_leverage * max(own.equity, 0.0) / view.mark_price
        delta = target - own.position
        if abs(delta) < MIN_TRADE:
            return []
        side = BUY if delta > 0 else SELL
        ref = (view.best_ask if side == BUY else view.best_bid) or view.last_price
        price = ref * (1 + self.slip) if side == BUY else ref * (1 - self.slip)
        return [OrderRequest(self.account_id, side, abs(delta), price=price, tif="IOC")]


# ---- the LLM hook --------------------------------------------------------
# An LLM agent must expose the same interface: an `account_id` attribute and an
# `act(view)` method returning a list of OrderRequest / CancelAll. Build it with
# a factory(slot, seed) -> agent passed to population.build_population(...).
#
# Example skeleton (not wired to any API here):
#
#   class LLMAgent:
#       def __init__(self, account_id, seed, policy):
#           self.account_id = account_id
#           self.policy = policy
#       def act(self, view):
#           decision = self.policy(view)      # your prompt/response -> intended position
#           ...                               # translate to OrderRequest(s)
#           return actions
