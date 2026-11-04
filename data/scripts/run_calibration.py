"""
Calibrate the perp_sim simulator against real-data targets.

Reads:
  data/processed/calibration_targets.json — real-data statistics

Does:
  1. Runs the simulator with scripted bots at various parameter values
  2. Computes the same statistics from simulation output
  3. Grid-searches for the best parameter combination
  4. Writes calibrated values to:
     - perp_sim/engine/calibrated_config.py (calibrated SimConfig defaults)
     - perp_sim/engine/calibrated_scenarios.py (calibrated scenario values)
     - data/processed/calibration_result.json (before/after comparison)

Key design decisions:
  - Mini-market scaling: agents have $1000 collateral each. The bg_depth
    must be small enough that agent trades can actually move the price
    (otherwise sigma_hourly=0 in the sim). We target the RATIO of agent
    position size to bg depth, not the absolute depth.
  - Real depth reference: Hyperliquid live snapshot (~108 BTC within 0.5%)
    is the right target for the sim's depth_band=0.005. Binance bookDepth
    (~50k BTC) is aggregate across all percentage bands and not comparable.
  - Sigma is primarily driven by the index price process (GBM), so the
    sigma_hourly parameter directly controls realized volatility. We
    search around the real-data target.
  - Jump parameters are taken directly from real data (no grid search
    needed — they are inputs to the index process, not emergent).

Usage:
  python run_calibration.py
  python run_calibration.py --quick   # fewer iterations, faster
"""

import os
import sys
import json
import math
import time
from itertools import product

import numpy as np

# ---------------------------------------------------------------------------
# Paths — make perp_sim importable
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", ".."))
PERP_SIM_DIR = os.path.join(REPO_ROOT, "perp_sim")
PROCESSED_DIR = os.path.normpath(os.path.join(REPO_ROOT, "data", "processed"))

sys.path.insert(0, PERP_SIM_DIR)

from engine import SimConfig, Exchange, SCENARIOS, write_csv
from engine.config import Scenario
from bots import CrowdBot, TrendBot, ContrarianBot, ValueBot, MakerBot

# ---------------------------------------------------------------------------
# Load calibration targets
# ---------------------------------------------------------------------------

def load_targets():
    path = os.path.join(PROCESSED_DIR, "calibration_targets.json")
    if not os.path.exists(path):
        print("[error] calibration_targets.json not found.")
        print("Run download_data.py then compute_calibration_targets.py first.")
        sys.exit(1)
    with open(path) as f:
        return json.load(f)

# ---------------------------------------------------------------------------
# Mini-market scaling
# ---------------------------------------------------------------------------
#
# The mini-market has:
#   - 50 bots, each with $1000 collateral
#   - BTC price ~$100,000
#   - At 10x leverage, each bot can hold ~0.1 BTC
#   - Total possible position: ~5 BTC
#
# For agents to move the price, bg_depth must be in the same order of
# magnitude as total agent positions. We use the Hyperliquid reference
# (~108 BTC) as the real-world depth within 0.5%, but SCALE IT DOWN
# to match the mini-market.
#
# Scaling factor: if real market has ~100,000 traders and ~108 BTC depth,
# and our mini-market has 50 traders, the proportional depth is:
#   108 * (50 / 100000) ≈ 0.054 BTC
#
# But that's too thin — agents would cascade immediately. We use a range
# around this: 0.5 to 5.0 BTC, which gives meaningful but not overwhelming
# depth relative to agent positions.
#
# The thick/thin distinction:
#   thick = 2.0 BTC (agents have moderate impact)
#   thin  = 0.5 BTC (agents have large impact, cascades possible)
#
# We grid-search bg_depth in this range to find the best fit.

# Depth grid for mini-market (in BTC, per side)
MINI_MARKET_DEPTH_GRID = [0.5, 1.0, 2.0, 4.0, 8.0]

