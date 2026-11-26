"""
The reference ("index"/"spot") price: what BTC is worth OUTSIDE this exchange.
The background LP keeps its quotes loosely tied to it.

Two implementations:

1. IndexPriceProcess (synthetic) -- geometric Brownian motion with occasional
   jumps, an AR(1) volatility multiplier so high-vol minutes cluster into
   episodes (the regime where cascades build), and an optional scheduled shock.

2. ReplayIndexPrice (historical) -- replays a real 1-minute BTC path. Restored
   after review: it is the project's only empirical anchor and the only stress
   event that is not of our own invention. Unlike the Phase-0 version it does
   NOT silently fall back to synthetic GBM when the stored path is missing; a
   missing or too-short path raises, so a replay run can never quietly become a
   synthetic one.

The clustering is a hand-set AR(1) on a latent state, turned into a vol
multiplier -- there is no fitting to a target ACF. It is centred so the long-run
average volatility is unchanged; it only changes the *timing* of volatility.

The path is deterministic for a given regime/seed and NEVER depends on who
trades, so two runs that differ only in the agent mix see the identical outside
world.
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
        self.vol_state = 0.0          # latent AR(1), stationary unit variance

    def _vol_multiplier(self) -> float:
        rho, vov = self.scenario.vol_cluster_rho, self.scenario.vol_cluster_vov
        if rho <= 0 or vov <= 0:
            return 1.0
        self.vol_state = rho * self.vol_state + math.sqrt(1 - rho ** 2) * self.rng.gauss(0.0, 1.0)
        # exp of the latent state, centred so E[multiplier] = 1 (level unchanged)
        return math.exp(vov * self.vol_state - 0.5 * vov ** 2)

    def next(self, step: int) -> float:
        s, dt = self.scenario, self.cfg.hours_per_step
        sigma = s.sigma_hourly * self._vol_multiplier()
        log_return = -0.5 * sigma ** 2 * dt + sigma * math.sqrt(dt) * self.rng.gauss(0.0, 1.0)
        if self.rng.random() < s.jump_rate_per_hour * dt:
            log_return += self.rng.gauss(s.jump_mean, s.jump_std)
        if self.shock_step is not None and step == self.shock_step:
            log_return += math.log(1.0 + s.shock_return)
        self.price *= math.exp(log_return)
        return self.price


class ReplayIndexPrice:
    """Replay a stored real 1-minute BTC path as the index price.

    The path comes from data/processed/replay_prices.json, which holds one entry
    per regime ("normal", "stress", "extreme") with a `prices` list. Prices are
    rescaled so the path starts at cfg.initial_price, which keeps the sim's
    absolute price level (and therefore tick/lot granularity) consistent across
    scenarios.

    `scenario.replay_offset` skips that many minutes into the stored path. This
    matters: in the 10-Oct "extreme" path the crash is around minute 500, so a
    600-step run from offset 0 spends most of its length before the event. Set
    the offset to start the run near the onset.

    The path is pure data -- identical for every seed. So replay runs vary only
    through trader and flow randomness, and a replay scenario needs more seeds
    than a synthetic one to say anything about the index path itself.
    """

    _cache = None

    @classmethod
    def _load(cls) -> dict:
        if cls._cache is None:
            path = os.path.normpath(os.path.join(
                os.path.dirname(__file__), "..", "..",
                "data", "processed", "replay_prices.json"))
            if not os.path.exists(path):
                raise FileNotFoundError(
                    f"replay path file not found at {path}. A replay scenario "
                    f"cannot run without it -- use a synthetic scenario instead.")
            with open(path) as handle:
                cls._cache = json.load(handle)
        return cls._cache

    def __init__(self, cfg: SimConfig, scenario: Scenario, rng: random.Random):
        self.cfg = cfg
        self.scenario = scenario
        self.rng = rng
        self.price = cfg.initial_price

        regime = scenario.replay_regime or scenario.regime
        data = self._load()
        if regime not in data or "prices" not in data[regime]:
            raise KeyError(
                f"replay regime {regime!r} is not in replay_prices.json "
                f"(available: {sorted(data)})")

        offset = max(0, int(getattr(scenario, "replay_offset", 0)))
        raw = data[regime]["prices"][offset:offset + cfg.n_steps + 1]
        if len(raw) < cfg.n_steps + 1:
            raise ValueError(
                f"replay regime {regime!r} has {len(data[regime]['prices'])} minutes; "
                f"a {cfg.n_steps}-step run from offset {offset} needs "
                f"{cfg.n_steps + 1}. Shorten n_steps or lower replay_offset.")
        scale = cfg.initial_price / raw[0]
        self.prices = [p * scale for p in raw]
        self.replay_date = data[regime].get("date", "unknown")

    def next(self, step: int) -> float:
        self.price = self.prices[step]       # prices[0] is the step-0 initial price
        return self.price
