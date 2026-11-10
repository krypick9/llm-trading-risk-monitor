"""
Verify that the calibrated simulator produces realistic stylized facts.

v2 (post-TA fixes):
  Q4 fix: Zero returns are RETAINED. We report both variants (with and
          without zeros) so the reader can see the bias the old code
          introduced by dropping them.
  Q6 fix: Multi-seed validation (5 seeds instead of 1). We report the
          mean and std of each metric across seeds.
  Q6 fix: Held-out comparison. We compute the same stylized facts from
          the held-out 6-day period and compare sim vs real.

Stylized facts checked:
  1. Fat-tailed returns: excess kurtosis > 0
  2. Volatility clustering: ACF of squared returns at lag-1 > 0.1
  3. Order flow autocorrelation: ACF of OFI at lag-1 > 0

Usage:
  python verify_stylized_facts.py
  python verify_stylized_facts.py --scenario stress-thin-early
  python verify_stylized_facts.py --seeds 5
"""

import os
import sys
import json
import math
import argparse

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


def autocorrelation(x, lag=1):
    """Lag-k autocorrelation of a series."""
    x = np.array(x, dtype=float)
    n = len(x)
    if n <= lag:
        return 0.0
    mean = np.mean(x)
    c0 = np.mean((x - mean) ** 2)
    if c0 == 0:
        return 0.0
    ck = np.mean((x[:n-lag] - mean) * (x[lag:] - mean))
    return float(ck / c0)


def excess_kurtosis(x):
    """Excess kurtosis (>0 means fatter tails than normal)."""
    x = np.array(x, dtype=float)
    n = len(x)
    if n < 4:
        return 0.0
    mean = np.mean(x)
    std = np.std(x, ddof=1)
    if std == 0:
        return 0.0
    return float((1/n) * np.sum(((x - mean) / std) ** 4) - 3.0)


def run_one(scenario_name, seed, n_steps, n_bots):
    """Run one simulation and return statistics."""
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

    return compute_stats(exchange, cfg)


def compute_stats(exchange, cfg):
    """Compute stylized fact statistics from a simulation run.

    Q4 fix: report both with-zeros and without-zeros variants.
    """
    recs = exchange.records
    prices = [cfg.initial_price] + [r.last_price for r in recs]
    returns = np.diff(np.log(prices))

    # Q4 fix: keep all returns (including zeros and non-finite)
    finite_mask = np.isfinite(returns)
    returns_all = returns[finite_mask]       # keep zeros
    returns_nonzero = returns_all[returns_all != 0]  # also without zeros

    if len(returns_all) < 60:
        print(f"  [warn] only {len(returns_all)} usable returns")

    # --- Fat tails ---
    kurt_with = excess_kurtosis(returns_all)
    kurt_without = excess_kurtosis(returns_nonzero)

    # --- Volatility clustering ---
    sq_all = returns_all ** 2
    sq_nonzero = returns_nonzero ** 2
    acf1_all = autocorrelation(sq_all, 1)
    acf5_all = autocorrelation(sq_all, 5)
    acf10_all = autocorrelation(sq_all, 10)
    acf1_nonzero = autocorrelation(sq_nonzero, 1)

    # --- Order flow autocorrelation ---
    ofi_values = [r.order_flow_imbalance for r in recs if r.volume > 0]
    ofi_acf1 = autocorrelation(ofi_values, 1) if len(ofi_values) > 2 else 0.0

    # --- Additional stats ---
    sigma_hourly = float(np.std(returns_all) * math.sqrt(60))
    cum = np.array(prices)
    peak = np.maximum.accumulate(cum)
    drawdown = (cum - peak) / peak
    max_drawdown = float(np.min(drawdown))

    # Zero return stats
    n_zero = int(np.sum(returns_all == 0))
    zero_pct = n_zero / len(returns_all) * 100 if len(returns_all) > 0 else 0

    return {
        "n_returns": len(returns_all),
        "n_zero_returns": n_zero,
        "zero_return_pct": zero_pct,
        "sigma_hourly": sigma_hourly,
        "max_drawdown": max_drawdown,
        "kurtosis_with_zeros": float(kurt_with),
        "kurtosis_without_zeros": float(kurt_without),
        "vol_acf1_with_zeros": float(acf1_all),
        "vol_acf1_without_zeros": float(acf1_nonzero),
        "vol_acf5": float(acf5_all),
        "vol_acf10": float(acf10_all),
        "ofi_acf1": float(ofi_acf1),
    }


