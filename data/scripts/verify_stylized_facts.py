"""
Verify that the calibrated simulator produces realistic stylized facts.

After calibration, this script runs the simulator with calibrated parameters
and checks whether the output exhibits the statistical properties that real
financial markets are known to have:

  1. Fat-tailed returns: excess kurtosis > 0 (kurtosis > 3 in raw terms)
     Real BTC returns have kurtosis of 5-20+ (much fatter than normal).
     
  2. Volatility clustering: squared returns show positive autocorrelation
     at multiple lags. "Volatility begets volatility" — high-vol periods
     cluster together, they don't alternate randomly.
     
  3. Order flow autocorrelation: buyer-initiated vs seller-initiated trades
     show positive autocorrelation. Buying tends to be followed by more
     buying (and vice versa) — this is how momentum and trends form.

If any check fails, the script suggests which parameters to adjust.

Usage:
  python verify_stylized_facts.py
  python verify_stylized_facts.py --scenario stress-thin-early
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
    x = np.array(x)
    n = len(x)
    if n <= lag:
        return 0.0
    mean = np.mean(x)
    c0 = np.mean((x - mean) ** 2)
    ck = np.mean((x[:n-lag] - mean) * (x[lag:] - mean))
    return float(ck / c0) if c0 > 0 else 0.0


def excess_kurtosis(x):
    """Excess kurtosis (>0 means fatter tails than normal)."""
    x = np.array(x)
    n = len(x)
    if n < 4:
        return 0.0
    mean = np.mean(x)
    std = np.std(x, ddof=1)
    if std == 0:
        return 0.0
    return float((1/n) * np.sum(((x - mean) / std) ** 4) - 3.0)


def run_and_verify(scenario_name, seed=42, n_steps=600, n_bots=50):
    """Run one simulation and check stylized facts."""
    
    # Try to use calibrated scenarios if available
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
    prices = [cfg.initial_price] + [r.last_price for r in recs]
    returns = np.diff(np.log(prices))
    
    # Remove zero/nan returns
    returns = returns[np.isfinite(returns) & (returns != 0)]
    
    if len(returns) < 60:
        print(f"  [warn] only {len(returns)} usable returns")
    
    # --- Check 1: Fat tails ---
    kurt = excess_kurtosis(returns)
    fat_tails_pass = kurt > 0  # excess kurtosis > 0 means fatter than normal
    
    # Real BTC reference: normal days kurtosis ~ 5-10, stress days ~ 10-30
    
    # --- Check 2: Volatility clustering ---
    squared_returns = returns ** 2
    acf_lag1 = autocorrelation(squared_returns, lag=1)
    acf_lag5 = autocorrelation(squared_returns, lag=5)
    acf_lag10 = autocorrelation(squared_returns, lag=10)
    vol_cluster_pass = acf_lag1 > 0.1  # positive autocorrelation at lag 1
    
    # Real markets: lag-1 ACF of squared returns is typically 0.1-0.5
    
    # --- Check 3: Order flow autocorrelation ---
    ofi_values = [r.order_flow_imbalance for r in recs if r.volume > 0]
    ofi_acf1 = autocorrelation(ofi_values, lag=1) if len(ofi_values) > 2 else 0.0
    ofi_pass = ofi_acf1 > 0.0  # positive = buying follows buying
    
    # Real markets: order flow autocorrelation is strongly positive (0.2-0.6)
    
    # --- Additional stats ---
    sigma_hourly = np.std(returns) * math.sqrt(60)
    max_drawdown = max((max(prices[:i+1]) - prices[i]) / max(prices[:i+1]) 
                       for i in range(len(prices)))
    
    result = {
        "scenario": scenario_name,
        "n_steps": n_steps,
        "n_returns": len(returns),
        "sigma_hourly": float(sigma_hourly),
        "max_drawdown": float(max_drawdown),
        "stylized_facts": {
            "fat_tails": {
                "excess_kurtosis": float(kurt),
                "pass": fat_tails_pass,
                "reference": "BTC normal: 5-10, stress: 10-30",
            },
            "volatility_clustering": {
                "acf_lag1": float(acf_lag1),
                "acf_lag5": float(acf_lag5),
                "acf_lag10": float(acf_lag10),
                "pass": vol_cluster_pass,
                "reference": "real markets: lag1 = 0.1-0.5",
            },
            "order_flow_autocorrelation": {
                "acf_lag1": float(ofi_acf1),
                "pass": ofi_pass,
                "reference": "real markets: 0.2-0.6",
            },
        },
        "overall_pass": fat_tails_pass and vol_cluster_pass and ofi_pass,
    }
    
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default=None, help="Test specific scenario")
    parser.add_argument("--steps", type=int, default=600)
    args = parser.parse_args()
    
    print("=" * 60)
    print("Stylized Facts Verification")
    print("=" * 60)
    
    # Test scenarios to verify
    if args.scenario:
        test_scenarios = [args.scenario]
    else:
        # Test one normal and one stress
        test_scenarios = ["normal-thick-early", "stress-thin-early"]
    
    all_results = []
    all_pass = True
    
    for scen in test_scenarios:
        print(f"\n--- Scenario: {scen} ({args.steps} steps) ---")
        result = run_and_verify(scen, seed=42, n_steps=args.steps)
        if result:
            all_results.append(result)
            sf = result["stylized_facts"]
            
            print(f"\n  Fat tails (excess kurtosis):")
            print(f"    value:     {sf['fat_tails']['excess_kurtosis']:.2f}")
            print(f"    pass:      {'YES' if sf['fat_tails']['pass'] else 'NO'}")
            print(f"    reference: {sf['fat_tails']['reference']}")
            
            print(f"\n  Volatility clustering (ACF of squared returns):")
            print(f"    lag-1:     {sf['volatility_clustering']['acf_lag1']:.4f}")
            print(f"    lag-5:     {sf['volatility_clustering']['acf_lag5']:.4f}")
            print(f"    lag-10:    {sf['volatility_clustering']['acf_lag10']:.4f}")
            print(f"    pass:      {'YES' if sf['volatility_clustering']['pass'] else 'NO'}")
            print(f"    reference: {sf['volatility_clustering']['reference']}")
            
            print(f"\n  Order flow autocorrelation:")
            print(f"    lag-1:     {sf['order_flow_autocorrelation']['acf_lag1']:.4f}")
            print(f"    pass:      {'YES' if sf['order_flow_autocorrelation']['pass'] else 'NO'}")
            print(f"    reference: {sf['order_flow_autocorrelation']['reference']}")
            
            overall = result["overall_pass"]
            print(f"\n  Overall: {'PASS' if overall else 'FAIL'}")
            
            if not overall:
                all_pass = False
                print(f"\n  Suggestions:")
                if not sf["fat_tails"]["pass"]:
                    print(f"    - Fat tails fail: increase jump_rate_per_hour or jump_std")
                    print(f"      to add more extreme return events")
                if not sf["volatility_clustering"]["pass"]:
                    print(f"    - Vol clustering fail: the sigma regime may be too uniform.")
                    print(f"      Try increasing jump_rate to create vol bursts, or")
                    print(f"      add a regime-switching component to sigma_hourly")
                if not sf["order_flow_autocorrelation"]["pass"]:
                    print(f"    - Order flow autocorr fail: increase the share of")
                    print(f"      CrowdBot (herd behavior creates correlated order flow)")
    
    # Save results
    out_path = os.path.join(PROCESSED_DIR, "stylized_facts_report.json")
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2)
    
    print(f"\n{'=' * 60}")
    print(f"Overall result: {'ALL PASS' if all_pass else 'SOME FAILURES'}")
    print(f"Report saved to {out_path}")
    
    if not all_pass:
        print(f"\nNote: Some stylized facts not matched. This is expected with")
        print(f"scripted bots. LLM agents may naturally produce these patterns.")
        print(f"Consider this a baseline — improvements to the bot mix or")
        print(f"adding regime-switching volatility may help.")


if __name__ == "__main__":
    main()
