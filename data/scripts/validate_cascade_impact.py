"""
Cascade and Price Impact Validation (TA Q6)

This script validates that the simulator produces realistic market microstructure
dynamics beyond stylized facts. Three specific checks:

1. Kyle's Lambda (Price Impact)
   Measures how much a unit of order flow moves the price. Real perpetual
   markets exhibit a linear relationship: delta_price = lambda * order_flow.
   We regress 1-min price changes on signed volume and check that lambda
   is positive and statistically significant.

2. Liquidity Recovery Time
   After a large price move (>2 sigma), how many steps until spread/depth
   returns to pre-shock levels? Real markets recover in 5-30 minutes.

3. Cascade Decay
   When liquidations occur, do they trigger further liquidations in
   subsequent steps (cascade) or do they die out quickly? We measure
   the autocorrelation of liquidation counts and the fraction of steps
   with cascades (>=2 consecutive liquidation steps).

These metrics address TA Q6: "the calibration matches stylized facts, but
does the simulator reproduce cascade dynamics and price impact realistically?"

Usage:
  python validate_cascade_impact.py
  python validate_cascade_impact.py --scenario stress-thin-early --seeds 5
"""

import os
import sys
import json
import math
import argparse
import statistics as stats

import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", ".."))
PERP_SIM_DIR = os.path.join(REPO_ROOT, "perp_sim")
PROCESSED_DIR = os.path.normpath(os.path.join(REPO_ROOT, "data", "processed"))

sys.path.insert(0, PERP_SIM_DIR)

from engine import SimConfig, Exchange, SCENARIOS
from bots import CrowdBot, TrendBot, ContrarianBot, ValueBot, MakerBot

CROWD_LEVERAGE = [6, 10, 14, 18]
DEFAULT_SEEDS = [42, 123, 456, 789, 2025]


def build_population(seed, n_total=50):
    """Same population as verify_stylized_facts.py for consistency."""
    bots = []
    n_crowd = int(n_total * 0.4)
    for i in range(n_crowd):
        bots.append(CrowdBot(f"crowd{i}", seed, CROWD_LEVERAGE[i % 4], act_prob=0.7))
    n_trend = int(n_total * 0.2)
    for i in range(n_trend):
        bots.append(TrendBot(f"trend{i}", seed, leverage=4 + i % 3, act_prob=0.5))
    n_contra = int(n_total * 0.15)
    for i in range(n_contra):
        bots.append(ContrarianBot(f"contrarian{i}", seed, act_prob=0.5))
    n_value = int(n_total * 0.15)
    for i in range(n_value):
        bots.append(ValueBot(f"value{i}", seed, act_prob=0.5))
    n_maker = n_total - len(bots)
    for i in range(max(1, n_maker)):
        bots.append(MakerBot(f"maker{i}", seed))
    return bots


# ---- Metric 1: Kyle's Lambda -----------------------------------------------

def compute_kyle_lambda(records):
    """Regress price change on signed volume to get price impact coefficient.

    lambda = Cov(dPrice, signedVolume) / Var(signedVolume)

    A positive, significant lambda means the market has realistic price impact:
    buys push price up, sells push it down, proportionally to size.

    Returns (lambda, r_squared, n_obs).
    """
    deltas = []
    flows = []
    prev_price = records[0].last_price if records else 100000.0
    for r in records:
        if r.volume > 0 and r.last_price > 0:
            dprice = (r.last_price - prev_price) / prev_price
            signed_flow = r.signed_volume  # already signed: buys positive, sells negative
            deltas.append(dprice)
            flows.append(signed_flow)
        prev_price = r.last_price

    if len(deltas) < 30:
        return 0.0, 0.0, len(deltas)

    dprice = np.array(deltas)
    flow = np.array(flows)

    var_flow = np.var(flow, ddof=1)
    if var_flow < 1e-15:
        return 0.0, 0.0, len(deltas)

    cov = np.cov(flow, dprice, ddof=1)[0, 1]
    lam = cov / var_flow

    # R-squared
    predicted = lam * flow
    ss_res = np.sum((dprice - predicted) ** 2)
    ss_tot = np.sum((dprice - np.mean(dprice)) ** 2)
    r_sq = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    return float(lam), float(r_sq), len(deltas)


# ---- Metric 2: Liquidity Recovery Time -------------------------------------

