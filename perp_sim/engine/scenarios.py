"""
The 8 fixed scenarios (the project plan's "8 fixed environment scenarios
shared across all conditions").

PLACEHOLDER DESIGN, needs team confirmation: the plan says "8 scenarios
(factorial design)" but not which factors. This builds 2 x 2 x 2 = 8 from:
    market state     : normal | stress
    background depth : thick  | thin
    shock timing     : early (35% into the run) | late (65% into the run)
Edit build_scenarios() to change the design; nothing else depends on it.
All numbers are placeholders until calibrated on real Level-2 data.
"""

from .config import Scenario

_REGIMES = {
    # sigma_hourly, jumps/hour, jump mean, jump std, shock size, makers retreat?, half spread
    "normal": dict(sigma_hourly=0.005, jump_rate_per_hour=0.2, jump_mean=0.0,
                   jump_std=0.002, shock_return=-0.008, bg_walkaway=False,
                   bg_half_spread=0.0001),
    "stress": dict(sigma_hourly=0.012, jump_rate_per_hour=1.0, jump_mean=-0.001,
                   jump_std=0.005, shock_return=-0.06, bg_walkaway=True,
                   bg_half_spread=0.0002),
}
_DEPTH_BTC = {"thick": 12.0, "thin": 5.0}
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
