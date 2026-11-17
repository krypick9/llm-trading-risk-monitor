"""
Held-out validation (v1)

Question: does the simulator, tuned on 24 Sep - 9 Oct, also reproduce
calm days it never saw (11, 12, 13, 20 Oct)?

Method (fixed BEFORE looking at results):
 1. Take the calibrated normal-regime dials (from calibration_result.json).
 2. Held-out days were more volatile and thinner than the training days,
    so scale three dials by the same ratios seen in the real data:
        sigma  x (held-out sigma / training sigma)
        depth  x (held-out depth / training depth)
        spread x (held-out spread / training spread)
    No other dial is touched, and nothing is tuned to the held-out result.
    EXCEPTION (stability floor): the mini-market breaks below ~1.5 BTC of
    depth (spreads blow up in about half the seeds, seen at 0.91 BTC and in
    the calibration grid at 0.5-1.0 BTC). Scaled depth is therefore never
    allowed below DEPTH_FLOOR = 1.5 BTC. This was decided after seeing that
    instability, not by tuning to the held-out statistics; report it as a
    limitation: the sim cannot represent the thinner held-out book.
 3. Run N seeds, compare against REAL held-out statistics.
 4. Control: also run the unscaled (training) dials, to show whether
    the scaling matters.

Pass rules (written before running):
    sigma_hourly   within 0.7x - 1.3x of held-out real
    vol_acf1       within +/-0.15 of held-out real
    kurtosis       within 1/3x - 3x of held-out real
    zero-return %  between 1% and 6%
    spread         within 2x of held-out real
Verdict: PASS if >= 4 of 5 rules pass.

Run:  py validate_heldout.py        (from data/scripts)
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
DEPTH_FLOOR = 1.5   # BTC, see docstring
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
        shock_frac=0.35,
        shock_return=tgt["normal"]["shock_return_1min"],
        bg_depth_btc=depth, bg_half_spread=spread,
        bg_walkaway=False, use_replay=False, replay_regime="normal",
    )


def run_many(scenario):
    stats = [run_one(scenario, seed=s, n_steps=600, n_bots=50) for s in SEEDS]
    keys = ["sigma_hourly", "vol_acf1", "return_kurtosis", "zero_return_pct", "spread_rel"]
    return {k: (float(np.mean([s[k] for s in stats])), float(np.std([s[k] for s in stats])))
            for k in keys}


def judge(sim, held):
    real_spread = held["spread_bps_median"] / 10000.0
    rules = [
        ("sigma_hourly", sim["sigma_hourly"][0], held["sigma_hourly"],
         0.7 <= sim["sigma_hourly"][0] / held["sigma_hourly"] <= 1.3,
         f"ratio {sim['sigma_hourly'][0] / held['sigma_hourly']:.2f} (band 0.7-1.3)"),
        ("vol_acf1", sim["vol_acf1"][0], held["vol_clustering_acf1_with_zeros"],
         abs(sim["vol_acf1"][0] - held["vol_clustering_acf1_with_zeros"]) <= 0.15,
         f"diff {sim['vol_acf1'][0] - held['vol_clustering_acf1_with_zeros']:+.2f} (band +/-0.15)"),
        ("kurtosis", sim["return_kurtosis"][0], held["kurtosis_with_zeros"],
         1 / 3 <= sim["return_kurtosis"][0] / held["kurtosis_with_zeros"] <= 3,
         f"ratio {sim['return_kurtosis'][0] / held['kurtosis_with_zeros']:.2f} (band 0.33-3)"),
        ("zero_return_%", sim["zero_return_pct"][0], held["zero_return_pct"],
         1.0 <= sim["zero_return_pct"][0] <= 6.0,
         "sim must be 1%-6%"),
        ("spread", sim["spread_rel"][0], real_spread,
         0.5 <= sim["spread_rel"][0] / real_spread <= 2.0,
         f"ratio {sim['spread_rel'][0] / real_spread:.2f} (band 0.5-2)"),
    ]
    return rules


def show(title, sim, held):
    rules = judge(sim, held)
    print(f"\n{title}")
    print(f"  {'metric':<15}{'sim (mean +/- sd)':>24}{'real held-out':>16}   result")
    for name, simv, realv, ok, note in rules:
        sd = sim[{"sigma_hourly": "sigma_hourly", "vol_acf1": "vol_acf1",
                  "kurtosis": "return_kurtosis", "zero_return_%": "zero_return_pct",
                  "spread": "spread_rel"}[name]][1]
        print(f"  {name:<15}{simv:>16.5f} +/- {sd:<6.5f}{realv:>13.5f}   "
              f"{'PASS' if ok else 'FAIL'}  {note}")
    n_ok = sum(r[3] for r in rules)
    print(f"  -> {n_ok}/5 rules pass => {'VALIDATED' if n_ok >= 4 else 'NOT VALIDATED'}")
    return n_ok, rules


def main():
    tgt = load("calibration_targets.json")
    cal = load("calibration_result.json")["normal"]["calibrated_params"]
    held = tgt["held_out"]
    train = tgt["normal"]

    r_sigma = held["sigma_hourly"] / train["sigma_hourly"]
    r_depth = held["depth_btc_median"] / train["depth_btc"]
    r_spread = held["spread_bps_median"] / train["spread_bps_median"]

    print("=" * 70)
    print("Held-out validation")
    print("=" * 70)
    print(f"Held-out days: {held['days']}")
    print(f"Scaling ratios (held-out / training): sigma {r_sigma:.2f}, "
          f"depth {r_depth:.2f}, spread {r_spread:.2f}")

    base = (cal["sigma_hourly"], cal["bg_depth_btc"], cal["bg_half_spread"])
    scaled = (base[0] * r_sigma, max(base[1] * r_depth, DEPTH_FLOOR), base[2] * r_spread)
    print(f"Calibrated dials : sigma {base[0]:.6f}, depth {base[1]:.2f} BTC, spread {base[2]:.2e}")
    if base[1] * r_depth < DEPTH_FLOOR:
        print(f"NOTE: depth x{r_depth:.2f} would be {base[1] * r_depth:.2f} BTC; held at floor {DEPTH_FLOOR} BTC (sim unstable below).")
    print(f"Scaled dials     : sigma {scaled[0]:.6f}, depth {scaled[1]:.2f} BTC, spread {scaled[2]:.2e}")
    print(f"Running {N_SEEDS} seeds per case ...")

    sim_scaled = run_many(build_scenario("heldout-scaled", *scaled, tgt))
    sim_ctrl = run_many(build_scenario("heldout-control", *base, tgt))

    n_ok, rules = show("MAIN TEST: dials scaled to held-out conditions", sim_scaled, held)
    n_ctl, _ = show("CONTROL: unscaled training dials", sim_ctrl, held)

    out = {
        "held_out_days": held["days"],
        "scaling_ratios": {"sigma": r_sigma, "depth": r_depth, "spread": r_spread},
        "scaled_dials": {"sigma": scaled[0], "depth": scaled[1], "spread": scaled[2]},
        "n_seeds": N_SEEDS,
        "main": {k: {"mean": v[0], "sd": v[1]} for k, v in sim_scaled.items()},
        "control": {k: {"mean": v[0], "sd": v[1]} for k, v in sim_ctrl.items()},
        "rules_passed_main": n_ok, "rules_passed_control": n_ctl,
        "verdict": "VALIDATED" if n_ok >= 4 else "NOT VALIDATED",
    }
    path = os.path.join(PROCESSED_DIR, "validation_heldout.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nSaved to {path}")


if __name__ == "__main__":
    main()