def load_held_out_targets():
    """Load held-out validation statistics from calibration targets."""
    path = os.path.join(PROCESSED_DIR, "calibration_targets.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        targets = json.load(f)
    return targets.get("held_out")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default=None, help="Test specific scenario")
    parser.add_argument("--steps", type=int, default=600)
    parser.add_argument("--seeds", type=int, default=5, help="Number of seeds (Q6 fix)")
    args = parser.parse_args()

    seeds = DEFAULT_SEEDS[:args.seeds]

    print("=" * 70)
    print(f"Stylized Facts Verification v2 ({len(seeds)} seeds)")
    print("=" * 70)

    # Test scenarios
    if args.scenario:
        test_scenarios = [args.scenario]
    else:
        test_scenarios = ["normal-thick-early", "stress-thin-early"]

    # Load held-out targets for comparison
    held_out = load_held_out_targets()
    if held_out:
        print(f"\n  Held-out validation data: {held_out.get('n_days', '?')} days")
        print(f"    sigma_hourly:       {held_out.get('sigma_hourly', 0):.6f}")
        print(f"    kurtosis (w/ zeros):{held_out.get('kurtosis_with_zeros', 0):.2f}")
        print(f"    vol_acf1 (w/ zeros):{held_out.get('vol_clustering_acf1_with_zeros', 0):.4f}")

    all_results = []

    for scen in test_scenarios:
        print(f"\n{'='*70}")
        print(f"Scenario: {scen} ({args.steps} steps × {len(seeds)} seeds)")
        print(f"{'='*70}")

        seed_results = []
        for seed in seeds:
            result = run_one(scen, seed, args.steps, n_bots=50)
            if result:
                result["scenario"] = scen
                result["seed"] = seed
                seed_results.append(result)

        if not seed_results:
            continue

        # Aggregate across seeds
        metrics = {}
        for key in ["kurtosis_with_zeros", "kurtosis_without_zeros",
                     "vol_acf1_with_zeros", "vol_acf1_without_zeros",
                     "vol_acf5", "vol_acf10", "ofi_acf1",
                     "sigma_hourly", "max_drawdown", "zero_return_pct"]:
            vals = [r[key] for r in seed_results]
            metrics[key] = {
                "mean": float(np.mean(vals)),
                "std": float(np.std(vals)),
                "min": float(np.min(vals)),
                "max": float(np.max(vals)),
            }

        # Check pass/fail using mean across seeds
        fat_tails_pass = metrics["kurtosis_with_zeros"]["mean"] > 0
        vol_cluster_pass = metrics["vol_acf1_with_zeros"]["mean"] > 0.1
        ofi_pass = metrics["ofi_acf1"]["mean"] > 0.0
        overall_pass = fat_tails_pass and vol_cluster_pass and ofi_pass

        # Print results
        print(f"\n  Fat tails (excess kurtosis):")
        print(f"    with zeros:    {metrics['kurtosis_with_zeros']['mean']:.2f} "
              f"± {metrics['kurtosis_with_zeros']['std']:.2f}")
        print(f"    without zeros: {metrics['kurtosis_without_zeros']['mean']:.2f} "
              f"± {metrics['kurtosis_without_zeros']['std']:.2f}")
        print(f"    pass: {'YES' if fat_tails_pass else 'NO'} (w/ zeros, mean > 0)")
        print(f"    reference: BTC normal: 5-10, stress: 10-30")

        print(f"\n  Volatility clustering (ACF of squared returns):")
        print(f"    lag-1 (w/ zeros):   {metrics['vol_acf1_with_zeros']['mean']:.4f} "
              f"± {metrics['vol_acf1_with_zeros']['std']:.4f}")
        print(f"    lag-1 (no zeros):   {metrics['vol_acf1_without_zeros']['mean']:.4f} "
              f"± {metrics['vol_acf1_without_zeros']['std']:.4f}")
        print(f"    lag-5:              {metrics['vol_acf5']['mean']:.4f} "
              f"± {metrics['vol_acf5']['std']:.4f}")
        print(f"    lag-10:             {metrics['vol_acf10']['mean']:.4f} "
              f"± {metrics['vol_acf10']['std']:.4f}")
        print(f"    pass: {'YES' if vol_cluster_pass else 'NO'} (w/ zeros, mean > 0.1)")
        print(f"    reference: real markets: lag1 = 0.1-0.5")

        print(f"\n  Order flow autocorrelation:")
        print(f"    lag-1: {metrics['ofi_acf1']['mean']:.4f} "
              f"± {metrics['ofi_acf1']['std']:.4f}")
        print(f"    pass: {'YES' if ofi_pass else 'NO'} (mean > 0)")
        print(f"    reference: real markets: 0.2-0.6")

        print(f"\n  Additional stats:")
        print(f"    sigma_hourly:   {metrics['sigma_hourly']['mean']:.6f} "
              f"± {metrics['sigma_hourly']['std']:.6f}")
        print(f"    max_drawdown:   {metrics['max_drawdown']['mean']:.4f} "
              f"± {metrics['max_drawdown']['std']:.4f}")
        print(f"    zero_return_pct:{metrics['zero_return_pct']['mean']:.2f}% "
              f"± {metrics['zero_return_pct']['std']:.2f}%")

        # Compare to held-out if available
        if held_out:
            print(f"\n  Comparison to held-out real data:")
            for key_sim, key_real, label in [
                ("sigma_hourly", "sigma_hourly", "sigma_hourly"),
                ("kurtosis_with_zeros", "kurtosis_with_zeros", "kurtosis (w/ zeros)"),
                ("vol_acf1_with_zeros", "vol_clustering_acf1_with_zeros", "vol_acf1 (w/ zeros)"),
            ]:
                sim_val = metrics[key_sim]["mean"]
                real_val = held_out.get(key_real, 0)
                ratio = sim_val / real_val if real_val != 0 else float('inf')
                print(f"    {label:<25} sim={sim_val:.4f}  real={real_val:.4f}  ratio={ratio:.2f}")

        print(f"\n  Overall: {'PASS' if overall_pass else 'FAIL'}")

        all_results.append({
            "scenario": scen,
            "n_seeds": len(seed_results),
            "seeds": seeds,
            "n_steps": args.steps,
            "metrics": metrics,
            "pass": {
                "fat_tails": fat_tails_pass,
                "vol_clustering": vol_cluster_pass,
                "order_flow": ofi_pass,
                "overall": overall_pass,
            },
            "seed_results": seed_results,
        })

    # Save
    out_path = os.path.join(PROCESSED_DIR, "stylized_facts_report.json")
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)

    all_pass = all(r["pass"]["overall"] for r in all_results)
    print(f"\n{'='*70}")
    print(f"Overall result: {'ALL PASS' if all_pass else 'SOME FAILURES'}")
    print(f"Report saved to {out_path}")


if __name__ == "__main__":
    main()
