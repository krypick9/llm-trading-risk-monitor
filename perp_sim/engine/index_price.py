"""
The reference ("index" or "spot") price: what BTC is worth OUTSIDE this
exchange. It does two jobs: funding is measured against it, and the exchange's
background liquidity provider keeps its quotes loosely tied to it.

Two implementations:

1. IndexPriceProcess (synthetic)
   A random walk with occasional jumps plus one scheduled shock.
   Used for normal-regime scenarios where the exact path doesn't matter.

2. ReplayIndexPrice (historical replay — Q2 fix)
   Replays a real 1-min BTC price path from Bybit/Binance data.
   This replaces the old approach where a daily drawdown (-13.15%) was
   applied as a single-minute jump (~100σ). Now the crash unfolds at its
   real intraday pace: the worst 1-min move was -7.19%, not -13.15%.

   The replay path is loaded from data/processed/replay_prices.json,
   which contains the first 600 minutes of a real trading day.
   Prices are rescaled to start at cfg.initial_price so the sim's
   absolute price level stays consistent.
"""

import json
import math
import os
import random

from .config import Scenario, SimConfig


class IndexPriceProcess:
    def __init__(self, cfg: SimConfig, scenario: Scenario, rng: random.Random):
        self.cfg = cfg
        self.scenario = scenario
        self.rng = rng
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


class ReplayIndexPrice:
    """Historical path replay for the reference price (Q2 fix).

    Instead of a synthetic GBM with a single shock jump, this replays
    real 1-minute BTC prices. The crash unfolds at its real intraday pace.

    Usage:
        Set scenario.use_replay = True and scenario.replay_regime = "stress"
        (or "normal"). The replay path is loaded from replay_prices.json.
    """

    _replay_cache = None  # class-level cache so we don't re-read the file per run

    @classmethod
    def _load_replay_data(cls):
        if cls._replay_cache is not None:
            return cls._replay_cache

        # Try multiple paths
        candidates = [
            os.path.join(os.path.dirname(__file__), "..", "..", "data", "processed", "replay_prices.json"),
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "processed", "replay_prices.json"),
        ]
        for path in candidates:
            path = os.path.normpath(path)
            if os.path.exists(path):
                with open(path) as f:
                    cls._replay_cache = json.load(f)
                return cls._replay_cache

        print("[ReplayIndexPrice] WARNING: replay_prices.json not found, "
              "will fall back to synthetic GBM")
        cls._replay_cache = {}
        return cls._replay_cache

    def __init__(self, cfg: SimConfig, scenario: Scenario, rng: random.Random):
        self.cfg = cfg
        self.scenario = scenario
        self.rng = rng
        self.price = cfg.initial_price
        self.step_count = 0

        # Load replay path
        regime = getattr(scenario, "replay_regime", scenario.regime)
        replay_data = self._load_replay_data()

        if regime in replay_data and "prices" in replay_data[regime]:
            raw_prices = replay_data[regime]["prices"]
            self.replay_prices = raw_prices[:cfg.n_steps + 1]  # +1 because step 0 = initial
            # Rescale so first price = cfg.initial_price
            scale = cfg.initial_price / self.replay_prices[0]
            self.replay_prices = [p * scale for p in self.replay_prices]
            self.has_replay = True
            self._fallback = None  # don't create fallback unless needed
        else:
            # Fallback to synthetic GBM if replay data not available
            self.has_replay = False
            self._fallback = IndexPriceProcess(cfg, scenario, rng)

    def next(self, step: int) -> float:
        if not self.has_replay:
            return self._fallback.next(step)

        # step is 1-indexed (first call after __init__ is step=1)
        idx = step  # replay_prices[0] is the initial price (step 0)
        if idx < len(self.replay_prices):
            self.price = self.replay_prices[idx]
        else:
            # If sim runs longer than replay data, continue with GBM from last price
            s, dt = self.scenario, self.cfg.hours_per_step
            log_return = -0.5 * s.sigma_hourly ** 2 * dt \
                + s.sigma_hourly * math.sqrt(dt) * self.rng.gauss(0.0, 1.0)
            self.price *= math.exp(log_return)

        self.step_count = step
        return self.price
