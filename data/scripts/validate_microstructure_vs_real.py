"""
Microstructure validation against real data (TA Q6, second half)

Question: does the simulator reproduce realistic PRICE IMPACT, LIQUIDITY
DEPTH/RECOVERY and CRASH behaviour -- not just return statistics?

validate_heldout.py checks return statistics (volatility, tails, clustering).
validate_cascade_impact.py runs the simulator but only checks "lambda > 0" and
"recovery <= 60 steps", with no real-data reference. This script puts the
simulator and the real data side by side, using the SAME definitions on both.

Real data : data/reference/binance_aggTrades_BTCUSDT_1min.csv  (signed flow, price)
            data/reference/bybit_orderbook_BTCUSDT_1min.csv    (spread, depth within 0.10%)
Sim       : perp_sim, normal-trader baseline, 50 bots, 600 steps, fresh seeds.

Metrics (same formula on both sides)
  1. price_impact_norm : lambda x median minute volume. lambda = Cov(ret, signed flow)/Var(flow).
                         "return per one typical minute of volume" -> removes the mini-market scale.
  2. impact_r2         : corr(ret, signed flow)^2 (with intercept; real data compare like with like).
  3. depth_dip         : after a >2-sigma minute, lowest depth in the next 5 min / median depth of the prior 30 min.
  4. recovery_min      : minutes until depth is back to >= 90% of the pre-shock level (cap 60).
  5. spread_gt_1bps    : share of minutes with spread above 1 bp (real tick-size spread is ~0.009 bp).
  6. crash window      : lowest depth vs pre-crash depth, widest spread, worst 1-min return, max drawdown.

Reading the result
  Tolerance bands below were chosen by us AFTER seeing a first run, so this is a
  DIAGNOSTIC of where the simulator differs from reality, not a formal pass/fail
  test like validate_heldout.py. Liquidation counts cannot be checked: the
  reference data contain no liquidation feed.

Run (from data/scripts):  py validate_microstructure_vs_real.py [--seeds 10]
Output: data/processed/validation_microstructure.json
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", ".."))
PERP_SIM_DIR = os.path.join(REPO_ROOT, "perp_sim")
PROCESSED_DIR = os.path.join(REPO_ROOT, "data", "processed")
REF_DIR = os.path.join(REPO_ROOT, "data", "reference")
sys.path.insert(0, PERP_SIM_DIR)
sys.path.insert(0, SCRIPT_DIR)

from engine import SimConfig, Exchange, SCENARIOS      # noqa: E402
from baseline_crowd import make_crowd, open_accounts    # noqa: E402

SEEDS0 = 1000                 # not used in calibration (42,123,456,789,2025)
CRASH_DAY = "2025-10-10"
CRASH_START_UTC = "12:00"     # same start as the replay path in scenarios.py
N_STEPS = 600
SHOCK_K, DIP_WINDOW, PRE_WINDOW, REC_FRAC, REC_CAP = 2.0, 5, 30, 0.9, 60


# ---------------------------------------------------------------- shared metrics
def impact_stats(ret, flow, vol):
    m = np.isfinite(ret) & np.isfinite(flow) & (vol > 0)
    r, f, v = ret[m], flow[m], vol[m]
    lam = np.cov(f, r)[0, 1] / np.var(f, ddof=1)
    corr = np.corrcoef(f, r)[0, 1]
    return {"price_impact_norm": float(lam * np.median(v)), "impact_r2": float(corr ** 2)}


def shock_stats(ret, depth):
    sig = np.nanstd(ret)
    dips, recs, i = [], [], PRE_WINDOW + 1
    while i < len(ret) - REC_CAP:
        if abs(ret[i]) > SHOCK_K * sig:
            pre = np.median(depth[i - PRE_WINDOW:i])
            if pre > 0:
                dips.append(float(np.min(depth[i:i + DIP_WINDOW]) / pre))
                k = next((j - i for j in range(i + 1, i + REC_CAP) if depth[j] >= REC_FRAC * pre), REC_CAP)
                recs.append(k)
                i += REC_CAP // 2
                continue
        i += 1
    return {"n_shocks": len(dips),
            "depth_dip": float(np.median(dips)) if dips else float("nan"),
            "recovery_min": float(np.median(recs)) if recs else float("nan")}


def crash_stats(price, depth, spread_bps):
    ret = np.diff(np.log(price))
    pre = np.median(depth[:PRE_WINDOW])
    dd = (price / np.maximum.accumulate(price) - 1).min()
    return {"depth_min_vs_pre": float(depth.min() / pre), "spread_max_bps": float(spread_bps.max()),
            "worst_1min_return": float(ret.min()), "max_drawdown": float(dd)}


# ---------------------------------------------------------------- real data
def load_real():
    a = pd.read_csv(os.path.join(REF_DIR, "binance_aggTrades_BTCUSDT_1min.csv"), parse_dates=["ts_utc"])
    o = pd.read_csv(os.path.join(REF_DIR, "bybit_orderbook_BTCUSDT_1min.csv"), parse_dates=["ts_utc"])
    d = a.merge(o, on="ts_utc").sort_values("ts_utc").reset_index(drop=True)
    d["date"] = d.ts_utc.dt.strftime("%Y-%m-%d")
    d["ret"] = np.log(d.close).diff()
    d.loc[d.date != d.date.shift(), "ret"] = np.nan          # no return across a day gap
    d["depth_btc"] = d.near_depth_usd / d.mid_bybit
    d["flow_btc"] = d.signed_usd / d.close
    d["vol_btc"] = d.vol_usd / d.close
    return d


def real_calm(d, days):
    x = d[d.date.isin(days)].reset_index(drop=True)
    out = impact_stats(x.ret.values, x.flow_btc.values, x.vol_btc.values)
    out.update(shock_stats(x.ret.values, x.depth_btc.values))
    out["spread_gt_1bps"] = float((x.spread_bps_max > 1.0).mean())
    out["depth_btc_median"] = float(x.depth_btc.median())
    return out


def real_crash(d):
    x = d[d.date == CRASH_DAY].reset_index(drop=True)
    s = x.index[x.ts_utc.dt.strftime("%H:%M") == CRASH_START_UTC][0]
    w = x.iloc[s:s + N_STEPS]
    out = crash_stats(w.close.values, w.depth_btc.values, w.spread_bps_max.values)
    out["depth_btc_median"] = float(w.depth_btc.median())
    return out


# ---------------------------------------------------------------- simulator
def run_sim(name, seed):
    cfg = SimConfig(n_steps=N_STEPS, step_minutes=1.0, seed=seed, initial_collateral=1000.0)
    ex = Exchange(cfg, SCENARIOS[name])
    pop = make_crowd(seed, n_total=50)
    open_accounts(ex, pop)
    for _ in range(N_STEPS):
        acts = []
        for b in pop.bots:
            acts += b.act(ex.view(b.account_id))
        ex.step(acts)
    return ex.records


def sim_stats(recs, crash):
    p = np.array([r.last_price for r in recs])
    depth = np.array([r.depth_bid + r.depth_ask for r in recs])
    spread_bps = np.array([r.spread_rel for r in recs]) * 1e4
    if crash:
        out = crash_stats(p, depth, spread_bps)
        out["liquidations"] = int(sum(r.liq_count for r in recs))
        return out
    ret = np.concatenate([[np.nan], np.diff(np.log(p))])
    flow = np.array([r.signed_volume for r in recs], float)
    vol = np.array([r.volume for r in recs], float)
    out = impact_stats(ret, flow, vol)
    out.update(shock_stats(ret, depth))
    out["spread_gt_1bps"] = float((spread_bps > 1.0).mean())
    out["depth_btc_median"] = float(np.median(depth))
    return out


def avg(rows):
    keys = rows[0].keys()
    return {k: float(np.nanmean([r[k] for r in rows])) for k in keys}


def band(name, sim, real, lo, hi):
    ratio = sim / real if real else float("nan")
    return {"metric": name, "sim": sim, "real": real, "sim_over_real": ratio,
            "within_band": bool(lo <= ratio <= hi), "band": f"{lo}-{hi}x"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=10)
    args = ap.parse_args()
    seeds = [SEEDS0 + i for i in range(args.seeds)]

    tg = json.load(open(os.path.join(PROCESSED_DIR, "calibration_targets.json")))
    train_days, held_days = tg["normal"]["training_days"], tg["held_out"]["days"]
    d = load_real()

    real = {"train": real_calm(d, train_days), "held_out": real_calm(d, held_days), "crash": real_crash(d)}
    sim = {}
    for scn in ["normal-thick-synthetic-no-event", "normal-thin-synthetic-no-event"]:
        sim[scn] = avg([sim_stats(run_sim(scn, s), crash=False) for s in seeds])
    for scn in ["stress-thick-replay", "stress-thin-replay"]:
        sim[scn] = avg([sim_stats(run_sim(scn, s), crash=True) for s in seeds])

    print("=" * 74)
    print(f"Microstructure: simulator vs real ({len(seeds)} fresh seeds)")
    print("=" * 74)
    cmp_ = {}
    ref = real["held_out"]
    for scn in ["normal-thick-synthetic-no-event", "normal-thin-synthetic-no-event"]:
        s = sim[scn]
        rows = [band("price_impact_norm", s["price_impact_norm"], ref["price_impact_norm"], 0.5, 2.0),
                band("impact_r2", s["impact_r2"], ref["impact_r2"], 0.5, 2.0),
                band("depth_dip (lower = deeper dip)", s["depth_dip"], ref["depth_dip"], 0.8, 1.25),
                band("recovery_min", s["recovery_min"], ref["recovery_min"], 0.5, 2.0)]
        cmp_[scn] = rows
        print(f"\n{scn}  vs real held-out days")
        for r in rows:
            print(f"  {r['metric']:<32} sim {r['sim']:>10.5f}  real {r['real']:>10.5f}  x{r['sim_over_real']:>7.2f}  "
                  f"{'CLOSE' if r['within_band'] else 'OFF'} ({r['band']})")
        print(f"  {'spread > 1 bp (share of minutes)':<32} sim {s['spread_gt_1bps']:>10.4f}  real {ref['spread_gt_1bps']:>10.4f}")
    rc = real["crash"]
    print("\nCrash window (real 10 Oct 12:00 UTC +600 min vs historical replay scenarios)")
    print(f"  {'':<22}{'depth min/pre':>15}{'max spread bp':>16}{'worst 1-min':>13}{'max DD':>10}{'liqs':>7}")
    print(f"  {'REAL':<22}{rc['depth_min_vs_pre']:>15.3f}{rc['spread_max_bps']:>16.2f}{rc['worst_1min_return']:>13.4f}{rc['max_drawdown']:>10.4f}{'n/a':>7}")
    for scn in ["stress-thick-replay", "stress-thin-replay"]:
        s = sim[scn]
        print(f"  {scn:<22}{s['depth_min_vs_pre']:>15.3f}{s['spread_max_bps']:>16.2f}{s['worst_1min_return']:>13.4f}{s['max_drawdown']:>10.4f}{s['liquidations']:>7.1f}")
        cmp_[scn] = {"worst_1min_sim_over_real": s["worst_1min_return"] / rc["worst_1min_return"],
                     "max_dd_sim_over_real": s["max_drawdown"] / rc["max_drawdown"]}

    out = {"seeds": seeds, "real": real, "sim": sim, "comparison": cmp_,
           "note": "Diagnostic only; bands chosen after a first look. No real liquidation data."}
    path = os.path.join(PROCESSED_DIR, "validation_microstructure.json")
    json.dump(out, open(path, "w"), indent=2)
    print(f"\nSaved to {path}")


if __name__ == "__main__":
    main()
