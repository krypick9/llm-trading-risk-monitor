"""Derive ONE unified market scale for the mini-market.

Problem being fixed
-------------------
Phase 0 calibrated LP depth (8/4 BTC per level) and trader order flow
(0.002 BTC median trades from a few quiet bots) to two DIFFERENT slices of
the real market. The flow/depth ratio -- the quantity that actually sets
price impact -- was therefore arbitrary, and measured impact came out at
~0.08-0.11x real. Meanwhile the reference population had no source of
aggressive flow at all, giving ~60% flat minutes.

Fix: pick ONE scale factor `s` (sim / real) and apply it to BOTH sides:
    flow volume : bg_flow_btc_per_min    = s x real BTC/min volume
    flow counts : bg_flow_orders_per_min = s x real trades/min
    near depth  : bg_depth_btc per side  = s x real near-depth (+-0.10% band)
The flow/depth ratio (real: ~0.39) is then inherited, so normalized price
impact (impact per unit of volume) carries over automatically.

Anchor choice for s: it must keep the mini-market small enough that a
$1,000 agent (max ~0.2 BTC position) is still a relevant participant
(a non-negligible share of near depth and of one minute of flow).

Usage:
    python3 data/scripts/calibrate_market_scale.py --scale 0.004
Writes data/processed/market_scale_settings.json and prints the
SimConfig / Scenario fields to set.
"""

import argparse
import json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", ".."))
PROCESSED_DIR = os.path.join(REPO_ROOT, "data", "processed")

# Real anchors (Binance 1-min aggTrades + Bybit bookDepth, normal regime,
# from data/processed/calibration_targets.json).
REAL_DEFAULTS = {
    "btc_per_min_median": 48.542275865348486,
    "trades_per_min_median": 547.0,
    "near_depth_btc_per_side": 123.00571200244632,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=float, default=0.004,
                    help="sim/real scale factor applied to flow AND depth")
    ap.add_argument("--regime", choices=["normal", "stress"], default="normal")
    args = ap.parse_args()

    targets_path = os.path.join(PROCESSED_DIR, "calibration_targets.json")
    with open(targets_path) as f:
        targets = json.load(f)
    tgt = targets[args.regime]

    real = {
        "btc_per_min_median": tgt["btc_per_min_median"],
        "trades_per_min_median": tgt["trades_per_min_median"],
        "near_depth_btc_per_side": tgt["depth_btc"],
    }
    s = args.scale
    flow_btc_per_min = s * real["btc_per_min_median"]
    depth_btc = s * real["near_depth_btc_per_side"]
    # Count floor: scaling the trade COUNT by the same factor would leave
    # ~11% empty minutes from Poisson zeros alone (vs 3.76% real), and
    # few-but-large orders stall on one ladder level. Keep counts above
    # ~80/min and let the volume target set the size scale.
    orders_per_min = max(s * real["trades_per_min_median"], 80.0)

    # Thin scenarios use half the calibrated scale (the thick/thin factorial
    # contrast is preserved around the anchored value).
    settings = {
        "scale_factor": s,
        "regime": args.regime,
        "real_anchors": real,
        "sim_targets": {
            "bg_flow_btc_per_min": round(flow_btc_per_min, 6),
            "bg_flow_orders_per_min": round(orders_per_min, 4),
            "bg_depth_btc_thick": round(depth_btc, 6),
            "bg_depth_btc_thin": round(depth_btc / 2.0, 6),
            "expected_flow_over_depth": round(flow_btc_per_min / depth_btc, 4),
        },
        "SimConfig_fields": {
            "bg_flow_enabled": True,
            "bg_flow_orders_per_min": round(orders_per_min, 4),
            "bg_flow_btc_per_min": round(flow_btc_per_min, 6),
            "bg_flow_size_median_btc": 0.002,
            "bg_flow_size_sigma": 2.5,
            "bg_flow_max_size_btc": 0.5,
            "bg_backstop_only": True,
            "bg_level_gap": 0.0002,
            "bg_backstop_extra": 0.0002,
        },
        "Scenario_fields": {
            "bg_depth_btc_thick": round(depth_btc, 6),
            "bg_depth_btc_thin": round(depth_btc / 2.0, 6),
        },
        "notes": [
            "One scale factor applied to flow volume, flow counts and near depth",
            "so the flow/depth ratio (and hence normalized price impact) is",
            "inherited from the real market instead of being accidental.",
            "bg_flow_size_median_btc stays at the real median (0.002 BTC =",
            "2 lots); the volume target renormalizes the tail per tick.",
            "Regenerate with this script when the real anchors are refitted.",
        ],
    }

    out_path = os.path.join(PROCESSED_DIR, "market_scale_settings.json")
    with open(out_path, "w") as f:
        json.dump(settings, f, indent=2)

    print(f"Unified market scale  s = {s}  (regime: {args.regime})")
    print(f"  real: {real['btc_per_min_median']:.1f} BTC/min over "
          f"{real['trades_per_min_median']:.0f} trades/min, "
          f"{real['near_depth_btc_per_side']:.1f} BTC near-depth per side")
    print(f"  sim : {flow_btc_per_min:.4f} BTC/min over "
          f"{orders_per_min:.2f} orders/min, {depth_btc:.4f} BTC near-depth per side")
    print(f"  flow/depth ratio: {flow_btc_per_min / depth_btc:.3f} "
          f"(real: {real['btc_per_min_median'] / real['near_depth_btc_per_side']:.3f})")
    print(f"\nSet the SimConfig/Scenario fields listed in {out_path}")


if __name__ == "__main__":
    main()