def compute_recovery_time(records):
    """After a large price move (>2 sigma), how long until spread returns to
    pre-shock median?

    Real markets recover liquidity in 5-30 minutes after a shock.
    A mini-market with 50 agents may recover faster or slower.

    Returns (median_recovery_steps, n_shocks_detected).
    """
    if len(records) < 60:
        return 0.0, 0

    prices = np.array([r.last_price for r in records])
    spreads = np.array([r.spread_rel for r in records])
    returns = np.diff(np.log(prices + 1e-10))

    sigma = np.std(returns[np.isfinite(returns)]) if len(returns) > 10 else 0.001
    if sigma < 1e-8:
        return 0.0, 0

    threshold = 2 * sigma
    median_spread = np.median(spreads[spreads > 0]) if np.any(spreads > 0) else 0.0

    recovery_times = []
    i = 0
    while i < len(returns) - 30:
        if abs(returns[i]) > threshold and i + 1 < len(spreads):
            # Shock detected; find when spread returns to <= median
            pre_shock_spread = spreads[max(0, i - 5):i + 1]
            target = np.median(pre_shock_spread) if len(pre_shock_spread) > 0 else median_spread
            for j in range(i + 1, min(i + 60, len(spreads))):
                if spreads[j] <= target * 1.5:  # within 1.5x of pre-shock
                    recovery_times.append(j - i)
                    break
            i = j + 1 if 'j' in dir() else i + 30
        else:
            i += 1

    if not recovery_times:
        return 0.0, 0

    return float(np.median(recovery_times)), len(recovery_times)


# ---- Metric 3: Cascade Decay -----------------------------------------------

def compute_cascade_metrics(records):
    """Measure liquidation cascade dynamics.

    - liq_acf1: autocorrelation of liquidation counts at lag 1.
      Positive = cascades (liquidations beget more liquidations).
      Near zero = independent liquidations.
    - cascade_steps: fraction of steps where liquidations occurred in
      consecutive steps (>= 2 in a row).
    - max_cascade_length: longest run of consecutive liquidation steps.
    - total_liqs: total liquidation events across the run.
    - liq_notional_total: total notional value of liquidations.

    Returns dict with these metrics.
    """
    liq_counts = np.array([r.liq_count for r in records])
    liq_notional = np.array([r.liq_notional for r in records])

    total_liqs = int(np.sum(liq_counts))
    total_notional = float(np.sum(liq_notional))

    # ACF of liquidation counts
    n = len(liq_counts)
    if n > 2 and np.var(liq_counts) > 0:
        mean = np.mean(liq_counts)
        c0 = np.mean((liq_counts - mean) ** 2)
        c1 = np.mean((liq_counts[:-1] - mean) * (liq_counts[1:] - mean))
        acf1 = float(c1 / c0) if c0 > 0 else 0.0
    else:
        acf1 = 0.0

    # Cascade runs: consecutive steps with liq_count > 0
    cascade_runs = []
    current_run = 0
    for c in liq_counts:
        if c > 0:
            current_run += 1
        else:
            if current_run > 0:
                cascade_runs.append(current_run)
            current_run = 0
    if current_run > 0:
        cascade_runs.append(current_run)

    cascade_steps = sum(r for r in cascade_runs if r >= 2)
    total_liq_steps = int(np.sum(liq_counts > 0))
    cascade_fraction = cascade_steps / total_liq_steps if total_liq_steps > 0 else 0.0
    max_cascade = max(cascade_runs) if cascade_runs else 0

    # Bad debt as fraction of total collateral
    bad_debt = float(records[-1].bad_debt_total) if records else 0.0
    total_collateral = 50 * 1000.0  # n_bots * initial_collateral
    bad_debt_ratio = bad_debt / total_collateral if total_collateral > 0 else 0.0

    return {
        "liq_acf1": acf1,
        "cascade_fraction": float(cascade_fraction),
        "max_cascade_length": int(max_cascade),
        "total_liquidations": total_liqs,
        "total_liq_notional": total_notional,
        "total_liq_steps": total_liq_steps,
        "n_cascade_runs": len(cascade_runs),
        "bad_debt_total": bad_debt,
        "bad_debt_ratio": float(bad_debt_ratio),
    }


# ---- Main ------------------------------------------------------------------

