"""Configuration for one experiment or calibration run.

The checked-in Bybit near-depth target is measured within ±0.10% of mid;
the environment is still a minimarket and does not yet match local trade
activity, spreads or impact. Phase 1 economics need team approval.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SimConfig:
    # ---- size of the run (small demo defaults; raise to scale) ------------
    n_steps: int = 120               # number of simulated steps in one run
    step_minutes: float = 1.0        # real time that one step stands for

    # ---- the market --------------------------------------------------------
    initial_price: float = 100_000.0  # replay paths are rescaled to this anchor
    tick_size: float = 0.1            # matches the current Bybit BTCUSDT reference
    lot_size: float = 0.001           # smallest order size, in BTC

    # ---- accounts and leverage --------------------------------------------
    max_leverage: float = 20.0           # per-account cap (Phase 2 tests lowering it)
    maintenance_margin_rate: float = 0.01  # calibrated to Hyperliquid (~1%)
    initial_collateral: float = 1000.0     # mini-market: $1000 per trader
    total_trader_collateral_budget: float | None = None

    # ---- fees (calibrated to Binance/Hyperliquid rates) -------------------
    taker_fee: float = 0.00045
    maker_fee: float = 0.00015

    # ---- funding (calibrated to Hyperliquid hourly funding) ----------------
    funding_interval_minutes: float = 60.0    # how often funding cash changes hands
    funding_scale: float = 0.125              # share of the average premium paid per hour
    funding_interest_per_hour: float = 0.0000121425  # from Hyperliquid funding history
    funding_cap_per_hour: float = 1.25e-05    # calibrated cap from real data

    # ---- background liquidity provider ------------------------------------
    # The one place the exchange price is tied to the outside world. These two
    # dials decide whether forced selling can feed on itself, so they are the
    # first things to calibrate on real order-book data:
    #   bg_anchor_strength: 0 = ignores the reference price, 1 = follows it fully
    #   bg_levels x bg_level_gap: how far from the price the book's liquidity reaches.
    #     Provisional candidates only; ladder shape still needs empirical fitting.
    bg_anchor_strength: float = 0.8
    bg_levels: int = 5                # how many price levels it quotes per side
    bg_level_gap: float = 0.0002      # 0.02% spacing: 5 levels now span the +-0.10% near-depth band
    bg_walkaway_threshold: float = 0.01  # retreat threshold over the 5-step lookback

    # ---- background taker flow (the demand side; see engine/flow.py) ------
    # The LP only supplies resting quotes; the local last price moves only on
    # fills, so the market needs a source of aggressive orders. Calibrate via
    # data/scripts/calibrate_market_scale.py (one scale factor sets flow AND
    # depth). Anchors: Binance 547 trades/min, 48.5 BTC/min (normal regime).
    bg_flow_enabled: bool = False
    bg_flow_orders_per_min: float = 80.0     # expected aggressive orders per minute
    # NOTE: the count is deliberately HIGHER than the scale-scaled real count
    # (0.004 x 547 = 2.2/min) for two discretization reasons: Poisson zeros
    # alone would give ~11% empty minutes (real: 3.76%), and few-but-large
    # orders stall on one ladder level, leaving the last price unchanged for
    # whole minutes even while volume trades. The volume target
    # (bg_flow_btc_per_min) keeps the anchored per-minute volume; the higher
    # count only granularizes it.
    bg_flow_btc_per_min: float | None = 0.194  # volume target; renormalizes drawn sizes
    bg_flow_size_median_btc: float = 0.002   # real median trade (2 lots)
    bg_flow_size_sigma: float = 2.5          # heavy-tail spread of sizes
    bg_flow_max_size_btc: float = 0.5
    bg_flow_inventory_cap_btc: float = 5.0   # flow trades to reduce beyond this
    bg_flow_vol_excitement: float = 2.0      # rate multiplier at 1x a normal 5-min move
    bg_flow_imbalance_skew: float = 0.15     # flow leans to the thinner book side

    # ---- backstop behaviour of the background LP --------------------------
    # When participant makers quote, the LP ladder starts BEYOND their best
    # quotes (by bg_backstop_extra) instead of overlapping them, so measured
    # spread/depth come from participant makers with the LP as a backstop.
    bg_backstop_only: bool = False
    bg_backstop_extra: float = 0.0002

    # ---- measurement -------------------------------------------------------
    depth_band: float = 0.001         # Bybit near-depth uses a ±0.10% band
    near_liq_buffer: float = 0.05     # an account is "near liquidation" below 5% buffer

    # ---- liquidation engine ------------------------------------------------
    liquidations_enabled: bool = True   # False = a counterfactual control: no forced closing
    max_liquidation_rounds: int = 25    # safety limit on chain reactions inside one step

    # ---- circuit breaker (Phase 2 intervention) -----------------------------
    # When enabled, the exchange rejects all new MARKET orders if the last
    # price deviates more than circuit_breaker_pct from the index price.
    # This models a "limit up / limit down" regime: forced selling cannot
    # push the price beyond the band, breaking the cascade feedback loop.
    # Phase 1: disabled (circuit_breaker_enabled=False) to observe unmitigated cascades.
    # Phase 2: enabled to test intervention effectiveness.
    # ---- index volatility clustering (log-vol AR(1)) -----------------------
    # A constant sigma cannot produce volatility clustering (real BTC 1-min
    # squared-return ACF1 ~ 0.27); the reference data were fit on vol/jumps/
    # kurtosis only, so this structural gap went unmeasured until the ACF
    # label bug was fixed. When enabled, log-volatility follows a mean-
    # reverting AR(1): large moves keep sigma elevated for a while, quiet
    # periods stay quiet. Calibrate (phi, eta) to the training-day ACF/kurtosis.
    index_vol_clustering: bool = False
    sigma_ar_phi: float = 0.95      # persistence of log-volatility, per step [grid-fit on training seeds, log-vol clamped to +-2.0]
    sigma_ar_eta: float = 0.3       # per-step innovation to log-volatility [grid-fit]
    circuit_breaker_enabled: bool = False
    circuit_breaker_pct: float = 0.05   # 5% deviation triggers the breaker
    circuit_breaker_cooldown_steps: int = 10  # how long the breaker stays active

    seed: int = 0

    @property
    def hours_per_step(self) -> float:
        return self.step_minutes / 60.0

    @property
    def funding_interval_steps(self) -> int:
        return max(1, round(self.funding_interval_minutes / self.step_minutes))


@dataclass(frozen=True)
class Scenario:
    """A fixed script for the outside world: how the reference price behaves,
    how deep the background liquidity is, and whether a shock hits."""

    name: str
    regime: str                 # "normal" or "stress"
    sigma_hourly: float         # reference-price volatility over one hour
    jump_rate_per_hour: float   # average number of random jumps per hour
    jump_mean: float            # average size of a jump (e.g. -0.001 = -0.1%)
    jump_std: float             # how much jump sizes vary
    shock_frac: float | None    # when the shock hits, as a share of the run (None = no shock)
    shock_return: float         # size of the shock, e.g. -0.06 = a 6% drop
    bg_depth_btc: float         # background liquidity per side, in BTC
    bg_half_spread: float       # background quotes sit this far from the centre
    bg_walkaway: bool           # background liquidity retreats when prices move fast

    # Q2 fix: historical path replay
    use_replay: bool = False   # if True, replay real BTC price path instead of GBM
    replay_regime: str = ""    # which replay path to use ("normal" or "stress")

    def shock_step(self, n_steps: int) -> int | None:
        if self.use_replay:
            return None
        if self.shock_frac is None:
            return None
        return max(1, round(self.shock_frac * n_steps))
