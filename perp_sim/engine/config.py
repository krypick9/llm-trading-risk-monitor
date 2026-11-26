"""Configuration for one run of the minimal simulator.

This is the deliberately-small version of the Phase-0 config. Everything here
is a FIXED, DOCUMENTED choice, not a value fitted to real BTC data. The project
makes only *relative* comparisons between agent mixes under identical settings,
so the market only has to be reasonable and held constant -- it does not have to
match real Bybit/Binance numbers. That is why there is no calibration grid and
no moment-matching: see README.md.

Removed from the Phase-0 config on purpose (see README "What was cut"):
  - circuit breaker (that is a Phase-2 intervention, not the baseline model)
  - the market-scale calibration machinery and its many flow dials

Restored after review (see README "Restored after review"):
  - funding (a perpetual-futures risk channel; the Phase-0 cap was ~1/20 of the
    real venue cap, which is why it looked negligible -- the cap, not the
    mechanism, was the problem)
  - AR(1) volatility clustering, as a hand-set vol multiplier (see Scenario)
  - historical replay of a real BTC path, as a third scenario
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SimConfig:
    # ---- size of the run --------------------------------------------------
    n_steps: int = 600               # simulated minutes in one run
    step_minutes: float = 1.0        # real time one step stands for

    # ---- the market -------------------------------------------------------
    initial_price: float = 100_000.0
    tick_size: float = 0.1
    lot_size: float = 0.001          # smallest order, in BTC

    # ---- accounts and leverage (mini-market: $1000 per trader) -----------
    # These are CHOSEN, not calibrated. Leverage/maintenance are plausible
    # (Hyperliquid-like); the $1000 collateral fixes the mini-market scale.
    max_leverage: float = 20.0
    maintenance_margin_rate: float = 0.01
    initial_collateral: float = 1000.0

    # ---- fees (plausible perp rates) -------------------------------------
    taker_fee: float = 0.00045
    maker_fee: float = 0.00015

    # ---- funding (perpetual-futures cash transfer between longs and shorts) --
    # Restored after review. Funding is what drains a crowded side's collateral
    # between liquidations, so it belongs in a perp risk study. The Phase-0 cap
    # (1.25e-5 per hour) was ~1/20 of a real venue's 0.75% per 8h, which made
    # funding look irrelevant; the cap below is the real order of magnitude.
    # Set funding_enabled=False for a no-funding counterfactual.
    funding_enabled: bool = True
    funding_interval_minutes: float = 60.0
    funding_scale: float = 0.125                     # share of the average premium paid per hour
    funding_interest_per_hour: float = 0.0000121425  # Hyperliquid-style interest component
    funding_cap_per_hour: float = 0.0009375          # = 0.75% per 8h, the Binance/Bybit cap

    # ---- background liquidity provider (the resting book) ----------------
    # One liquidity source for the whole market (Phase-0 had three: this LP,
    # MakerBots, and taker flow -- the overlap caused the scale conflict).
    # The LP is the only tie between the exchange price and the outside index.
    #   bg_anchor_strength: 0 = ignore the index, 1 = follow it fully.
    #   bg_levels x bg_level_gap: how far the quoted liquidity reaches.
    bg_anchor_strength: float = 0.8
    bg_levels: int = 5
    bg_level_gap: float = 0.0002          # 0.02% spacing between quoted levels
    bg_walkaway_threshold: float = 0.01   # LP retreats on a >1% move over 5 steps

    # ---- background taker flow (the demand side; see engine/flow.py) ------
    # Anonymous aggressive orders so the last price actually moves on trades.
    # It is NOT liquidatable and has no strategy -- it is market texture, not an
    # agent. The liquidatable, swappable traders live in population.py.
    bg_flow_orders_per_min: float = 80.0
    bg_flow_btc_per_min: float = 0.194    # volume target; drawn sizes renormalize to this
    bg_flow_size_median_btc: float = 0.002
    bg_flow_size_sigma: float = 2.5
    bg_flow_max_size_btc: float = 0.5
    bg_flow_inventory_cap_btc: float = 5.0
    bg_flow_vol_excitement: float = 2.0   # arrival rate rises when the index just moved
    bg_flow_imbalance_skew: float = 0.15  # flow leans to the thinner book side

    # ---- measurement ------------------------------------------------------
    depth_band: float = 0.001         # +-0.10% band for the depth columns
    near_liq_buffer: float = 0.05     # "near liquidation" below a 5% margin buffer

    # ---- liquidation engine ----------------------------------------------
    liquidations_enabled: bool = True   # False = counterfactual control (no forced closing)
    max_liquidation_rounds: int = 25    # cap on chain reactions inside one step

    seed: int = 0

    @property
    def hours_per_step(self) -> float:
        return self.step_minutes / 60.0

    @property
    def funding_interval_steps(self) -> int:
        return max(1, round(self.funding_interval_minutes / self.step_minutes))


@dataclass(frozen=True)
class Scenario:
    """The outside world: how the reference price behaves and how the LP quotes.

    Only two are shipped (see engine/scenarios.py): `normal` and `stress`.
    """

    name: str
    regime: str                 # "normal" or "stress"
    sigma_hourly: float         # reference-price volatility over one hour
    jump_rate_per_hour: float   # average random jumps per hour
    jump_mean: float            # average jump size (e.g. -0.001 = -0.1%)
    jump_std: float             # spread of jump sizes
    bg_depth_btc: float         # LP liquidity per side, in BTC
    bg_half_spread: float       # LP quotes sit this far (relative) from the centre
    bg_walkaway: bool           # LP liquidity retreats when prices move fast
    shock_frac: float | None = None   # when an optional scheduled shock hits (share of run)
    shock_return: float = 0.0         # size of that shock, e.g. -0.05 = -5%
    # volatility clustering (AR(1) multiplier on the index vol; hand-set, NOT fitted).
    #   vol_cluster_rho: persistence of the vol regime (0 = off, ~0.95 = sticky).
    #   vol_cluster_vov: how much the vol multiplier swings ("vol of vol").
    vol_cluster_rho: float = 0.0
    vol_cluster_vov: float = 0.0
    # historical replay (restored after review). When use_replay is True the
    # index follows a real 1-minute BTC path from data/processed/replay_prices.json
    # instead of the synthetic process, and no scheduled shock is applied.
    use_replay: bool = False
    replay_regime: str = ""           # which stored path: "normal" | "stress" | "extreme"
    replay_offset: int = 0            # start this many minutes into the stored path

    def shock_step(self, n_steps: int) -> int | None:
        if self.use_replay:
            return None
        if self.shock_frac is None:
            return None
        return max(1, round(self.shock_frac * n_steps))
