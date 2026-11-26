"""Background taker flow: the DEMAND side of the market.

The LP supplies resting quotes; this supplies the customers -- aggressive orders
that cross the spread and actually trade. Without it the last price only moves
when a position-taker happens to fire, which gives mostly flat minutes.

It keeps two queue-reactive-*inspired* properties (the cheap part of the
Huang-Lehalle-Rosenbaum queue-reactive model -- flow reacting to book state --
without estimating queue-level intensities from L2 data):

  1. Volatility excitement: the arrival rate rises when the index just moved
     more than a normal recent move.
  2. Book-imbalance skew: flow leans toward the thinner side of the near book.

This flow is anonymous market texture. It is NOT liquidatable and has no
strategy; the liquidatable, swappable traders are in population.py.
"""

import math
import random

from .config import SimConfig

FLOW_ID = "__bg_flow__"


class BackgroundTakerFlow:
    def __init__(self, cfg: SimConfig, seed):
        self.cfg = cfg
        self.rng = random.Random(f"{seed}|bg_flow")
        self.inventory_btc = 0.0

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

    def orders_this_step(self, step, index_history, sigma_hourly, depth_bid, depth_ask) -> list:
        cfg = self.cfg
        rate = cfg.bg_flow_orders_per_min * cfg.step_minutes

        # (1) volatility excitement
        if cfg.bg_flow_vol_excitement > 0 and len(index_history) >= 6:
            look = 5
            move = abs(index_history[-1] / index_history[-1 - look] - 1.0)
            normal = sigma_hourly * math.sqrt(look * cfg.hours_per_step)
            if normal > 0:
                rate *= 1.0 + cfg.bg_flow_vol_excitement * min(move / normal, 3.0)

        n_orders = self._poisson(rate)

        # heavy-tailed sizes, renormalized to hit the per-minute volume target
        sizes = []
        for _ in range(n_orders):
            size = cfg.bg_flow_size_median_btc * math.exp(
                cfg.bg_flow_size_sigma * self.rng.gauss(0.0, 1.0))
            sizes.append(min(cfg.bg_flow_max_size_btc, max(cfg.lot_size, size)))
        if cfg.bg_flow_btc_per_min is not None and sizes:
            want = cfg.bg_flow_btc_per_min * cfg.step_minutes
            total = sum(sizes)
            if total > 0:
                scale = want / total
                sizes = [min(cfg.bg_flow_max_size_btc, s * scale) for s in sizes]

        # (2) book-imbalance skew
        p_buy = 0.5
        depth_total = depth_bid + depth_ask
        if depth_total > 0 and cfg.bg_flow_imbalance_skew:
            imb = (depth_bid - depth_ask) / depth_total
            p_buy = min(0.8, max(0.2, 0.5 + cfg.bg_flow_imbalance_skew * imb))

        orders = []
        for qty in sizes:
            if abs(self.inventory_btc) > cfg.bg_flow_inventory_cap_btc:
                side = -1 if self.inventory_btc > 0 else 1
            else:
                side = 1 if self.rng.random() < p_buy else -1
            qty = math.floor(qty / cfg.lot_size + 1e-9) * cfg.lot_size
            if qty < cfg.lot_size:
                continue
            self.inventory_btc += side * qty
            orders.append((side, qty))
        return orders
