"""Pre-registered market scenarios.

The candidate matrix crosses two regimes, two liquidity scales, and three
price paths: synthetic no-event, synthetic scheduled-event, and historical
replay. Replay paths are fixed data and do not receive scheduled shocks.
"""

from .config import Scenario

_REGIMES = {
    "normal": dict(
        sigma_hourly=0.002602,
        jump_rate_per_hour=0.845,
        jump_mean=-0.001712,
        jump_std=0.000672,
        shock_return=-0.005346,
        bg_walkaway=False,
        bg_half_spread=8.765756414142713e-07,
        use_replay=False,
        replay_regime="normal",
    ),
    "stress": dict(
        sigma_hourly=0.004271,
        jump_rate_per_hour=0.750,
        jump_mean=-0.001892,
        jump_std=0.000507,
        shock_return=-0.005047,
        bg_walkaway=True,
        bg_half_spread=8.203060597605061e-07,
        use_replay=False,
        replay_regime="stress",
    ),
}

_DEPTH_BTC = {"thick": 8.0, "thin": 4.0}
_PATHS = {
    "synthetic-no-event": (None, False, ""),
    "synthetic-event": (0.5, False, ""),
    "replay": (None, True, "extreme"),
}


def build_scenarios() -> list[Scenario]:
    scenarios = []
    for regime, params in _REGIMES.items():
        for depth_name, depth in _DEPTH_BTC.items():
            for path_name, (shock_frac, use_replay, replay_regime) in _PATHS.items():
                scenarios.append(Scenario(
                    name=f"{regime}-{depth_name}-{path_name}",
                    regime=regime,
                    shock_frac=shock_frac,
                    use_replay=use_replay,
                    replay_regime=replay_regime or params["replay_regime"],
                    bg_depth_btc=depth,
                    **{k: v for k, v in params.items()
                       if k not in {"use_replay", "replay_regime"}},
                ))
    return scenarios


SCENARIOS = {scenario.name: scenario for scenario in build_scenarios()}
