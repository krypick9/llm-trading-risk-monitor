"""
The 8 fixed scenarios (the project plan's "8 fixed environment scenarios
shared across all conditions").

Calibrated against real BTC perpetual futures data (Nov 2025):
  Normal regime: 3 days Sep 2025 (Binance BTC-USDT perp)
  Stress regime: Oct 9-10 2025 crash + Aug 25 2025 drop
  Depth: mini-market scaled (see calibration report)

The 2 x 2 x 2 = 8 factorial design:
    market state     : normal | stress
    background depth : thick  | thin
    shock timing     : early (35% into the run) | late (65% into the run)
Edit build_scenarios() to change the design; nothing else depends on it.

Note: bg_depth_btc values are mini-market scaled. In the real market,
BTC perp books have ~100+ BTC within 0.5% (Hyperliquid reference). In
our mini-market with 50 agents at $1000 each, depth is scaled so agents
can meaningfully move prices.
"""

from .config import Scenario

_REGIMES = {
    # Calibrated from real BTC data:
    # sigma_hourly, jumps/hour, jump mean, jump std, shock size, makers retreat?, half spread
    "normal": dict(sigma_hourly=0.002875, jump_rate_per_hour=0.708,
                   jump_mean=-0.0014, jump_std=0.000208,
                   shock_return=-0.0208, bg_walkaway=False,
                   bg_half_spread=0.0001),
    "stress": dict(sigma_hourly=0.003098, jump_rate_per_hour=0.792,
                   jump_mean=-0.0025, jump_std=0.000692,
                   shock_return=-0.1315, bg_walkaway=True,
                   bg_half_spread=0.0001),
}
# Mini-market depth: thick = 12 BTC, thin = 0.5 BTC per side
# (calibrated: stress regime uses 1.0; thick=1.5x normal, thin=0.5x stress)
_DEPTH_BTC = {"thick": 12.0, "thin": 0.5}
_TIMING = {"early": 0.35, "late": 0.65}


def build_scenarios() -> list:
    scenarios = []
    for regime, params in _REGIMES.items():
        for depth_name, depth in _DEPTH_BTC.items():
            for timing_name, frac in _TIMING.items():
                scenarios.append(Scenario(
                    name=f"{regime}-{depth_name}-{timing_name}",
                    regime=regime,
                    shock_frac=frac,
                    bg_depth_btc=depth,
                    **params,
                ))
    return scenarios


SCENARIOS = {s.name: s for s in build_scenarios()}
