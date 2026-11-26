"""
Traders.

Two scripted archetypes, plus the hook an LLM agent plugs into:

  PositionTaker -- a leveraged directional trader. The thing that gets
      liquidated, and so the source of cascades.
  MakerAgent    -- a liquidity provider with its OWN capital, its own inventory
      limit, and the right to widen and withdraw. The thing that decides how
      much a trade moves the price.

Phase-0 had five archetypes with invented thresholds; the minimal cut collapsed
them to the position-taker alone and made the exchange's background LP the only
resting liquidity. That turned out to cost something measurable: with one
unbounded LP as the whole supply side, quoted spread and price impact are
opposite ends of a single dial, and no amount of replenishment tuning separates
them (see README "Intra-step replenishment: measured, and not adopted" on the
branch where that was measured). Finite, strategic liquidity is what separates
them, so the maker is back -- but as ONE archetype with a documented purpose,
not a crowd of four.

Action contract (same as an LLM agent must follow):
    trader.act(view) -> list of OrderRequest / CancelAll
where `view` is Exchange.view(account_id): public market state + own account.
"""

import math
import random

from engine import BUY, SELL, CancelAll, OrderRequest

MIN_TRADE = 0.002


class PositionTaker:
    """A leveraged directional trader.

    Each agent holds a persistent direction (drawn at construction; `long_bias`
    sets the share that are long) and builds toward `target_leverage`. Because it
    is leveraged it can be force-closed on an adverse move -- a drop liquidates
    the longs, which is the baseline cascade setup.

    `rebalance` decides what happens AFTER the position is built, and it decides
    whether the trader can ever be liquidated at moderate leverage:

      "hold" (default) -- size the position once, from starting equity, then hold
          that many BTC. As the price falls, equity falls and leverage RISES, so
          the trader walks into its own liquidation. This is ordinary leveraged
          retail behaviour and it is what a cascade is made of.

      "constant_leverage" -- re-target `leverage x equity / mark` on every act,
          which means SELLING into a loss to restore the target. That is a
          volatility-targeting strategy: it self-deleverages and is almost
          impossible to liquidate. With it, liquidations were 0 at 2x, 5x and 10x
          and only appeared at 20x, where the 4% margin buffer is thin enough
          that a fast move lands before the trader can rebalance. Kept because
          "do self-deleveraging traders cascade?" is a real Phase-2 question --
          but it is the wrong baseline.

    `momentum` adds a trend tilt so positions can also build endogenously from
    price moves rather than only from the fixed bias.
    """

    kind = "position_taker"

    def __init__(self, account_id, seed, target_leverage=5.0, act_prob=0.4,
                 direction=1, momentum=0.0, lookback=10, slip=0.003,
                 rebalance="hold", ramp_steps=5):
        self.account_id = account_id
        self.rng = random.Random(f"{seed}|agent|{account_id}")
        self.target_leverage = target_leverage
        self.act_prob = act_prob
        self.direction = direction          # +1 long, -1 short
        self.momentum = momentum            # 0 = pure bias; >0 = add a trend tilt
        self.lookback = lookback
        self.slip = slip
        if rebalance not in ("hold", "constant_leverage"):
            raise ValueError(f"unknown rebalance mode {rebalance!r}")
        self.rebalance = rebalance
        self.ramp_steps = max(1, ramp_steps)
        self.target_btc = None              # fixed once, in "hold" mode

    def _sign(self, view):
        sign = self.direction
        if self.momentum > 0:
            hist = view.price_history
            if len(hist) > self.lookback:
                signal = math.log(hist[-1] / hist[-1 - self.lookback])
                if abs(signal) > 0:
                    sign = 1 if (self.direction + self.momentum * (1 if signal > 0 else -1)) >= 0 else -1
        return sign

    def act(self, view):
        if self.rng.random() > self.act_prob:
            return []
        own = view.own
        sign = self._sign(view)

        if self.rebalance == "constant_leverage":
            target = sign * self.target_leverage * max(own.equity, 0.0) / view.mark_price
        else:
            # size once from starting equity, then hold that many BTC
            if self.target_btc is None:
                self.target_btc = self.target_leverage * max(own.equity, 0.0) / view.mark_price
            ramped = self.target_btc * min(1.0, view.step / self.ramp_steps)
            target = sign * ramped
            # holding means not topping up a position the market has shrunk for
            # us, and not selling into a loss either -- only build, and only
            # close when the direction flips
            if sign * own.position > sign * target:
                return []

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


class MakerAgent:
    """A liquidity provider with finite capital that can widen and withdraw.

    Unlike the exchange's background LP -- which has an unbounded balance sheet
    and re-posts a full ladder every step whatever happens -- a maker here:

      * quotes from its OWN collateral, so its size is bounded by its account;
      * holds an inventory limit and stops quoting the side that would breach
        it, skewing away from inventory before that;
      * WITHDRAWS when the market turns against it -- when recent realised
        volatility exceeds `vol_withdraw`, it widens by `widen_factor` and cuts
        its size, and beyond twice that threshold it stops quoting entirely.

    The third behaviour is the one the market model was missing. Depth that
    disappears exactly when it is needed is a documented feature of real
    stressed books and a cascade channel in its own right: forced selling meets
    a thinner book than the one that was there a minute earlier. The background
    LP's `bg_walkaway` was a crude proxy for it; this is the behaviour itself,
    on an agent that can run out of money.

    It quotes one level per side. Near depth comes from there being several
    makers, not from one maker quoting a ladder -- so losing a few of them
    thins the touch, which is the point.
    """

    kind = "maker"

    def __init__(self, account_id, seed, half_spread_ticks=1.5, quote_size=0.05,
                 inventory_limit=0.10, skew=0.6, vol_withdraw=0.0015,
                 widen_factor=3.0, lookback=5):
        self.account_id = account_id
        self.rng = random.Random(f"{seed}|maker|{account_id}")
        self.half_spread_ticks = half_spread_ticks
        self.quote_size = quote_size
        self.inventory_limit = inventory_limit
        self.skew = skew
        self.vol_withdraw = vol_withdraw
        self.widen_factor = widen_factor
        self.lookback = lookback

    def _recent_move(self, view) -> float:
        hist = view.price_history
        if len(hist) <= self.lookback:
            return 0.0
        return abs(math.log(hist[-1] / hist[-1 - self.lookback]))

    def act(self, view):
        centre = view.mid or view.last_price
        if centre <= 0:
            return []
        actions = [CancelAll(self.account_id)]

        half = view.tick_size * self.half_spread_ticks / centre
        size = self.quote_size

        # strategic withdrawal
        move = self._recent_move(view)
        if move > 2.0 * self.vol_withdraw:
            return actions                      # off the market entirely
        if move > self.vol_withdraw:
            half *= self.widen_factor
            size *= 0.4

        pos = view.own.position
        limit = self.inventory_limit
        frac = pos / limit if limit else 0.0
        buy_half = half * (1 + self.skew * max(0.0, frac))
        sell_half = half * (1 + self.skew * max(0.0, -frac))

        # A wiped-out maker stops quoting. Beyond that, size is bounded by the
        # inventory limit above and by the exchange's own leverage clip
        # (Exchange._clip_for_leverage), so there is no third cap here.
        if view.own.equity <= 0:
            return actions

        if pos < limit and size > 0:
            actions.append(OrderRequest(self.account_id, BUY, size,
                                        price=centre * (1 - buy_half)))
        if pos > -limit and size > 0:
            actions.append(OrderRequest(self.account_id, SELL, size,
                                        price=centre * (1 + sell_half)))
        return actions
