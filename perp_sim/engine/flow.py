"""Background taker flow: the DEMAND side of the simulated market.

The background LP supplies passive liquidity (a shelf of resting quotes);
this module supplies the customers -- a stream of aggressive orders that
cross the spread and therefore actually trade. Without it, the local last
price only moves when a signal bot happens to fire, which produced ~60%
flat minutes in Phase 0 calibration. Quotes moving is not a fill.

Design follows the spirit of the Queue-Reactive model -- Stoikov, S.,
"Simulating and Analyzing Order Book Data: The Queue-Reactive Model",
Journal of the American Statistical Association 110(509), 2014
(DOI 10.1080/01621459.2014.982278) -- where order-flow intensities depend
on the observable state of the book rather than being a fixed random
stream. The full model estimates queue-level intensities from L2 data;
our simplified proxy keeps two of its qualitative properties:

  1. Volatility excitement: the base arrival rate rises when the index has
     just moved more than a normal recent move (aggressive flow clusters
     in volatile time).
  2. Book-imbalance skew: flow leans toward the thinner side of the near
     book (aggressors lift offers more readily when the offer side is
     depleted, and hit bids when the bid side is depleted).

Calibration anchors (data/processed/calibration_targets.json, Binance
1-minute aggTrades): median 547 trades/min and 48.5 BTC/min volume in the
normal regime. Both are scaled by ONE market-scale factor
(see data/scripts/calibrate_market_scale.py) so the flow/depth ratio --
and therefore normalized price impact -- is inherited from the real market.
"""

import math
import random

from .config import SimConfig

FLOW_ID = "__bg_flow__"   # account id the background flow trades through


class BackgroundTakerFlow:
    """State-dependent stream of aggressive background orders.

    The module does not touch the exchange directly: it returns
    (side, qty) pairs each step and the exchange wraps them into market
    orders from a dedicated background account, so the flow competes in
    the same shuffled arrival order as everyone else.

    Inventory note: a pure random flow would accumulate an unbounded net
    position. Once the absolute inventory exceeds `bg_flow_inventory_cap_btc`
    the flow trades only in the reducing direction until it is back inside
    the cap (the same bounded-exposure rule NoiseBot uses).
    """

    def __init__(self, cfg: SimConfig, seed):
        self.cfg = cfg
        self.rng = random.Random(f"{seed}|bg_flow")
        self.inventory_btc = 0.0

    # ---- Poisson draw (Knuth; rate is small, overflow-guarded) -------------
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

    def orders_this_step(self, step: int, index_history: list,
                         sigma_hourly: float, depth_bid: float,
                         depth_ask: float) -> list:
        cfg = self.cfg
        rate = cfg.bg_flow_orders_per_min * cfg.step_minutes

        # State modulator 1: volatility excitement.
        if cfg.bg_flow_vol_excitement > 0 and len(index_history) >= 6:
            look = 5
            move = abs(index_history[-1] / index_history[-1 - look] - 1.0)
            normal = sigma_hourly * math.sqrt(look * cfg.hours_per_step)
            if normal > 0:
                rate *= 1.0 + cfg.bg_flow_vol_excitement * min(move / normal, 3.0)

        n_orders = self._poisson(rate)

        # Sizes: lognormal (heavy-tailed), optionally renormalized so the
        # expected traded volume per minute hits the calibration target.
        sizes = []
        for _ in range(n_orders):
            size = cfg.bg_flow_size_median_btc * math.exp(
                cfg.bg_flow_size_sigma * self.rng.gauss(0.0, 1.0))
            sizes.append(min(cfg.bg_flow_max_size_btc,
                             max(cfg.lot_size, size)))
        if cfg.bg_flow_btc_per_min is not None and sizes:
            want = cfg.bg_flow_btc_per_min * cfg.step_minutes
            total = sum(sizes)
            if total > 0:
                scale = want / total
                sizes = [min(cfg.bg_flow_max_size_btc, s * scale) for s in sizes]

        # State modulator 2: book-imbalance skew.
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
