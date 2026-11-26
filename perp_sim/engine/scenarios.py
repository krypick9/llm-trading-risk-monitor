"""Three fixed scenarios: `normal`, `stress` and `replay-extreme`.

These are CHOSEN, reasonable settings, not calibrated fits. The Phase-0 matrix
(2 regimes x 2 depths x 3 paths = 12 scenarios) is collapsed to the three the
experiment actually needs: a calm baseline, a volatile one in which the LP
retreats, and one replay of a real crash day. Depth is a single fixed
mini-market value per scenario.

sigma/jump values are in the right ballpark for 1-minute BTC but are not claimed
to match any specific day. bg_half_spread is a HALF-spread, so the quoted spread
is twice it; it is set so the quoted spread lands near the ~1-tick spread real
BTCUSDT shows (8.8e-7 relative at a $100k price), not fitted.
"""

from .config import Scenario

SCENARIOS = {
    "normal": Scenario(
        name="normal",
        regime="normal",
        sigma_hourly=0.0026,
        jump_rate_per_hour=0.85,
        jump_mean=-0.0017,
        jump_std=0.0007,
        bg_depth_btc=0.5,
        bg_half_spread=4.4e-07,   # half-spread -> ~1 tick quoted spread
        bg_walkaway=False,
        vol_cluster_rho=0.95,
        vol_cluster_vov=0.7,
    ),
    "stress": Scenario(
        name="stress",
        regime="stress",
        sigma_hourly=0.0043,
        jump_rate_per_hour=0.75,
        jump_mean=-0.0019,
        jump_std=0.0005,
        bg_depth_btc=0.5,
        bg_half_spread=4.4e-07,   # half-spread -> ~1 tick quoted spread
        bg_walkaway=True,
        vol_cluster_rho=0.97,
        vol_cluster_vov=0.9,
        # optional endogenous-stress trigger: one scheduled -5% index shock
        # near the middle of the run. Set shock_frac=None to disable.
        shock_frac=0.5,
        shock_return=-0.05,
    ),
    # A real crash day instead of a scheduled synthetic shock. Restored after
    # review: this is the project's only empirical stress event.
    #
    # The stored path is 600 minutes of 2025-10-10 and the worst minute is 558
    # (-5.13%), i.e. near the END of the window. Starting at minute 300 puts the
    # crash about 255 minutes into a 299-step run, which leaves some run before
    # and after it. Note the stored data stop ~35 minutes after the worst
    # minute, so post-crash recovery is truncated -- extending the path is a
    # data task, not a model one.
    #
    # Run it with a length that fits: n_steps <= 599 - replay_offset.
    #   python run.py --scenario replay-extreme --steps 299
    "replay-extreme": Scenario(
        name="replay-extreme",
        regime="stress",
        # sigma_hourly is still used by the flow's volatility-excitement
        # normaliser and the LP walkaway test, so it keeps a stress value even
        # though the index path itself comes from data.
        sigma_hourly=0.0043,
        jump_rate_per_hour=0.0,      # the real path carries its own jumps
        jump_mean=0.0,
        jump_std=0.0,
        bg_depth_btc=0.5,
        bg_half_spread=4.4e-07,
        bg_walkaway=True,
        use_replay=True,
        replay_regime="extreme",
        replay_offset=300,
    ),
}


def get_scenario(name: str) -> Scenario:
    if name not in SCENARIOS:
        raise ValueError(f"unknown scenario {name!r}; choose from {list(SCENARIOS)}")
    return SCENARIOS[name]
