"""
Sensitivity of the validation results to the background-flow order count.

Why this exists
  engine/config.py sets bg_flow_orders_per_min = 80 and its own comment says the
  count is deliberately HIGHER than the scaled real count (0.004 x 547 = 2.2 per
  minute) to avoid empty minutes. Flat minutes are also a validation statistic,
  so choosing 80 because it fixes flat minutes is circular: a validation number
  was used to set a dial. The calibration scripts use 2.2.

  This script does NOT pick a winner. It runs the same scenarios at both counts
  with the same seeds and prints the results side by side, next to the real
  held-out values, so the report can state how much each conclusion depends on
  this assumption. Both counts are ASSUMPTIONS. Do not choose the count that
  makes the output look best, and do not change bands or dials afterwards.

Setup: data/scripts/sim_setup.py (same config and population as calibration);
only the flow count differs between columns.

Run (from data/scripts):  py sensitivity_flow_count.py [--seeds 10] [--flows 2.2 80]
Output: data/processed/sensitivity_flow_count.json
"""
import argparse
import json
import math
import os
import sys

import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", ".."))
PERP_SIM_DIR = os.path.join(REPO_ROOT, "perp_sim")
PROCESSED_DIR = os.path.join(REPO_ROOT, "data", "processed")
sys.path.insert(0, PERP_SIM_DIR)
sys.path.insert(0, SCRIPT_DIR)

from engine import SCENARIOS                     # noqa: E402
import sim_setup                                 # noqa: E402
import validate_microstructure_vs_real as micro  # noqa: E402

SEEDS0 = 1000          # same fresh seeds as validate_microstructure_vs_real.py
DEFAULT_FLOWS = [2.2, 80.0]
SCENARIO_NAMES = ["normal-thick-synthetic-no-event", "normal-thin-synthetic-no-event"]

METRICS = [
    ("flat_minutes_pct", "flat minutes (%)"),
    ("sigma_hourly", "local hourly vol"),
    ("kurtosis", "excess kurtosis"),
    ("vol_acf1", "vol clustering ACF1"),
    ("spread_median_bps", "median spread (bp)"),
    ("spread_gt_1bps", "share of minutes spread > 1 bp"),
    ("price_impact_norm", "price impact (norm.)"),
    ("impact_r2", "impact R2"),
    ("depth_dip", "depth dip (lowest / prior median)"),
    ("recovery_min", "recovery (min)"),
    ("depth_btc_median", "median depth (BTC)"),
]


def _acf1(x):
    x = np.asarray(x, float)
    if len(x) < 3:
        return 0.0
    d = x - x.mean()
    c0 = np.mean(d * d)
    return float(np.mean(d[:-1] * d[1:]) / c0) if c0 > 0 else 0.0


def _excess_kurtosis(x):
    x = np.asarray(x, float)
    s = x.std(ddof=1) if len(x) > 3 else 0.0
    return float(np.mean(((x - x.mean()) / s) ** 4) - 3.0) if s > 0 else 0.0


def run_stats(scenario_name, seed, flow):
    exchange, cfg = sim_setup.run(SCENARIOS[scenario_name], seed, sim_setup.N_STEPS,
                                  sim_setup.N_BOTS, flow_orders_per_min=flow)
    recs = exchange.records
    prices = np.array([cfg.initial_price] + [r.last_price for r in recs])
    ret = np.diff(np.log(prices))
    ret = ret[np.isfinite(ret)]
    spreads = np.array([r.spread_rel for r in recs])
    spreads = spreads[spreads > 0] * 1e4
    out = {
        "flat_minutes_pct": float(np.mean(ret == 0) * 100),
        "sigma_hourly": float(np.std(ret) * math.sqrt(60)),
        "kurtosis": _excess_kurtosis(ret),
        "vol_acf1": _acf1(ret ** 2),
        "spread_median_bps": float(np.median(spreads)) if len(spreads) else float("nan"),
    }
    out.update(micro.sim_stats(recs, crash=False))
    return out


def mean_over_seeds(rows):
    return {k: float(np.nanmean([r[k] for r in rows])) for k in rows[0]}


def real_reference():
    tg = json.load(open(os.path.join(PROCESSED_DIR, "calibration_targets.json")))
    held = tg["held_out"]
    real_micro = micro.real_calm(micro.load_real(), held["days"])
    ref = {
        "flat_minutes_pct": held["zero_return_pct"],
        "sigma_hourly": held["sigma_hourly"],
        "kurtosis": held["kurtosis_with_zeros"],
        "vol_acf1": held["vol_clustering_acf1_with_zeros"],
        "spread_median_bps": held["spread_bps_median"],
    }
    for k in ("spread_gt_1bps", "price_impact_norm", "impact_r2", "depth_dip",
              "recovery_min", "depth_btc_median"):
        ref[k] = real_micro[k]
    return ref, held["days"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--flows", type=float, nargs="+", default=DEFAULT_FLOWS)
    args = ap.parse_args()
    seeds = [SEEDS0 + i for i in range(args.seeds)]

    real, held_days = real_reference()
    results = {}
    for scn in SCENARIO_NAMES:
        results[scn] = {}
        for flow in args.flows:
            results[scn][f"{flow:g}"] = mean_over_seeds([run_stats(scn, s, flow) for s in seeds])

    print("=" * 78)
    print(f"Flow-count sensitivity ({len(seeds)} seeds; both counts are ASSUMPTIONS)")
    print("=" * 78)
    heads = "".join(f"{('sim ' + f'{f:g}/min'):>16}" for f in args.flows)
    for scn in SCENARIO_NAMES:
        print(f"\n{scn}\n  {'metric':<36}{heads}{'real held-out':>16}")
        for key, label in METRICS:
            vals = "".join(f"{results[scn][f'{f:g}'][key]:>16.5g}" for f in args.flows)
            print(f"  {label:<36}{vals}{real[key]:>16.5g}")

    out = {
        "seeds": seeds,
        "flow_counts_orders_per_min": args.flows,
        "environment": sim_setup.describe(),
        "real_held_out_days": held_days,
        "real_held_out": real,
        "results": results,
        "note": "Both flow counts are assumptions. 2.2 = 0.004 x 547 real trades/min; 80 was chosen in the "
                "engine to avoid empty minutes, and flat minutes is also a validation statistic, so 80 is "
                "circular. This file reports sensitivity only; do not select a count because it matches "
                "real values, and do not change bands or dials after seeing it.",
    }
    path = os.path.join(PROCESSED_DIR, "sensitivity_flow_count.json")
    json.dump(out, open(path, "w"), indent=2)
    print(f"\nSaved to {path}")


if __name__ == "__main__":
    main()
