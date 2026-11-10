"""
The 8 fixed scenarios (the project plan's "8 fixed environment scenarios
shared across all conditions").

v3 (post-feedback fixes) — calibrated 2026-11-10:
  Q1 fix: spread from Bybit real data (median 0.009 bps, not 2 bps artifact)
  Q1 fix: depth from Bybit near_depth (~123 BTC, not 50k BTC full-book)
  Q2 fix: stress scenarios use ReplayIndexPrice (historical path replay)
  Q4 fix: zero returns retained in all statistics
  Q6 fix: calibrated with 3 seeds; 8-day held-out validation

  v3 fix (Feedback #1): stress regime now uses the EXTREME Oct 10 crash path
    (12:00-22:00 UTC, max DD -15.47%, max 1-min -5.13%) instead of the gentle
    Oct 9 path (DD -1.77%). This ensures liquidations and cascades actually
    occur, which is essential for the core research question.

    The "stress" regime label now maps to the "extreme" replay path.
    The original Oct 9 path is retained as "moderate" for reference but
    not used in the main 8-scenario design.

Calibration result:
  Normal: best fit=19.08 (sigma=0.002334, depth=8.0 BTC, spread=3.5e-6)
  Stress:  best fit=34.90 (sigma=0.002567, depth=4.0 BTC, spread=3.3e-6)
    Stress replay: Oct 10 12:00 UTC (extreme crash, DD=-15.47%, max 1-min=-5.13%)

The 2 x 2 x 2 = 8 factorial design:
    market state     : normal | stress
    background depth : thick  | thin
    shock timing     : early (35% into the run) | late (65% into the run)
"""

from .config import Scenario

# v3 calibrated parameters (best-fit from multi-seed grid search)
# Sources: teammate's 22-day Bybit + Binance data (data/processed/calibration_targets.json)
_REGIMES = {
    "normal": dict(
        sigma_hourly=0.002334,          # calibrated best-fit (grid: 0.8-1.2x real target)
        jump_rate_per_hour=0.845,       # from real data (directly, not emergent)
        jump_mean=-0.001712,
        jump_std=0.000672,
        shock_return=-0.005346,         # Q2 fix: max 1-min return on normal days
        bg_walkaway=False,
        bg_half_spread=3.5e-06,         # Q1 fix: calibrated from real Bybit spread
        use_replay=False,               # normal: synthetic GBM is fine
        replay_regime="normal",
    ),
    "stress": dict(
        sigma_hourly=0.002567,          # calibrated best-fit
        jump_rate_per_hour=0.750,
        jump_mean=-0.001892,
        jump_std=0.000507,
        shock_return=-0.003086,         # Q2 fix: max 1-min return on stress days
        bg_walkaway=True,
        bg_half_spread=3.3e-06,         # Q1 fix: calibrated from real Bybit spread
        use_replay=True,                # Q2 fix: replay real crash path
        # v3 fix (Feedback #1): use "extreme" path (Oct 10 12:00 UTC)
        # This path has DD=-15.47% and max 1-min=-5.13%, which WILL trigger
        # liquidations at 15x+ leverage. The old "stress" path (Oct 9,
        # DD=-1.77%) was too gentle to produce any liquidations.
        replay_regime="extreme",
    ),
}

# Mini-market depth (calibrated best-fit from grid search)
# thick = 8.0 BTC (normal best-fit, moderate price impact)
# thin  = 4.0 BTC (stress best-fit, larger price impact, cascades possible)
_DEPTH_BTC = {"thick": 8.0, "thin": 4.0}
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
