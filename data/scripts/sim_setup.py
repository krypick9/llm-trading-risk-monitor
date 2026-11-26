"""Single source of truth for how the validation scripts build the simulator.

Why this file exists
  run_calibration.run_one, validate_microstructure_vs_real.py,
  validate_cascade_impact.py and verify_stylized_facts.py each built their own
  SimConfig and population, so they described different markets (flow count,
  volatility clustering and trader mix all differed). Every validation script
  now calls this module, and perp_sim/tests/test_script_config_alignment.py
  fails if any of them drifts from run_calibration.run_one.

The settings below copy run_calibration.run_one exactly:
  * background taker flow on, backstop liquidity provider on
  * 2.2 flow orders per minute (0.004 x 547 real trades/min)
  * AR(1) index volatility clustering on
  * reference population (trend / contrarian / value / maker), no noise bots
    and no crowd bots, because the flow supplies the aggressive demand

FLOW_ORDERS_PER_MIN is an ASSUMPTION. engine/config.py defaults to 80 per
minute because Poisson zeros and large orders otherwise leave empty minutes;
that choice was made to fix flat minutes, which is also a validation statistic.
Use data/scripts/sensitivity_flow_count.py to report both values.
"""
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", ".."))
PERP_SIM_DIR = os.path.join(REPO_ROOT, "perp_sim")
if PERP_SIM_DIR not in sys.path:
    sys.path.insert(0, PERP_SIM_DIR)

from engine import SimConfig, Exchange          # noqa: E402
from bots import build_reference_population    # noqa: E402

FLOW_ORDERS_PER_MIN = 2.2    # ASSUMPTION: scaled real count; the engine default is 80
N_BOTS = 50
N_STEPS = 600


def make_config(seed, n_steps=N_STEPS, n_bots=N_BOTS, flow_orders_per_min=FLOW_ORDERS_PER_MIN):
    return SimConfig(
        n_steps=n_steps,
        step_minutes=1.0,
        seed=seed,
        initial_collateral=1000.0,
        total_trader_collateral_budget=n_bots * 1000.0,
        maintenance_margin_rate=0.01,
        funding_cap_per_hour=1.25e-05,
        bg_flow_enabled=True,
        bg_backstop_only=True,
        bg_flow_orders_per_min=flow_orders_per_min,
        index_vol_clustering=True,
    )


def make_population(seed, n_bots=N_BOTS):
    return build_reference_population(seed, n_total=n_bots, include_crowd=False)


def build_exchange(scenario, seed, n_steps=N_STEPS, n_bots=N_BOTS,
                   flow_orders_per_min=FLOW_ORDERS_PER_MIN):
    cfg = make_config(seed, n_steps, n_bots, flow_orders_per_min)
    exchange = Exchange(cfg, scenario)
    bots = make_population(seed, n_bots)
    for bot in bots:
        exchange.add_account(bot.account_id)
    return exchange, bots, cfg


def run(scenario, seed, n_steps=N_STEPS, n_bots=N_BOTS,
        flow_orders_per_min=FLOW_ORDERS_PER_MIN):
    """Run one simulation; returns (exchange, cfg)."""
    exchange, bots, cfg = build_exchange(scenario, seed, n_steps, n_bots, flow_orders_per_min)
    for _ in range(n_steps):
        actions = []
        for bot in bots:
            actions += bot.act(exchange.view(bot.account_id))
        exchange.step(actions)
    return exchange, cfg


def describe(n_bots=N_BOTS, flow_orders_per_min=FLOW_ORDERS_PER_MIN):
    """Environment settings to store next to every validation output."""
    roles = {}
    for bot in make_population(0, n_bots):
        roles[bot.kind] = roles.get(bot.kind, 0) + 1
    return {
        "source": "data/scripts/sim_setup.py (mirrors run_calibration.run_one)",
        "bg_flow_enabled": True,
        "bg_backstop_only": True,
        "bg_flow_orders_per_min": flow_orders_per_min,
        "flow_orders_per_min_is_assumption": True,
        "index_vol_clustering": True,
        "population": "build_reference_population, no noise bots, no crowd bots",
        "role_counts": roles,
        "n_bots": n_bots,
        "n_steps": N_STEPS,
    }