# ---------------------------------------------------------------------------
# Bot population
# ---------------------------------------------------------------------------

CROWD_LEVERAGE = [6, 10, 14, 18]

def build_population(seed, n_total=50):
    """Build a realistic bot population for the mini-market."""
    bots = []
    # 40% crowd bots
    n_crowd = int(n_total * 0.4)
    for i in range(n_crowd):
        lev = CROWD_LEVERAGE[i % len(CROWD_LEVERAGE)]
        bots.append(CrowdBot(f"crowd{i}", seed, lev, act_prob=0.7))
    
    # 20% trend bots
    n_trend = int(n_total * 0.2)
    for i in range(n_trend):
        bots.append(TrendBot(f"trend{i}", seed, leverage=4 + i % 3, act_prob=0.5))
    
    # 15% contrarian
    n_contra = int(n_total * 0.15)
    for i in range(n_contra):
        bots.append(ContrarianBot(f"contrarian{i}", seed, act_prob=0.5))
    
    # 15% value
    n_value = int(n_total * 0.15)
    for i in range(n_value):
        bots.append(ValueBot(f"value{i}", seed, act_prob=0.5))
    
    # 10% maker
    n_maker = n_total - len(bots)
    for i in range(max(1, n_maker)):
        bots.append(MakerBot(f"maker{i}", seed))
    
    return bots


# ---------------------------------------------------------------------------
# Run one simulation and compute stats
# ---------------------------------------------------------------------------

def run_one(scenario, seed=42, n_steps=600, n_bots=50):
    """Run one simulation and return statistics."""
    cfg = SimConfig(
        n_steps=n_steps,
        step_minutes=1.0,
        seed=seed,
        initial_collateral=1000.0,  # mini-market
        maintenance_margin_rate=0.01,  # calibrated to Hyperliquid
        funding_cap_per_hour=1.25e-05,  # from real data
    )
    
    exchange = Exchange(cfg, scenario)
    bots = build_population(seed, n_total=n_bots)
    for bot in bots:
        exchange.add_account(bot.account_id)
    
    for _ in range(n_steps):
        actions = []
        for bot in bots:
            actions += bot.act(exchange.view(bot.account_id))
        exchange.step(actions)
    
    return compute_sim_stats(exchange)


def compute_sim_stats(exchange):
    """Compute the same statistics we extract from real data."""
    recs = exchange.records
    prices = [exchange.cfg.initial_price] + [r.last_price for r in recs]
    
    # Minute returns
    returns = np.diff(np.log(prices))
    
    if len(returns) < 10:
        return {"sigma_hourly": 0, "jump_rate_per_hour": 0, "jump_mean": 0,
                "jump_std": 0, "return_kurtosis": 0, "spread_rel": 0,
                "depth_btc": 0, "shock_return": 0}
    
    # Hourly volatility
    hourly_vols = []
    for i in range(0, len(returns), 60):
        chunk = returns[i:i+60]
        if len(chunk) >= 30:
            hourly_vols.append(np.std(chunk) * math.sqrt(60))
    
    sigma_hourly = np.median(hourly_vols) if hourly_vols else float(np.std(returns) * math.sqrt(60))
    
    # Jumps (returns beyond 3 sigma)
    std_r = np.std(returns)
    jumps = returns[np.abs(returns) > 3 * std_r] if std_r > 0 else np.array([])
    hours = len(returns) / 60.0
    jump_rate = len(jumps) / hours if hours > 0 else 0
    
    neg_jumps = jumps[jumps < 0]
    jump_mean = float(np.mean(neg_jumps)) if len(neg_jumps) > 0 else 0.0
    jump_std = float(np.std(neg_jumps)) if len(neg_jumps) > 1 else 0.0
    
    # Kurtosis (excess)
    kurtosis = float(excess_kurtosis(returns))
    
    # Spread (median relative spread from records)
    spreads = [r.spread_rel for r in recs if r.spread_rel > 0]
    spread_rel = float(np.median(spreads)) if spreads else 0.0001
    
    # Depth (median total depth within depth_band from records)
    depths = [r.depth_bid + r.depth_ask for r in recs]
    depth_btc = float(np.median(depths)) if depths else 0.0
    
    # Shock (max drawdown)
    cum = np.array(prices)
    peak = np.maximum.accumulate(cum)
    drawdown = (cum - peak) / peak
    shock = float(np.min(drawdown))
    
    return {
        "sigma_hourly": float(sigma_hourly),
        "jump_rate_per_hour": float(jump_rate),
        "jump_mean": jump_mean,
        "jump_std": jump_std,
        "return_kurtosis": kurtosis,
        "spread_rel": spread_rel,
        "depth_btc": depth_btc,
        "shock_return": shock,
    }