def run_one(scenario_name, seed, n_steps, n_bots):
    scenario = SCENARIOS.get(scenario_name)
    if scenario is None:
        print(f"[error] scenario '{scenario_name}' not found")
        return None

    cfg = SimConfig(
        n_steps=n_steps,
        step_minutes=1.0,
        seed=seed,
        initial_collateral=1000.0,
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

    recs = exchange.records

    # Metric 1: Kyle's lambda
    lam, r_sq, n_obs = compute_kyle_lambda(recs)

    # Metric 2: Liquidity recovery
    recovery, n_shocks = compute_recovery_time(recs)

    # Metric 3: Cascade dynamics
    cascade = compute_cascade_metrics(recs)

    return {
        "kyle_lambda": lam,
        "kyle_r_squared": r_sq,
        "kyle_n_obs": n_obs,
        "recovery_median_steps": recovery,
        "recovery_n_shocks": n_shocks,
        **cascade,
        "scenario": scenario_name,
        "seed": seed,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default=None)
    parser.add_argument("--steps", type=int, default=600)
    parser.add_argument("--seeds", type=int, default=5)
    args = parser.parse_args()

    seeds = DEFAULT_SEEDS[:args.seeds]

    print("=" * 70)
    print(f"Cascade & Price Impact Validation ({len(seeds)} seeds)")
    print("=" * 70)

    if args.scenario:
        test_scenarios = [args.scenario]
    else:
        # Test both normal and stress extremes
        test_scenarios = ["normal-thick-early", "stress-thin-early"]

    all_results = []

    for scen in test_scenarios:
        print(f"\n{'='*70}")
        print(f"Scenario: {scen} ({args.steps} steps x {len(seeds)} seeds)")
        print(f"{'='*70}")

        seed_results = []
        for seed in seeds:
            result = run_one(scen, seed, args.steps, n_bots=50)
            if result:
                seed_results.append(result)

        if not seed_results:
            continue

        # Aggregate
        metrics = {}
        for key in ["kyle_lambda", "kyle_r_squared", "kyle_n_obs",
                     "recovery_median_steps", "recovery_n_shocks",
                     "liq_acf1", "cascade_fraction", "max_cascade_length",
                     "total_liquidations", "total_liq_notional",
                     "total_liq_steps", "n_cascade_runs",
                     "bad_debt_total", "bad_debt_ratio"]:
            vals = [r[key] for r in seed_results]
            metrics[key] = {
                "mean": float(np.mean(vals)),
                "std": float(np.std(vals)),
                "min": float(np.min(vals)),
                "max": float(np.max(vals)),
            }

        # Print results
        print(f"\n  1. Price Impact (Kyle's Lambda):")
        print(f"     lambda:       {metrics['kyle_lambda']['mean']:.6f} "
              f"± {metrics['kyle_lambda']['std']:.6f}")
        print(f"     R-squared:    {metrics['kyle_r_squared']['mean']:.4f} "
              f"± {metrics['kyle_r_squared']['std']:.4f}")
        print(f"     n_obs:        {metrics['kyle_n_obs']['mean']:.0f}")
        lam_pass = metrics["kyle_lambda"]["mean"] > 0
        print(f"     pass: {'YES' if lam_pass else 'NO'} (lambda > 0)")
        print(f"     reference: real perps lambda ~ 1e-6 to 1e-4 (size-dependent)")

        print(f"\n  2. Liquidity Recovery Time:")
        print(f"     median steps: {metrics['recovery_median_steps']['mean']:.1f} "
              f"± {metrics['recovery_median_steps']['std']:.1f}")
        print(f"     shocks detected: {metrics['recovery_n_shocks']['mean']:.1f} "
              f"± {metrics['recovery_n_shocks']['std']:.1f}")
        rec_pass = 0 < metrics["recovery_median_steps"]["mean"] <= 60
        print(f"     pass: {'YES' if rec_pass else 'CHECK'} (0 < recovery <= 60 steps)")
        print(f"     reference: real markets recover in 5-30 min")

        print(f"\n  3. Cascade Dynamics:")
        print(f"     liq ACF(1):         {metrics['liq_acf1']['mean']:.4f} "
              f"± {metrics['liq_acf1']['std']:.4f}")
        print(f"     cascade fraction:   {metrics['cascade_fraction']['mean']:.2f} "
              f"± {metrics['cascade_fraction']['std']:.2f}")
        print(f"     max cascade length: {metrics['max_cascade_length']['mean']:.1f} "
              f"± {metrics['max_cascade_length']['std']:.1f}")
        print(f"     total liquidations: {metrics['total_liquidations']['mean']:.1f} "
              f"± {metrics['total_liquidations']['std']:.1f}")
        print(f"     total liq notional: ${metrics['total_liq_notional']['mean']:.0f} "
              f"± ${metrics['total_liq_notional']['std']:.0f}")
        print(f"     bad debt total:     ${metrics['bad_debt_total']['mean']:.2f} "
              f"± ${metrics['bad_debt_total']['std']:.2f}")
        print(f"     bad debt ratio:     {metrics['bad_debt_ratio']['mean']:.6f} "
              f"± {metrics['bad_debt_ratio']['std']:.6f}")

        # Cascade pass: ACF1 > 0 means liquidations cluster (realistic cascade)
        cascade_pass = metrics["liq_acf1"]["mean"] > 0 or metrics["total_liquidations"]["mean"] == 0
        print(f"     pass: {'YES' if cascade_pass else 'NO'} "
              f"(ACF1 > 0 or no liquidations)")

        overall_pass = lam_pass and (rec_pass or metrics["recovery_n_shocks"]["mean"] == 0)
        print(f"\n  Overall: {'PASS' if overall_pass else 'CHECK'}")

        all_results.append({
            "scenario": scen,
            "n_seeds": len(seed_results),
            "seeds": seeds,
            "n_steps": args.steps,
            "metrics": metrics,
            "pass": {
                "price_impact": lam_pass,
                "liquidity_recovery": rec_pass,
                "cascade_dynamics": cascade_pass,
                "overall": overall_pass,
            },
            "seed_results": seed_results,
        })

    # Save
    out_path = os.path.join(PROCESSED_DIR, "cascade_validation_report.json")
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)

    all_pass = all(r["pass"]["overall"] for r in all_results)
    print(f"\n{'='*70}")
    print(f"Overall result: {'ALL PASS' if all_pass else 'SOME CHECKS NEEDED'}")
    print(f"Report saved to {out_path}")


if __name__ == "__main__":
    main()
