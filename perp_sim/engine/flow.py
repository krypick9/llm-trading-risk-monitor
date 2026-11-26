"""Background taker flow: the DEMAND side of the market.

The LP supplies resting quotes; this supplies the customers -- aggressive orders
that cross the spread and actually trade. Without it the last price only moves
when a position-taker happens to fire, which gives mostly flat minutes.

It keeps two queue-reactive-*inspired* properties (the cheap part of the
Huang-Lehalle-Rosenbaum queue-reactive model -- flow reacting to book state --
without estimating queue-level intensities from L2 data):

  1. Volatility excitement: the arrival rate rises when the index just moved
     more than a normal recent move.
  (A second modulator, a lean toward the thinner side of the book, was deleted
  after a measurement showed it changed nothing: with it off, no calm-market
  statistic and no risk outcome moved by as much as the seed-to-seed spread.)

This flow is anonymous market texture. It is NOT liquidatable and has no
strategy; the liquidatable, swappable traders are in population.py.
"""

import math
import random

from .config import SimConfig

FLOW_ID = "__bg_flow__"

# E|z| for a standard normal, used to centre the excitement multiplier
_MEAN_ABS_Z = math.sqrt(2.0 / math.pi)


class BackgroundTakerFlow:
    """Aggressive background orders.

    Sizes are drawn from a lognormal anchored on real BTC trades and are NOT
    renormalised per step. The earlier version drew heavy-tailed sizes and then
    rescaled them so each minute's total equalled `bg_flow_btc_per_min`, which
    had three consequences:

      * volume per minute was CONSTANT -- 0.170 BTC/min at both 2.2 and 80
        orders/min -- so there was no volume dispersion and no
        volume/volatility relationship, two things a market is largely made of;
      * the volatility-excitement modulator added no volume at all, it only
        split the same total into more, smaller orders;
      * at 80 orders/min the rescaling pushed ~73% of draws below the lot size,
        where they were silently dropped.

    Phase-0 validation measured a price-impact R^2 of 0.04 against 0.36 in real
    data. Regressing price change on an order flow whose total is constant by
    construction is the most likely reason.

    Now the DISTRIBUTION is calibrated once and each minute's volume is whatever
    the draws give. If `bg_flow_btc_per_min` is set, the arrival rate is derived
    from it so that EXPECTED volume per minute hits the target; the size
    distribution keeps its real-data shape.
    """

    def __init__(self, cfg: SimConfig, seed):
        self.cfg = cfg
        self.rng = random.Random(f"{seed}|bg_flow")
        self.inventory_btc = 0.0
        self.mean_size_btc = self._estimate_mean_size()
        self.orders_per_min = cfg.bg_flow_orders_per_min
        if cfg.bg_flow_btc_per_min and self.mean_size_btc > 0:
            self.orders_per_min = cfg.bg_flow_btc_per_min / self.mean_size_btc

    def _estimate_mean_size(self, draws: int = 20_000) -> float:
        """Expected order size under the cap and the lot floor.

        Estimated by simulation rather than from the lognormal formula because
        `bg_flow_max_size_btc` truncates the tail, and for a heavy-tailed size
        distribution the tail carries most of the mean -- at the shipped
        settings the cap removes about 64% of it. Uses its own fixed seed, so
        the answer is identical for every run and seed and the main flow stream
        is untouched.
        """
        cfg = self.cfg
        rng = random.Random("bg_flow|mean_size_estimate")
        total = 0.0
        for _ in range(draws):
            size = cfg.bg_flow_size_median_btc * math.exp(
                cfg.bg_flow_size_sigma * rng.gauss(0.0, 1.0))
            total += min(cfg.bg_flow_max_size_btc, max(cfg.lot_size, size))
        return total / draws

    def _poisson(self, rate: float) -> int:
        if rate <= 0:
            return 0
        rate = min(rate, 200.0)
        limit = math.exp(-rate)
        n, p = 0, 1.0
        while True:
            p *= self.rng.random()
            if p <= limit or n >= 1000:
                return n
            n += 1

    def orders_this_step(self, step, index_history, sigma_hourly) -> list:
        cfg = self.cfg
        rate = self.orders_per_min * cfg.step_minutes

        # (1) volatility excitement, MEAN-PRESERVING.
        # The multiplier is centred so its unconditional mean is 1, the same way
        # index_price.py centres its AR(1) vol multiplier. Uncentred, this term
        # INFLATED total volume rather than redistributing it: for a normal move
        # E|z| = sqrt(2/pi) = 0.798, so the mean multiplier was
        # 1 + 2.0*0.798 = 2.60, and measured volume came out 2.57x the target.
        # Excitement should change WHEN volume arrives, not how much.
        if cfg.bg_flow_vol_excitement > 0 and len(index_history) >= 6:
            look = 5
            move = abs(index_history[-1] / index_history[-1 - look] - 1.0)
            normal = sigma_hourly * math.sqrt(look * cfg.hours_per_step)
            if normal > 0:
                excite = 1.0 + cfg.bg_flow_vol_excitement * min(move / normal, 3.0)
                mean_excite = 1.0 + cfg.bg_flow_vol_excitement * _MEAN_ABS_Z
                rate *= excite / mean_excite

        n_orders = self._poisson(rate)

        # Heavy-tailed sizes, drawn independently. Deliberately NOT renormalised
        # to a per-minute total: that is what made volume constant and killed
        # the volume/volatility relationship. Each minute's volume is the sum of
        # what the draws give, so it is dispersed and rises with the arrival
        # rate -- which is what makes the excitement modulator above do
        # something.
        sizes = []
        for _ in range(n_orders):
            size = cfg.bg_flow_size_median_btc * math.exp(
                cfg.bg_flow_size_sigma * self.rng.gauss(0.0, 1.0))
            sizes.append(min(cfg.bg_flow_max_size_btc, max(cfg.lot_size, size)))

        orders = []
        for qty in sizes:
            if abs(self.inventory_btc) > cfg.bg_flow_inventory_cap_btc:
                side = -1 if self.inventory_btc > 0 else 1
            else:
                side = 1 if self.rng.random() < 0.5 else -1
            qty = math.floor(qty / cfg.lot_size + 1e-9) * cfg.lot_size
            if qty < cfg.lot_size:
                continue
            self.inventory_btc += side * qty
            orders.append((side, qty))
        return orders
