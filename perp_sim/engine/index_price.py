"""
The reference ("index" or "spot") price: what BTC is worth OUTSIDE this
exchange. It does two jobs: funding is measured against it, and the exchange's
background liquidity provider keeps its quotes loosely tied to it.

It is made-up on purpose (a random walk with occasional jumps plus one
scheduled shock), because the point of Phase 0 is a controlled laboratory.
Later it can be swapped for replayed real data: anything with a `.next(step)`
method that returns the next price will do.
"""

import math
import random

from .config import Scenario, SimConfig


class IndexPriceProcess:
    def __init__(self, cfg: SimConfig, scenario: Scenario, rng: random.Random):
        self.cfg = cfg
        self.scenario = scenario
        self.rng = rng                      # its own random stream: the path never depends on who trades
        self.price = cfg.initial_price
        self.shock_step = scenario.shock_step(cfg.n_steps)

    def next(self, step: int) -> float:
        s, dt = self.scenario, self.cfg.hours_per_step
        log_return = -0.5 * s.sigma_hourly ** 2 * dt \
            + s.sigma_hourly * math.sqrt(dt) * self.rng.gauss(0.0, 1.0)
        if self.rng.random() < s.jump_rate_per_hour * dt:
            log_return += self.rng.gauss(s.jump_mean, s.jump_std)
        if self.shock_step is not None and step == self.shock_step:
            log_return += math.log(1.0 + s.shock_return)
        self.price *= math.exp(log_return)
        return self.price
