"""
Process real data into calibration targets — v2 (post-TA-meeting fixes).

Key changes from v1:
  Q1 fix: Spread and depth now come from teammate's Bybit orderbook data
          (spread_bps_mean, near_depth_usd) instead of the broken Binance
          bookDepth band semantics. Binance bookDepth percentage bands are
          ±1..±5 PERCENT (not bps), so the old code always computed |1-(-1)|=2 bps.
  Q2 fix: Shock is now the max 1-min return from real data (not daily drawdown).
          Daily drawdown (-13.15%) was being applied as a single-minute jump.
  Q4 fix: Zero-return statistics are computed and reported (not discarded).
  Q6 fix: Data is split into training (first 16 days) and held-out (last 6 days)
          for out-of-sample validation.

Data sources:
  1. Teammate's 22-day Bybit orderbook CSV (spread, depth, mid price)
  2. Teammate's 22-day Binance aggTrades 1-min CSV (OHLCV, volume)
  3. Our 6-day Binance Vision raw data (fallback for funding rates)

Usage:
  python compute_calibration_targets.py
"""

import os
import sys
import json
import math
from datetime import datetime

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", ".."))
REFERENCE_DIR = os.path.join(REPO_ROOT, "data", "reference")
RAW_DIR = os.path.join(REPO_ROOT, "raw")
PROCESSED_DIR = os.path.join(REPO_ROOT, "data", "processed")
os.makedirs(PROCESSED_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def safe_std(arr, ddof=0):
    if len(arr) < 2:
        return 0.0
    return float(np.std(arr, ddof=ddof))


def safe_mean(arr):
    return float(np.mean(arr)) if len(arr) > 0 else 0.0


def safe_median(arr):
    return float(np.median(arr)) if len(arr) > 0 else 0.0


def safe_percentile(arr, p):
    return float(np.percentile(arr, p)) if len(arr) > 0 else 0.0


def excess_kurtosis(arr):
    arr = np.array(arr)
    n = len(arr)
    if n < 4:
        return 0.0
    mean = np.mean(arr)
    std = np.std(arr, ddof=1)
    if std == 0:
        return 0.0
    return float((1 / n) * np.sum(((arr - mean) / std) ** 4) - 3.0)


def autocorrelation(x, lag=1):
    x = np.array(x)
    n = len(x)
    if n <= lag:
        return 0.0
    mean = np.mean(x)
    c0 = np.mean((x - mean) ** 2)
    ck = np.mean((x[:n - lag] - mean) * (x[lag:] - mean))
    return float(ck / c0) if c0 > 0 else 0.0


# ---------------------------------------------------------------------------
# 1. Load teammate's 22-day reference data
# ---------------------------------------------------------------------------

def load_reference_data():
    """Load teammate's Bybit orderbook + Binance 1-min OHLCV data."""
    bybit_path = os.path.join(REFERENCE_DIR, "bybit_orderbook_BTCUSDT_1min.csv")
    binance_path = os.path.join(REFERENCE_DIR, "binance_aggTrades_BTCUSDT_1min.csv")

    if not os.path.exists(bybit_path):
        print(f"[error] Bybit data not found: {bybit_path}")
        sys.exit(1)
    if not os.path.exists(binance_path):
        print(f"[error] Binance data not found: {binance_path}")
        sys.exit(1)

    print("Loading teammate reference data...")
    bybit = pd.read_csv(bybit_path, parse_dates=["ts_utc"])
    binance = pd.read_csv(binance_path, parse_dates=["ts_utc"])

    print(f"  Bybit:   {len(bybit)} rows, {bybit.ts_utc.dt.date.nunique()} days")
    print(f"  Binance: {len(binance)} rows, {binance.ts_utc.dt.date.nunique()} days")

    # Merge on timestamp (inner join — both sources should have the same minutes)
    merged = pd.merge_asof(
        bybit.sort_values("ts_utc"),
        binance[["ts_utc", "close", "vol_usd", "signed_usd", "n_trades"]].sort_values("ts_utc"),
        on="ts_utc",
        direction="nearest",
        tolerance=pd.Timedelta("1min"),
    )
    merged = merged.dropna(subset=["close"])
    print(f"  Merged:  {len(merged)} rows, {merged.ts_utc.dt.date.nunique()} days")
    print(f"  Date range: {merged.ts_utc.min()} to {merged.ts_utc.max()}")

    return merged


# ---------------------------------------------------------------------------
# 2. Compute calibration targets from merged data
# ---------------------------------------------------------------------------

def compute_targets(df):
    """Compute all calibration targets from the merged 22-day dataset."""

    df = df.sort_values("ts_utc").reset_index(drop=True)
    df["date"] = df.ts_utc.dt.date

    # --- 1-min log returns (KEEP zeros — Q4 fix) ---
    returns = np.diff(np.log(df["close"].values))
    returns_all = returns  # keep zeros
    returns_nonzero = returns[returns != 0]  # also compute non-zero variant for comparison

    # --- Split into training and held-out ---
    all_dates = sorted(df["date"].unique())
    n_days = len(all_dates)
    # Training: first ~70% of days, Held-out: last ~30%
    split_idx = int(n_days * 0.7)
    train_dates = set(all_dates[:split_idx])
    held_dates = set(all_dates[split_idx:])

    print(f"\n  Training days ({len(train_dates)}): {sorted(train_dates)}")
    print(f"  Held-out days ({len(held_dates)}): {sorted(held_dates)}")

    df_train = df[df["date"].isin(train_dates)]
    df_held = df[df["date"].isin(held_dates)]

    # --- Split into normal vs stress regimes ---
    # Oct 8-10 = crash (stress); everything else = normal
    stress_dates = {d for d in all_dates if hasattr(d, 'month') and d.month == 10 and 8 <= d.day <= 10}
    normal_dates_train = train_dates - stress_dates
    stress_dates_train = train_dates & stress_dates

    print(f"  Normal training days: {sorted(normal_dates_train)}")
    print(f"  Stress training days: {sorted(stress_dates_train)}")

    # --- Compute regime-specific stats ---
    targets = {}

    for regime, dates_set in [("normal", normal_dates_train), ("stress", stress_dates_train)]:
        if not dates_set:
            print(f"  [warn] no {regime} training days, using all training data")
            dates_set = train_dates

        sub = df[df["date"].isin(dates_set)].sort_values("ts_utc")
        if len(sub) < 60:
            print(f"  [warn] only {len(sub)} rows for {regime}")
            continue

        # Returns
        close_prices = sub["close"].values
        regime_rets = np.diff(np.log(close_prices))

        # Hourly volatility (keep zeros)
        hourly_vols = []
        for i in range(0, len(regime_rets), 60):
            chunk = regime_rets[i:i + 60]
            if len(chunk) >= 30:
                hourly_vols.append(np.std(chunk) * math.sqrt(60))

        sigma_hourly = safe_median(hourly_vols)
        sigma_p90 = safe_percentile(hourly_vols, 90)

        # Jumps: returns beyond 3 std (keep zeros)
        std_r = np.std(regime_rets)
        jumps = regime_rets[np.abs(regime_rets) > 3 * std_r] if std_r > 0 else np.array([])
        hours = len(regime_rets) / 60.0
        jump_rate = len(jumps) / hours if hours > 0 else 0

        neg_jumps = jumps[jumps < 0]
        pos_jumps = jumps[jumps > 0]
        jump_mean = float(np.mean(neg_jumps)) if len(neg_jumps) > 0 else 0.0
        jump_std = float(np.std(neg_jumps)) if len(neg_jumps) > 1 else 0.0

        # Kurtosis (both with and without zeros — Q4 fix)
        kurt_with_zeros = excess_kurtosis(regime_rets)
        kurt_without_zeros = excess_kurtosis(regime_rets[regime_rets != 0])

        # Volatility clustering (ACF of squared returns — both variants)
        sq_rets_all = regime_rets ** 2
        sq_rets_nonzero = regime_rets[regime_rets != 0] ** 2
        acf1_with_zeros = autocorrelation(sq_rets_all, lag=1)
        acf1_without_zeros = autocorrelation(sq_rets_nonzero, lag=1)

        # Order flow imbalance ACF
        ofi = sub["signed_usd"].values / (sub["vol_usd"].values + 1e-12)
        ofi = ofi[~np.isnan(ofi)]
        ofi_acf1 = autocorrelation(ofi, lag=1) if len(ofi) > 2 else 0.0

        # Spread (from Bybit spread_bps_mean — Q1 fix)
        spread_bps = sub["spread_bps_mean"].values
        spread_rel_median = safe_median(spread_bps) / 10000.0  # bps to fraction
        spread_rel_p5 = safe_percentile(spread_bps, 5) / 10000.0
        spread_rel_p95 = safe_percentile(spread_bps, 95) / 10000.0

        # Depth (from Bybit near_depth_usd, converted to BTC — Q1 fix)
        depth_btc = sub["near_depth_usd"].values / sub["mid_bybit"].values
        depth_median = safe_median(depth_btc)
        depth_p10 = safe_percentile(depth_btc, 10)
        depth_p90 = safe_percentile(depth_btc, 90)

        # Shock: max 1-min negative return (Q2 fix — not daily drawdown!)
        max_neg_1min = float(regime_rets.min())
        max_pos_1min = float(regime_rets.max())

        # Also compute daily drawdown for reference (but don't use as shock)
        cum = np.maximum.accumulate(close_prices)
        dd = (close_prices - cum) / cum
        max_drawdown = float(dd.min())

        # Zero returns stats
        n_zero = int(np.sum(regime_rets == 0))
        zero_pct = n_zero / len(regime_rets) * 100 if len(regime_rets) > 0 else 0

        targets[regime] = {
            "sigma_hourly": sigma_hourly,
            "sigma_p90": sigma_p90,
            "jump_rate_per_hour": float(jump_rate),
            "jump_mean": jump_mean,
            "jump_std": jump_std,
            # Q2 fix: shock is the max 1-min return, NOT daily drawdown
            "shock_return_1min": max_neg_1min,
            "shock_return_1min_pct": max_neg_1min * 100,
            # For reference only (what the old code used):
            "max_drawdown_daily": max_drawdown,
            "max_drawdown_daily_pct": max_drawdown * 100,
            # Spread (Q1 fix: from Bybit spread_bps_mean)
            "spread_bps_median": safe_median(spread_bps),
            "spread_rel": spread_rel_median,
            "spread_rel_p5": spread_rel_p5,
            "spread_rel_p95": spread_rel_p95,
            # Depth (Q1 fix: from Bybit near_depth within ~0.5%)
            "depth_btc": depth_median,
            "depth_p10": depth_p10,
            "depth_p90": depth_p90,
            # Q4 fix: kurtosis both with and without zeros
            "kurtosis_with_zeros": kurt_with_zeros,
            "kurtosis_without_zeros": kurt_without_zeros,
            "vol_clustering_acf1_with_zeros": acf1_with_zeros,
            "vol_clustering_acf1_without_zeros": acf1_without_zeros,
            "order_flow_acf1": ofi_acf1,
            "zero_return_pct": zero_pct,
            "n_zero_returns": n_zero,
            "n_total_returns": len(regime_rets),
            "max_pos_1min_return": max_pos_1min,
            "training_days": sorted(str(d) for d in dates_set),
        }

    # --- Full dataset stats ---
    all_hourly_vols = []
    for i in range(0, len(returns_all), 60):
        chunk = returns_all[i:i + 60]
        if len(chunk) >= 30:
            all_hourly_vols.append(np.std(chunk) * math.sqrt(60))

    targets["full_dataset"] = {
        "n_days": n_days,
        "n_minutes": len(df),
        "n_returns": len(returns_all),
        "sigma_hourly": safe_median(all_hourly_vols),
        "kurtosis_with_zeros": excess_kurtosis(returns_all),
        "kurtosis_without_zeros": excess_kurtosis(returns_nonzero),
        "vol_clustering_acf1_with_zeros": autocorrelation(returns_all ** 2, 1),
        "vol_clustering_acf1_without_zeros": autocorrelation(returns_nonzero ** 2, 1),
        "zero_return_pct": float(np.sum(returns_all == 0) / len(returns_all) * 100),
        "max_neg_1min": float(returns_all.min()),
        "max_pos_1min": float(returns_all.max()),
        "max_drawdown": float(((df.close.values - np.maximum.accumulate(df.close.values)) /
                                np.maximum.accumulate(df.close.values)).min()),
    }

    # --- Held-out validation stats ---
    if len(df_held) > 60:
        held_close = df_held["close"].values
        held_rets = np.diff(np.log(held_close))
        held_hourly_vols = []
        for i in range(0, len(held_rets), 60):
            chunk = held_rets[i:i + 60]
            if len(chunk) >= 30:
                held_hourly_vols.append(np.std(chunk) * math.sqrt(60))

        targets["held_out"] = {
            "days": sorted(str(d) for d in held_dates),
            "n_days": len(held_dates),
            "n_minutes": len(df_held),
            "sigma_hourly": safe_median(held_hourly_vols),
            "kurtosis_with_zeros": excess_kurtosis(held_rets),
            "kurtosis_without_zeros": excess_kurtosis(held_rets[held_rets != 0]),
            "vol_clustering_acf1_with_zeros": autocorrelation(held_rets ** 2, 1),
            "vol_clustering_acf1_without_zeros": autocorrelation(held_rets[held_rets != 0] ** 2, 1),
            "zero_return_pct": float(np.sum(held_rets == 0) / len(held_rets) * 100),
            "spread_bps_median": safe_median(df_held["spread_bps_mean"].values),
            "depth_btc_median": safe_median(df_held["near_depth_usd"].values / df_held["mid_bybit"].values),
            "max_neg_1min": float(held_rets.min()),
            "max_drawdown": float(((held_close - np.maximum.accumulate(held_close)) /
                                    np.maximum.accumulate(held_close)).min()),
        }

    # --- Funding (from our raw data if available, otherwise defaults) ---
    funding = load_funding()
    targets["funding"] = funding

    # --- Metadata ---
    targets["_meta"] = {
        "data_source": "teammate_bybit_orderbook + teammate_binance_aggTrades_1min",
        "data_period": f"{df.ts_utc.min()} to {df.ts_utc.max()}",
        "n_days": n_days,
        "train_split": f"first {split_idx} days ({len(train_dates)} days)",
        "held_out_split": f"last {n_days - split_idx} days ({len(held_dates)} days)",
        "stress_definition": "Oct 8-10 2025 (BTC crash period)",
        "zero_returns": "RETAINED in all statistics (Q4 fix)",
        "shock_definition": "max 1-min log return (Q2 fix: NOT daily drawdown)",
        "spread_source": "Bybit spread_bps_mean (Q1 fix: NOT Binance bookDepth artifact)",
        "depth_source": "Bybit near_depth_usd / mid (Q1 fix: NOT full-book sum)",
        "generated_at": datetime.utcnow().isoformat(),
    }

    return targets


# ---------------------------------------------------------------------------
# 3. Funding (from raw data if available)
# ---------------------------------------------------------------------------

def load_funding():
    """Load funding rates from our downloaded Hyperliquid data."""
    funding_path = os.path.join(RAW_DIR, "hyperliquid", "funding_history.json")
    if not os.path.exists(funding_path):
        print(f"  [info] no funding data at {funding_path}, using defaults")
        return {
            "funding_mean": 0.0000125,
            "funding_std": 0.0001,
            "funding_cap": 0.0000125,
            "n_records": 0,
            "source": "default",
        }

    with open(funding_path) as f:
        data = json.load(f)

    if isinstance(data, list) and len(data) > 0:
        rates = []
        for rec in data:
            if isinstance(rec, dict):
                rate = rec.get("fundingRate") or rec.get("funding_rate") or rec.get("rate")
                if rate is not None:
                    rates.append(float(rate))

        if rates:
            rates = np.array(rates)
            return {
                "funding_mean": float(np.mean(rates)),
                "funding_std": float(np.std(rates)),
                "funding_cap": float(max(abs(rates.min()), abs(rates.max()))),
                "funding_p95": float(np.percentile(np.abs(rates), 95)),
                "n_records": len(rates),
                "source": "hyperliquid_api",
            }

    return {
        "funding_mean": 0.0000125,
        "funding_std": 0.0001,
        "funding_cap": 0.0000125,
        "n_records": 0,
        "source": "default",
    }


# ---------------------------------------------------------------------------
# 4. Extract price paths for replay (Q2 fix)
# ---------------------------------------------------------------------------

def extract_replay_paths(df):
    """Extract real 1-min close price paths for historical replay.

    Returns dict with three paths:
      - normal: Sep 24 (calm day, first 600 min)
      - stress: Oct 9 (moderate stress, first 600 min, DD -1.77%)
      - extreme: Oct 10 12:00-22:00 UTC (severe crash, DD -15.47%, max 1-min -5.13%)

    The "extreme" path is critical for triggering liquidations and cascades.
    At 20x leverage with 1% maintenance margin, liquidation triggers at ~6%
    drop from entry. The extreme path has a -15.47% drawdown, ensuring
    that leveraged agents WILL be liquidated.
    """
    df = df.sort_values("ts_utc").reset_index(drop=True)
    df["date"] = df.ts_utc.dt.date

    # Normal: Sep 24 (calm day from training set)
    normal_dates = sorted([d for d in df.date.unique() if not (d.month == 10 and 8 <= d.day <= 10)])
    normal_day = normal_dates[0] if normal_dates else None

    # Stress: Oct 9 (moderate stress from training set)
    stress_day = None
    for d in df.date.unique():
        if d.month == 10 and d.day == 9:
            stress_day = d
            break

    # Extreme: Oct 10, 12:00-22:00 UTC window (largest drawdown window)
    # This is from the held-out set — used specifically for cascade testing
    extreme_day = None
    for d in df.date.unique():
        if d.month == 10 and d.day == 10:
            extreme_day = d
            break

    def _extract_window(day, start_hour=None, n_steps=600):
        """Extract n_steps minutes from a given day, optionally starting at start_hour."""
        if day is None:
            return None
        sub = df[df.date == day].sort_values("ts_utc")
        if start_hour is not None:
            sub = sub[sub.ts_utc.dt.hour >= start_hour]
        prices = list(sub["close"].values)
        if len(prices) < n_steps:
            print(f"  [warn] only {len(prices)} minutes for day {day}, padding")
            while len(prices) < n_steps:
                prices.append(prices[-1])
        return prices[:n_steps]

    def _make_replay_entry(day, prices):
        return {
            "date": str(day),
            "n_steps": len(prices),
            "start_price": float(prices[0]),
            "end_price": float(prices[-1]),
            "max_drawdown": float(min(
                (prices[i] - max(prices[:i + 1])) / max(prices[:i + 1])
                for i in range(len(prices))
            )),
            "min_1min_return": float(min(
                math.log(prices[i + 1] / prices[i])
                for i in range(len(prices) - 1)
            )),
            "prices": [float(p) for p in prices],
        }

    replay = {}

    # Normal path
    if normal_day:
        prices = _extract_window(normal_day, start_hour=None, n_steps=600)
        if prices:
            replay["normal"] = _make_replay_entry(normal_day, prices)
            print(f"  Replay normal: day={normal_day}, 600 steps, "
                  f"start=${prices[0]:.0f}, end=${prices[-1]:.0f}, "
                  f"DD={replay['normal']['max_drawdown']*100:.2f}%")

    # Stress path (moderate — Oct 9 first 600 min)
    if stress_day:
        prices = _extract_window(stress_day, start_hour=None, n_steps=600)
        if prices:
            replay["stress"] = _make_replay_entry(stress_day, prices)
            print(f"  Replay stress: day={stress_day}, 600 steps, "
                  f"start=${prices[0]:.0f}, end=${prices[-1]:.0f}, "
                  f"DD={replay['stress']['max_drawdown']*100:.2f}%")

    # Extreme stress path (Oct 10, 12:00 UTC start — the main crash window)
    if extreme_day:
        prices = _extract_window(extreme_day, start_hour=12, n_steps=600)
        if prices:
            replay["extreme"] = _make_replay_entry(extreme_day, prices)
            print(f"  Replay extreme: day={extreme_day} (12:00 UTC start), 600 steps, "
                  f"start=${prices[0]:.0f}, end=${prices[-1]:.0f}, "
                  f"DD={replay['extreme']['max_drawdown']*100:.2f}%, "
                  f"max 1-min={replay['extreme']['min_1min_return']*100:.2f}%")

    return replay


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("Calibration Target Extraction v2 (post-TA fixes)")
    print("=" * 70)

    # Load data
    df = load_reference_data()

    # Compute targets
    print("\n--- Computing calibration targets ---")
    targets = compute_targets(df)

    # Extract replay paths
    print("\n--- Extracting price paths for historical replay ---")
    replay = extract_replay_paths(df)
    targets["replay_paths"] = replay

    # Save
    out_path = os.path.join(PROCESSED_DIR, "calibration_targets.json")
    with open(out_path, "w") as f:
        json.dump(targets, f, indent=2, default=str)

    # Save replay paths separately for the engine to load
    replay_path = os.path.join(PROCESSED_DIR, "replay_prices.json")
    with open(replay_path, "w") as f:
        json.dump(replay, f, indent=2)

    # Print summary
    print(f"\n{'=' * 70}")
    print("Calibration targets v2 computed:")
    for regime in ["normal", "stress"]:
        if regime not in targets:
            continue
        t = targets[regime]
        print(f"\n  {regime.upper()} regime (training days: {t['training_days']}):")
        print(f"    sigma_hourly:       {t['sigma_hourly']:.6f} ({t['sigma_hourly']*100:.3f}%/h)")
        print(f"    jump_rate/hour:     {t['jump_rate_per_hour']:.3f}")
        print(f"    jump_mean:          {t['jump_mean']:.6f}")
        print(f"    jump_std:           {t['jump_std']:.6f}")
        print(f"    spread_bps:         {t['spread_bps_median']:.4f} bps (Q1 fix: from Bybit)")
        print(f"    spread_rel:         {t['spread_rel']:.8f}")
        print(f"    depth_btc:          {t['depth_btc']:.1f} BTC (Q1 fix: 0.5% band)")
        print(f"    depth_p10:          {t['depth_p10']:.1f} BTC")
        print(f"    shock_1min:         {t['shock_return_1min']:.6f} ({t['shock_return_1min_pct']:.2f}%) (Q2 fix)")
        print(f"    max_drawdown_daily: {t['max_drawdown_daily']:.6f} ({t['max_drawdown_daily_pct']:.2f}%) (reference only)")
        print(f"    kurtosis (w/ zeros):{t['kurtosis_with_zeros']:.2f} (Q4 fix)")
        print(f"    kurtosis (no zeros):{t['kurtosis_without_zeros']:.2f}")
        print(f"    vol_acf1 (w/ zeros):{t['vol_clustering_acf1_with_zeros']:.4f}")
        print(f"    vol_acf1 (no zeros):{t['vol_clustering_acf1_without_zeros']:.4f}")
        print(f"    ofi_acf1:           {t['order_flow_acf1']:.4f}")
        print(f"    zero_return_pct:    {t['zero_return_pct']:.2f}%")

    if "held_out" in targets:
        h = targets["held_out"]
        print(f"\n  HELD-OUT validation ({h['n_days']} days):")
        print(f"    sigma_hourly:       {h['sigma_hourly']:.6f}")
        print(f"    kurtosis (w/ zeros):{h['kurtosis_with_zeros']:.2f}")
        print(f"    vol_acf1 (w/ zeros):{h['vol_clustering_acf1_with_zeros']:.4f}")
        print(f"    spread_bps:         {h['spread_bps_median']:.4f}")
        print(f"    depth_btc:          {h['depth_btc_median']:.1f} BTC")

    print(f"\n  Full dataset: {targets['full_dataset']['n_days']} days, "
          f"{targets['full_dataset']['n_returns']} returns, "
          f"zero_return_pct={targets['full_dataset']['zero_return_pct']:.2f}%")

    print(f"\nSaved to {out_path}")
    print(f"Replay paths saved to {replay_path}")
    print(f"\nNext step: run run_calibration.py")


if __name__ == "__main__":
    main()