def excess_kurtosis(arr):
    """Compute excess kurtosis without pandas dependency."""
    arr = np.array(arr)
    n = len(arr)
    if n < 4:
        return 0.0
    mean = np.mean(arr)
    std = np.std(arr, ddof=1)
    if std == 0:
        return 0.0
    return float((1/n) * np.sum(((arr - mean) / std) ** 4) - 3.0)


# ---------------------------------------------------------------------------
# Calibration: grid search
# ---------------------------------------------------------------------------

def calibrate_regime(targets, regime, quick=False):
    """Calibrate parameters for one regime (normal or stress).
    
    Grid search over the parameters that have the most impact on emergent
    sim statistics:
      - sigma_hourly: directly controls index price volatility
      - bg_depth_btc: controls how much agent trades move prices
      - bg_half_spread: controls the bid-ask spread
    
    Jump parameters are taken directly from real data (they are inputs to
    the index price process, not emergent from trading).
    """
    target = targets[regime]
    print(f"\n--- Calibrating {regime} regime ---")
    print(f"  Real-data targets:")
    print(f"    sigma_hourly:    {target['sigma_hourly']:.6f}")
    print(f"    jump_rate/hour:  {target['jump_rate_per_hour']:.3f}")
    print(f"    spread_rel:      {target['spread_rel']:.6f}")
    print(f"    depth_btc:       {target['depth_btc']:.1f} (Binance aggregate)")
    print(f"    shock_return:    {target['shock_return']:.4f}")
    
    hl_ref = targets.get("hyperliquid_reference", {})
    hl_depth = hl_ref.get("depth_total_05pct", 108.26)
    print(f"    Hyperliquid ref: {hl_depth:.1f} BTC within 0.5%")
    
    # --- Sigma grid ---
    # sigma_hourly in the scenario directly drives the index price GBM.
    # Realized sim volatility ≈ index sigma + agent-driven noise.
    # Search around the real-data target.
    sigma_grid = [target["sigma_hourly"] * f for f in [0.8, 0.9, 1.0, 1.1, 1.2]]
    
    # --- Depth grid ---
    # Mini-market depth: must be small enough that 50 agents with $1000 each
    # can meaningfully move prices. Total agent capacity ~5 BTC at 10x lev.
    # We search 0.5 to 8.0 BTC per side.
    depth_grid = MINI_MARKET_DEPTH_GRID
    
    # --- Spread grid ---
    # bg_half_spread controls the bid-ask spread. Real spread is 0.0002.
    spread_grid = [target["spread_rel"] * f for f in [0.5, 1.0, 2.0, 4.0]]
    
    if quick:
        sigma_grid = [target["sigma_hourly"]]
        depth_grid = [2.0, 4.0]
        spread_grid = [target["spread_rel"]]
    
    n_combos = len(sigma_grid) * len(depth_grid) * len(spread_grid)
    print(f"  Testing {n_combos} parameter combinations...")
    print(f"    sigma:   {[f'{s:.6f}' for s in sigma_grid]}")
    print(f"    depth:   {depth_grid}")
    print(f"    spread:  {[f'{s:.6f}' for s in spread_grid]}")
    
    best_fit = None
    best_params = None
    best_stats = None
    
    t0 = time.time()
    for idx, (sigma, depth, spread) in enumerate(product(sigma_grid, depth_grid, spread_grid)):
        # Build scenario with these params
        walkaway = (regime == "stress")
        shock_ret = target.get("shock_return", -0.06)
        
        scenario = Scenario(
            name=f"calib-{regime}",
            regime=regime,
            sigma_hourly=sigma,
            jump_rate_per_hour=target.get("jump_rate_per_hour", 0.7),
            jump_mean=target.get("jump_mean", -0.001),
            jump_std=target.get("jump_std", 0.0003),
            shock_frac=0.35,
            shock_return=shock_ret,
            bg_depth_btc=depth,
            bg_half_spread=spread,
            bg_walkaway=walkaway,
        )
        
        stats = run_one(scenario, seed=42, n_steps=600, n_bots=50)
        
        # Fit score: weighted sum of relative errors
        # We compare sim stats to real-data targets for the metrics that
        # are meaningfully comparable in the mini-market.
        #
        # sigma_hourly: comparable directly (it's a rate, not a level)
        # jump_rate: comparable directly (it's a frequency)
        # spread_rel: comparable directly (it's a ratio)
        # shock_return: comparable directly (it's a return)
        # depth_btc: NOT directly comparable (mini-market vs real market)
        #   → we check that depth is > 0 and reasonable, but don't match absolute
        #
        # For depth, we use a softer penalty: if sim depth > 0, good.
        # If depth = 0, penalize heavily (nothing is trading).
        
        fit = 0.0
        weights = {
            "sigma_hourly": 3.0,      # most important
            "jump_rate_per_hour": 1.0,
            "spread_rel": 2.0,
            "shock_return": 1.5,
        }
        for key, w in weights.items():
            t_val = abs(target.get(key, 0.001))
            s_val = abs(stats.get(key, 0.001))
            if t_val > 0:
                rel_err = abs(s_val - t_val) / t_val
                fit += w * rel_err
        
        # Depth penalty: if nothing is trading (depth=0 or sigma=0), penalize
        if stats["sigma_hourly"] < 0.0001:
            fit += 100  # severe penalty for dead market
        if stats["depth_btc"] < 0.001:
            fit += 50
        
        # Kurtosis bonus: real BTC has fat tails. Reward positive excess kurtosis.
        if stats["return_kurtosis"] > 0:
            fit -= 0.5  # small bonus
        
        elapsed = time.time() - t0
        print(f"    [{idx+1}/{n_combos}] sigma={sigma:.6f} depth={depth:.1f} spread={spread:.6f} "
              f"→ fit={fit:.4f} (sim_sigma={stats['sigma_hourly']:.6f}) [{elapsed:.1f}s]")
        
        if best_fit is None or fit < best_fit:
            best_fit = fit
            best_params = {
                "sigma_hourly": sigma,
                "jump_rate_per_hour": target.get("jump_rate_per_hour", 0.7),
                "jump_mean": target.get("jump_mean", -0.001),
                "jump_std": target.get("jump_std", 0.0003),
                "shock_return": shock_ret,
                "bg_depth_btc": depth,
                "bg_half_spread": spread,
                "bg_walkaway": walkaway,
            }
            best_stats = stats
    
    print(f"\n  Best fit score: {best_fit:.4f} (lower = better)")
    print(f"  Best params:")
    for k, v in best_params.items():
        print(f"    {k}: {v}")
    print(f"\n  Sim stats vs targets:")
    print(f"    {'metric':<22} {'target':>14} {'sim':>14} {'ratio':>8}")
    for key in ["sigma_hourly", "jump_rate_per_hour", "spread_rel", "shock_return"]:
        t = target.get(key, 0)
        s = best_stats.get(key, 0)
        ratio = f"{s/t:.2f}" if t != 0 else "N/A"
        print(f"    {key:<22} {t:>14.6f} {s:>14.6f} {ratio:>8}")
    print(f"    {'return_kurtosis':<22} {'(info)':>14} {best_stats.get('return_kurtosis', 0):>14.2f} {'':>8}")
    print(f"    {'depth_btc (sim)':<22} {'(mini)':>14} {best_stats.get('depth_btc', 0):>14.4f} {'':>8}")
    
    return best_params, best_stats, best_fit


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="Fewer grid combinations")
    args = parser.parse_args()
    
    targets = load_targets()
    
    print("=" * 60)
    print("Simulator Calibration")
    print("=" * 60)
    print(f"Mode: {'quick' if args.quick else 'full'}")
    
    # Calibrate both regimes
    normal_params, normal_stats, normal_fit = calibrate_regime(targets, "normal", quick=args.quick)
    stress_params, stress_stats, stress_fit = calibrate_regime(targets, "stress", quick=args.quick)
    
    # Funding (directly from targets, no search needed)
    funding = targets.get("funding", {})
    funding_params = {
        "funding_scale": 1.0 / 8.0,
        "funding_interest_per_hour": funding.get("funding_mean", 0.0000125),
        "funding_cap_per_hour": funding.get("funding_cap", 1.25e-05),
    }
    
    # --- Write calibrated config ---
    config_path = os.path.join(PERP_SIM_DIR, "engine", "calibrated_config.py")
    with open(config_path, "w") as f:
        f.write('"""\nCalibrated simulator parameters.\n')
        f.write('Generated by data/scripts/run_calibration.py.\n')
        f.write('These values replace the placeholders in config.py.\n')
        f.write('\nCalibration data sources:\n')
        f.write('  - Binance Vision aggTrades (6 days: 3 normal, 3 stress)\n')
        f.write('  - Binance Vision bookDepth (6 days, percentage-band format)\n')
        f.write('  - Hyperliquid API (funding history + live L2 orderbook)\n')
        f.write('"""\n\n')
        f.write('from dataclasses import dataclass\n\n')
        f.write('@dataclass(frozen=True)\nclass CalibratedSimConfig:\n')
        f.write(f'    initial_price: float = 100_000.0\n')
        f.write(f'    initial_collateral: float = 1000.0  # mini-market scaling\n')
        f.write(f'    max_leverage: float = 20.0\n')
        f.write(f'    maintenance_margin_rate: float = 0.01  # Hyperliquid-calibrated\n')
        f.write(f'    taker_fee: float = 0.00045\n')
        f.write(f'    maker_fee: float = 0.00015\n')
        f.write(f'    funding_scale: float = {funding_params["funding_scale"]}\n')
        f.write(f'    funding_interest_per_hour: float = {funding_params["funding_interest_per_hour"]:.10f}\n')
        f.write(f'    funding_cap_per_hour: float = {funding_params["funding_cap_per_hour"]}\n')
        f.write(f'    bg_anchor_strength: float = 0.2\n')
        f.write(f'    bg_levels: int = 5\n')
        f.write(f'    bg_level_gap: float = 0.003\n')
    
    # --- Write calibrated scenarios ---
    scenarios_path = os.path.join(PERP_SIM_DIR, "engine", "calibrated_scenarios.py")
    with open(scenarios_path, "w") as f:
        f.write('"""\nCalibrated scenario parameters.\n')
        f.write('Generated by data/scripts/run_calibration.py.\n')
        f.write('These values replace the placeholders in scenarios.py.\n')
        f.write('\nReal-data sources:\n')
        f.write('  Normal regime: 3 days Sep 2025 (Binance BTC-USDT perp)\n')
        f.write('  Stress regime: Oct 9-10 2025 crash + Aug 25 2025 drop\n')
        f.write('  Depth: mini-market scaled (see calibration report)\n')
        f.write('"""\n\n')
        f.write('from .config import Scenario\n\n')
        f.write('_REGIMES = {\n')
        f.write('    "normal": dict(\n')
        for k, v in normal_params.items():
            if k not in ("bg_walkaway",):
                f.write(f'        {k}={v},\n')
        f.write(f'        bg_walkaway={normal_params.get("bg_walkaway", False)},\n')
        f.write('    ),\n')
        f.write('    "stress": dict(\n')
        for k, v in stress_params.items():
            if k not in ("bg_walkaway",):
                f.write(f'        {k}={v},\n')
        f.write(f'        bg_walkaway={stress_params.get("bg_walkaway", True)},\n')
        f.write('    ),\n')
        f.write('}\n\n')
        
        # Depth: thick = 1.5x calibrated normal depth, thin = 0.5x
        normal_depth = normal_params.get("bg_depth_btc", 2.0)
        stress_depth = stress_params.get("bg_depth_btc", 1.0)
        thick_depth = max(normal_depth * 1.5, 1.0)
        thin_depth = max(stress_depth * 0.5, 0.3)
        f.write(f'_DEPTH_BTC = {{"thick": {thick_depth:.4f}, "thin": {thin_depth:.4f}}}\n')
        f.write('_TIMING = {"early": 0.35, "late": 0.65}\n\n')
        
        f.write('def build_calibrated_scenarios() -> list:\n')
        f.write('    scenarios = []\n')
        f.write('    for regime, params in _REGIMES.items():\n')
        f.write('        for depth_name, depth in _DEPTH_BTC.items():\n')
        f.write('            for timing_name, frac in _TIMING.items():\n')
        f.write('                scenarios.append(Scenario(\n')
        f.write('                    name=f"{regime}-{depth_name}-{timing_name}",\n')
        f.write('                    regime=regime, shock_frac=frac,\n')
        f.write('                    bg_depth_btc=depth, **params,\n')
        f.write('                ))\n')
        f.write('    return scenarios\n\n')
        f.write('CALIBRATED_SCENARIOS = {s.name: s for s in build_calibrated_scenarios()}\n')
    
    # --- Write comparison report ---
    hl_ref = targets.get("hyperliquid_reference", {})
    result = {
        "_meta": {
            "mode": "quick" if args.quick else "full",
            "n_combos_per_regime": "varies",
            "n_steps": 600,
            "n_bots": 50,
            "collateral_per_bot": 1000,
            "calibration_data": "Binance Vision + Hyperliquid API",
            "calibration_date": time.strftime("%Y-%m-%d"),
        },
        "normal": {
            "target": targets["normal"],
            "simulated": normal_stats,
            "calibrated_params": normal_params,
            "fit_score": normal_fit,
        },
        "stress": {
            "target": targets["stress"],
            "simulated": stress_stats,
            "calibrated_params": stress_params,
            "fit_score": stress_fit,
        },
        "funding": funding_params,
        "hyperliquid_reference": hl_ref,
        "stylized_facts": targets.get("stylized_facts", {}),
        "notes": {
            "depth_scaling": "Mini-market: bg_depth scaled so 50 agents ($1000 each) can move prices. Real depth (Hyperliquid ~108 BTC) is proportionally reduced.",
            "sigma_matching": "sigma_hourly is the GBM parameter; realized sim vol ≈ sigma + agent noise. Grid searches around real-data target.",
            "jump_params": "Jump rate/mean/std taken directly from real data (inputs to index process, not emergent).",
        },
    }
    
    result_path = os.path.join(PROCESSED_DIR, "calibration_result.json")
    with open(result_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    
    print(f"\n{'=' * 60}")
    print("Calibration complete!")
    print(f"\nGenerated files:")
    print(f"  {config_path}")
    print(f"  {scenarios_path}")
    print(f"  {result_path}")
    print(f"\nNext step: run verify_stylized_facts.py to check the calibrated sim")


if __name__ == "__main__":
    main()
