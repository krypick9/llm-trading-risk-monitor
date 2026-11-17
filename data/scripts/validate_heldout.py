"""Frozen-parameter held-out diagnostic; do not tune dials on held-out data.

Oct 10 is reserved for the known crash replay. Selected post-crash held-out
dates have already been inspected and are not a fresh untouched validation.
"""
import json
import os
import sys

import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from run_calibration import run_one, PROCESSED_DIR          # noqa: E402
from engine.config import Scenario                           # noqa: E402

N_SEEDS = 10
SEEDS = [1000 + i for i in range(N_SEEDS)]   # different from the calibration seeds


def load(name):
    with open(os.path.join(PROCESSED_DIR, name)) as f:
        return json.load(f)


def build_scenario(label, sigma, depth, spread, tgt):
    return Scenario(
        name=label, regime="normal",
        sigma_hourly=sigma,
        jump_rate_per_hour=tgt["normal"]["jump_rate_per_hour"],
        jump_mean=tgt["normal"]["jump_mean"],
        jump_std=tgt["normal"]["jump_std"],
        shock_frac=None,
        shock_return=tgt["normal"]["shock_return_1min"],
        bg_depth_btc=depth, bg_half_spread=spread,
        bg_walkaway=False, use_replay=False, replay_regime="normal",
    )


def run_many(scenario):
    stats = [run_one(scenario, seed=s, n_steps=600, n_bots=50) for s in SEEDS]
    keys = ["sigma_hourly", "vol_acf1", "return_kurtosis", "zero_return_pct", "spread_rel",
            "exchange_sigma_hourly", "exchange_zero_return_pct", "exchange_return_kurtosis",
            "exchange_vol_acf1", "exchange_worst_1min_return", "exchange_jump_rate_per_hour"]
    return {k: (float(np.mean([s[k] for s in stats])), float(np.std([s[k] for s in stats])))
            for k in keys}


def judge(sim, held):
    real_spread = held["spread_bps_median"] / 10000.0
    sigma_ratio = sim["exchange_sigma_hourly"][0] / held["sigma_hourly"]
    vol_diff = sim["exchange_vol_acf1"][0] - held["vol_clustering_acf1_with_zeros"]
    kurt_ratio = sim["exchange_return_kurtosis"][0] / held["kurtosis_with_zeros"]
    spread_ratio = sim["spread_rel"][0] / real_spread
    return [
        ("local_sigma", sim["exchange_sigma_hourly"][0], held["sigma_hourly"],
         0.7 <= sigma_ratio <= 1.3, f"ratio {sigma_ratio:.2f} (band 0.7-1.3)"),
        ("vol_acf1", sim["exchange_vol_acf1"][0], held["vol_clustering_acf1_with_zeros"],
         abs(vol_diff) <= 0.15, f"difference {vol_diff:+.3f} (band +/-0.15)"),
        ("kurtosis", sim["exchange_return_kurtosis"][0], held["kurtosis_with_zeros"],
         1 / 3 <= kurt_ratio <= 3, f"ratio {kurt_ratio:.2f} (band 0.33-3)"),
        ("flat_minutes", sim["exchange_zero_return_pct"][0], held["zero_return_pct"],
         None, "descriptive only; previous fixed interval was invalid"),
        ("spread", sim["spread_rel"][0], real_spread, None,
         f"ratio {spread_ratio:.2f}; diagnostic only because dial is data-derived"),
    ]


def show(title, sim, held):
    rules = judge(sim, held)
    print(f"\n{title}")
    print(f"  {'metric':<15}{'sim (mean +/- sd)':>24}{'real held-out':>16}   result")
    for name, simv, realv, ok, note in rules:
        sim_key = {"local_sigma": "exchange_sigma_hourly", "vol_acf1": "exchange_vol_acf1",
                   "kurtosis": "exchange_return_kurtosis", "flat_minutes": "exchange_zero_return_pct",
                   "spread": "spread_rel"}[name]
        sd = sim[sim_key][1]
        result = "INFO" if ok is None else ("PASS" if ok else "FAIL")
        print(f"  {name:<15}{simv:>16.5f} +/- {sd:<6.5f}{realv:>13.5f}   "
              f"{result}  {note}")
    n_ok = sum(bool(r[3]) for r in rules[:3])
    print(f"  -> {n_ok}/3 scored checks; diagnostics are not a fresh independent validation")
    return n_ok, rules


def main():
    tgt = load("calibration_targets.json")
    cal = load("calibration_result.json")["normal"]["calibrated_params"]
    held = tgt["held_out"]
    print("=" * 70)
    print("Frozen-dial held-out diagnostic")
    print("=" * 70)
    print(f"Held-out days: {held['days']}")
    if "2025-10-10" in held.get("days", []):
        raise ValueError("Oct 10 is the known replay date and cannot be held out")
    all_training_dates = set(tgt["normal"].get("training_days", [])) | \
        set(tgt["stress"].get("training_days", []))
    overlap = set(held.get("days", [])) & all_training_dates
    if overlap:
        raise ValueError(f"Training/held-out date overlap: {sorted(overlap)}")

    base = (cal["sigma_hourly"], cal["bg_depth_btc"], cal["bg_half_spread"])
    print(f"Frozen training dials: sigma {base[0]:.6f}, depth {base[1]:.2f} BTC, spread {base[2]:.2e}")
    print(f"Running {N_SEEDS} seeds per case ...")

    sim = run_many(build_scenario("heldout-training-frozen", *base, tgt))
    n_ok, rules = show("TRAINING-FROZEN PARAMETERS", sim, held)

    out = {
        "held_out_days": held["days"],
        "held_out_is_independent_validation": False,
        "independence_note": "Dates have been inspected previously; this is a diagnostic rerun, not a fresh untouched validation.",
        "validation_note": "Training dials remain frozen; held-out metrics do not change simulator parameters.",
        "frozen_training_dials": {"sigma": base[0], "depth": base[1], "spread": base[2]},
        "n_seeds": N_SEEDS,
        "metrics": {k: {"mean": v[0], "sd": v[1]} for k, v in sim.items()},
        "scenario_type": "synthetic_no_event",
        "scored_checks_passed": n_ok,
        "scored_checks_total": 3,
        "rules": [{"name": r[0], "passed": r[3], "note": r[4]} for r in rules],
        "verdict": "DIAGNOSTIC ONLY — not independent validation",
    }
    path = os.path.join(PROCESSED_DIR, "validation_heldout.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nSaved to {path}")


if __name__ == "__main__":
    main()
