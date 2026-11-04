"""
Settings for one simulation run.

Calibrated against real BTC perpetual futures data:
  - Binance Vision aggTrades + bookDepth (6 days: 3 normal, 3 stress)
  - Hyperliquid API (funding history + live L2 orderbook)
  - Calibration date: Nov 2025

The mini-market design (Decision #1, Section 3.6 of FYP plan):
  Agents have $1000 collateral each. Background depth is scaled so that
  50-100 agents can meaningfully move prices. Absolute numbers are smaller
  than real markets, but relative proportions (agent position vs book depth)
  are preserved, which is what drives cascade dynamics.

To scale up: change n_steps and step_minutes here. Nothing else in the
engine hard-codes a run length: funding timing, scenario shock timing
and the like all follow from these two numbers.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SimConfig:
    # ---- size of the run (small demo defaults; raise to scale) ------------
    n_steps: int = 120               # number of simulated steps in one run
    step_minutes: float = 1.0        # real time that one step stands for

    # ---- the market --------------------------------------------------------
    initial_price: float = 100_000.0  # roughly BTC-sized price
    tick_size: float = 0.5            # smallest price move (binary-exact on purpose)
    lot_size: float = 0.001           # smallest order size, in BTC

    # ---- accounts and leverage --------------------------------------------
    max_leverage: float = 20.0           # per-account cap (Phase 2 tests lowering it)
    maintenance_margin_rate: float = 0.01  # calibrated to Hyperliquid (~1%)
    initial_collateral: float = 1000.0     # mini-market: $1000 per trader

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
    #   bg_anchor_strength: 0 = ignores the reference price, 1 = snaps to it each step
    #   bg_levels x bg_level_gap: how far from the price the book's liquidity reaches.
    #     Default 5 x 0.30% = 1.5%. A book that reaches only 0.1% away cannot be
    #     pushed far by forced sales, however large (tested), so no cascade can form.
    bg_anchor_strength: float = 0.2
    bg_levels: int = 5                # how many price levels it quotes per side
    bg_level_gap: float = 0.003       # spacing between its levels (0.30%)

    # ---- measurement -------------------------------------------------------
    depth_band: float = 0.005         # "depth" = size within 0.5% of the mid price
    near_liq_buffer: float = 0.05     # an account is "near liquidation" below 5% buffer

    # ---- liquidation engine ------------------------------------------------
    liquidations_enabled: bool = True   # False = a counterfactual control: no forced closing
    max_liquidation_rounds: int = 25    # safety limit on chain reactions inside one step

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

    def shock_step(self, n_steps: int) -> int | None:
        if self.shock_frac is None:
            return None
        return max(1, round(self.shock_frac * n_steps))
