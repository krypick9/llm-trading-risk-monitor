"""
Process downloaded raw data into calibration targets.

Reads:
  raw/binance/aggTrades/*.csv      — aggregated trades (price, volume, timestamp)
  raw/binance/bookDepth/*.csv      — order book depth snapshots
  raw/hyperliquid/funding_*.json   — funding rate history
  raw/hyperliquid/orderbook_*.json — live order book snapshot
  raw/manifest.json                 — what was downloaded

Computes and outputs:
  data/processed/calibration_targets.json — the numbers to match in the simulator

Calibration targets produced:
  - sigma_hourly:       hourly realized volatility (normal vs stress)
  - jump_rate_per_hour: frequency of jumps > 2 sigma
  - jump_mean:          mean jump size (for negative jumps)
  - jump_std:           std of jump sizes
  - spread_rel:         relative bid-ask spread (median, p5, p95)
  - depth_btc:          order book depth within 0.5% of mid (median, normal vs stress)
  - funding_mean:       mean funding rate per hour
  - funding_std:        std of funding rate
  - funding_cap:        max absolute funding rate observed
  - return_kurtosis:    kurtosis of minute returns (should be > 3 = fat tails)
  - shock_examples:     largest intraday drops observed (for shock_return calibration)

Usage:
  python compute_calibration_targets.py
"""

import os
import sys
import json
import math
import glob
from datetime import datetime, timedelta
from collections import defaultdict

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "raw"))
PROCESSED_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "data", "processed"))
os.makedirs(PROCESSED_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_manifest():
    path = os.path.join(RAW_DIR, "manifest.json")
    if not os.path.exists(path):
        print("[error] manifest.json not found. Run download_data.py first.")
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


def ts_to_ms(ts_str):
    """Parse a timestamp string to milliseconds."""
    try:
        return int(float(ts_str))
    except (ValueError, TypeError):
        return None

# ---------------------------------------------------------------------------
# 1. Aggregated trades -> minute bars -> volatility, jumps, kurtosis
# ---------------------------------------------------------------------------

def process_agg_trades(csv_path: str, day: str) -> dict:
    """Process one day of agg trades into minute bars and compute stats.
    
    Binance aggTrades columns:
      agg_id, price, quantity, first_trade_id, last_trade_id, 
      transact_time (ms), is_buyer_maker
    """
    print(f"  [process] {os.path.basename(csv_path)}")
    
    # Read CSV — column names vary slightly, handle gracefully
    try:
        df = pd.read_csv(csv_path, header=None,
                         names=["agg_id", "price", "qty", "first_id", "last_id", "time_ms", "is_maker"],
                         dtype={"price": float, "qty": float, "time_ms": int})
    except Exception:
        try:
            df = pd.read_csv(csv_path)
            if "price" not in df.columns or "quantity" not in df.columns:
                df = pd.read_csv(csv_path, header=None)
                df.columns = ["agg_id", "price", "qty", "first_id", "last_id", "time_ms", "is_maker"]
                df["price"] = df["price"].astype(float)
                df["qty"] = df["qty"].astype(float)
                df["time_ms"] = df["time_ms"].astype(int)
            else:
                df["qty"] = df["quantity"].astype(float)
                if "transact_time" in df.columns:
                    df["time_ms"] = df["transact_time"].astype(int)
                elif "timestamp" in df.columns:
                    df["time_ms"] = df["timestamp"].astype(int)
                if "is_buyer_maker" in df.columns:
                    df["is_maker"] = df["is_buyer_maker"]
        except Exception as e:
            print(f"  [error] reading {csv_path}: {e}")
            return None
    
    if "price" not in df.columns or len(df) == 0:
        print(f"  [warn] no usable price data in {csv_path}")
        return None
    
    # Convert timestamp to datetime
    if "time_ms" in df.columns:
        ts_col = "time_ms"
    elif "transact_time" in df.columns:
        ts_col = "transact_time"
    elif "timestamp" in df.columns:
        ts_col = "timestamp"
    else:
        print(f"  [warn] no timestamp column found, using row order")
        return None
    
    df["datetime"] = pd.to_datetime(df[ts_col], unit="ms", utc=True)
    df = df.sort_values("datetime")
    
    # Resample to 1-minute bars
    df.set_index("datetime", inplace=True)
    minute_bars = df["price"].resample("1min").ohlc()
    minute_bars.columns = ["open", "high", "low", "close"]
    minute_volumes = df["qty"].resample("1min").sum()
    
    # Drop empty minutes (no trades)
    minute_bars = minute_bars.dropna()
    
    if len(minute_bars) < 60:
        print(f"  [warn] only {len(minute_bars)} minutes of data")
    
    # Compute minute log returns
    returns = np.log(minute_bars["close"] / minute_bars["close"].shift(1)).dropna()
    
    # --- Hourly volatility ---
    # Group returns into hourly chunks, compute std, then annualize to per-hour
    hourly_vols = []
    for hour_start, group in returns.groupby(returns.index.floor("h")):
        if len(group) >= 30:  # need at least 30 minutes of data
            vol = group.std() * math.sqrt(60)  # scale minute std to hourly
            hourly_vols.append(vol)
    
    if not hourly_vols:
        hourly_vols = [returns.std() * math.sqrt(60)]
    
    sigma_hourly = float(np.median(hourly_vols))
    sigma_p90 = float(np.percentile(hourly_vols, 90))
    sigma_p10 = float(np.percentile(hourly_vols, 10))
    
    # --- Jumps: returns > 3 std ---
    std_returns = returns.std()
    jumps = returns[abs(returns) > 3 * std_returns]
    hours_of_data = len(minute_bars) / 60.0
    
    if hours_of_data > 0 and len(jumps) > 0:
        jump_rate = len(jumps) / hours_of_data
        # Separate negative and positive jumps
        neg_jumps = jumps[jumps < 0]
        pos_jumps = jumps[jumps > 0]
        
        if len(neg_jumps) > 0:
            jump_mean = float(neg_jumps.mean())
            jump_std = float(neg_jumps.std()) if len(neg_jumps) > 1 else 0.002
        else:
            jump_mean = -0.001
            jump_std = 0.002
    else:
        jump_rate = 0.2
        jump_mean = 0.0
        jump_std = 0.002
    
    # --- Kurtosis (fat tails) ---
    kurtosis = float(returns.kurtosis()) if len(returns) > 10 else 0.0
    
    # --- Largest intraday drop (for shock calibration) ---
    if len(minute_bars) >= 10:
        cumulative = minute_bars["close"]
        max_drop = 0.0
        peak = cumulative.iloc[0]
        for p in cumulative:
            peak = max(peak, p)
            drop = (p - peak) / peak
            max_drop = min(max_drop, drop)
        shock_example = float(max_drop)
    else:
        shock_example = -0.02
    
    # --- Buyer-maker ratio (order flow) ---
    if "is_maker" in df.columns:
        is_maker = df["is_maker"].astype(bool)
        sell_volume = df.loc[is_maker, "qty"].sum()  # buyer is maker => seller is taker => sell
        buy_volume = df.loc[~is_maker, "qty"].sum()
        total = sell_volume + buy_volume
        ofi = float((buy_volume - sell_volume) / total) if total > 0 else 0.0
    else:
        ofi = 0.0
    
    return {
        "day": day,
        "n_trades": len(df),
        "n_minutes": len(minute_bars),
        "sigma_hourly": sigma_hourly,
        "sigma_p10": sigma_p10,
        "sigma_p90": sigma_p90,
        "jump_rate_per_hour": float(jump_rate),
        "jump_mean": jump_mean,
        "jump_std": jump_std,
        "return_kurtosis": kurtosis,
        "shock_example": shock_example,
        "order_flow_imbalance": ofi,
        "price_open": float(minute_bars["open"].iloc[0]),
        "price_close": float(minute_bars["close"].iloc[-1]),
        "total_volume_btc": float(df["qty"].sum()),
    }


# ---------------------------------------------------------------------------
# 2. Order book depth snapshots -> spread, depth
# ---------------------------------------------------------------------------

def process_book_depth(csv_path: str, day: str) -> dict:
    """Process order book depth snapshots.
    
    Binance bookDepth CSV format:
      timestamp, percentage, depth (BTC), notional (USD)
    Where percentage = distance from mid price in basis points (negative = bid side)
    Each row = one snapshot at one percentage level.
    Multiple rows with same timestamp = one full snapshot.
    """
    print(f"  [process] {os.path.basename(csv_path)}")
    
    try:
        df = pd.read_csv(csv_path)
    except Exception as e:
        print(f"  [error] reading {csv_path}: {e}")
        return None
    
    if len(df) == 0:
        return None
    
    # Check expected columns
    if "percentage" not in df.columns or "depth" not in df.columns:
        print(f"  [warn] unexpected columns: {list(df.columns)}")
        return None
    
    # Group by timestamp to get individual snapshots
    # Within each snapshot, percentage ranges from -20 to +20 (bps from mid)
    # We want depth within 0.5% = 50 bps of mid
    spreads = []
    depths_05pct = []
    depths_bid = []
    depths_ask = []
    
    for ts, group in df.groupby("timestamp"):
        # Depth within 0.5% (50 bps) of mid
        bid_depth = group[(group["percentage"] >= -50) & (group["percentage"] < 0)]["depth"].sum()
        ask_depth = group[(group["percentage"] > 0) & (group["percentage"] <= 50)]["depth"].sum()
        
        total_05 = bid_depth + ask_depth
        if total_05 > 0:
            depths_bid.append(float(bid_depth))
            depths_ask.append(float(ask_depth))
            depths_05pct.append(float(total_05))
            
            # Estimate spread from the closest bid/ask levels
            closest_bid = group[group["percentage"] < 0]["percentage"].max() if len(group[group["percentage"] < 0]) > 0 else -1
            closest_ask = group[group["percentage"] > 0]["percentage"].min() if len(group[group["percentage"] > 0]) > 0 else 1
            # Spread in bps -> relative
            spread_bps = abs(closest_ask - closest_bid)
            spreads.append(spread_bps / 10000.0)  # bps to fraction
    
    if not depths_05pct:
        print(f"  [warn] no usable depth data in {csv_path}")
        return None
    
    return {
        "day": day,
        "n_snapshots": len(depths_05pct),
        "spread_rel_median": float(np.median(spreads)),
        "spread_rel_p5": float(np.percentile(spreads, 5)),
        "spread_rel_p95": float(np.percentile(spreads, 95)),
        "depth_bid_median": float(np.median(depths_bid)),
        "depth_ask_median": float(np.median(depths_ask)),
        "depth_total_median": float(np.median(depths_05pct)),
        "depth_total_p10": float(np.percentile(depths_05pct, 10)),
        "depth_total_p90": float(np.percentile(depths_05pct, 90)),
    }


# ---------------------------------------------------------------------------
# 3. Funding rates -> mean, std, cap
# ---------------------------------------------------------------------------

def process_funding(funding_path: str) -> dict:
    """Process funding rate history."""
    if not funding_path or not os.path.exists(funding_path):
        print(f"  [warn] no funding data, using estimates")
        return {
            "funding_mean": 0.0000125,
            "funding_std": 0.0001,
            "funding_cap": 0.04,
            "n_records": 0,
        }
    
    with open(funding_path) as f:
        data = json.load(f)
    
    if isinstance(data, list) and len(data) > 0:
        # Could be Hyperliquid format or Binance format
        rates = []
        for rec in data:
            if isinstance(rec, dict):
                rate = rec.get("fundingRate") or rec.get("funding_rate") or rec.get("rate")
                if rate is not None:
                    rates.append(float(rate))
        
        if not rates:
            return {"funding_mean": 0.0000125, "funding_std": 0.0001, "funding_cap": 0.04, "n_records": 0}
        
        rates = np.array(rates)
        return {
            "funding_mean": float(np.mean(rates)),
            "funding_std": float(np.std(rates)),
            "funding_cap": float(max(abs(rates.min()), abs(rates.max()))),
            "funding_p95": float(np.percentile(np.abs(rates), 95)),
            "n_records": len(rates),
        }
    
    return {"funding_mean": 0.0000125, "funding_std": 0.0001, "funding_cap": 0.04, "n_records": 0}


# ---------------------------------------------------------------------------
# 4. Hyperliquid live order book (depth reference)
# ---------------------------------------------------------------------------

def process_hyperliquid_orderbook() -> dict:
    """Process live Hyperliquid order book snapshot for BTC depth reference.
    
    Hyperliquid l2Book format:
      {"coin": "BTC", "time": ..., "levels": [[bid_levels], [ask_levels]]}
    where each level is {"px": "78000", "sz": "1.5", "n": 3}
    levels[0] = bids (sorted descending), levels[1] = asks (sorted ascending)
    """
    path = os.path.join(RAW_DIR, "hyperliquid", "orderbook_snapshot.json")
    if not os.path.exists(path):
        return None
    
    with open(path) as f:
        data = json.load(f)
    
    levels = data.get("levels", [])
    if not levels or len(levels) < 2:
        return None
    
    bid_levels = levels[0] if isinstance(levels[0], list) else []
    ask_levels = levels[1] if isinstance(levels[1], list) else []
    
    bids = [(float(l["px"]), float(l["sz"])) for l in bid_levels if "px" in l and "sz" in l]
    asks = [(float(l["px"]), float(l["sz"])) for l in ask_levels if "px" in l and "sz" in l]
    
    if not bids or not asks:
        return None
    
    best_bid = max(b[0] for b in bids)
    best_ask = min(a[0] for a in asks)
    mid = (best_bid + best_ask) / 2
    spread = (best_ask - best_bid) / mid
    
    depth_bid_05 = sum(sz for px, sz in bids if (mid - px) / mid <= 0.005)
    depth_ask_05 = sum(sz for px, sz in asks if (px - mid) / mid <= 0.005)
    
    return {
        "source": "hyperliquid_live",
        "mid_price": float(mid),
        "spread_rel": float(spread),
        "depth_bid_05pct": float(depth_bid_05),
        "depth_ask_05pct": float(depth_ask_05),
        "depth_total_05pct": float(depth_bid_05 + depth_ask_05),
        "n_bid_levels": len(bids),
        "n_ask_levels": len(asks),
    }


# ---------------------------------------------------------------------------
# Main: aggregate everything into calibration targets
# ---------------------------------------------------------------------------

def main():
    manifest = load_manifest()
    
    print("=" * 60)
    print("Calibration Target Extraction")
    print("=" * 60)
    
    dates = manifest.get("dates", {})
    trade_files = manifest.get("trade_files", {})
    depth_files = manifest.get("depth_files", {})
    funding_file = manifest.get("funding_file")
    
    # Separate normal and stress dates
    normal_days = [d for d, regime in dates.items() if regime == "normal"]
    stress_days = [d for d, regime in dates.items() if regime == "stress"]
    
    print(f"\nNormal days: {normal_days}")
    print(f"Stress days: {stress_days}")
    
    # Process trades
    print(f"\n--- Processing aggregated trades ---")
    trade_stats = {}
    for day, path in trade_files.items():
        stats = process_agg_trades(path, day)
        if stats:
            trade_stats[day] = stats
    
    # Process depth
    print(f"\n--- Processing order book depth ---")
    depth_stats = {}
    for day, path in depth_files.items():
        stats = process_book_depth(path, day)
        if stats:
            depth_stats[day] = stats
    
    # Process funding
    print(f"\n--- Processing funding rates ---")
    funding_stats = process_funding(funding_file)
    
    # Process Hyperliquid live order book
    print(f"\n--- Processing Hyperliquid live order book ---")
    hl_book = process_hyperliquid_orderbook()
    
    # --- Aggregate into normal vs stress ---
    print(f"\n--- Aggregating calibration targets ---")
    
    normal_trade = [trade_stats[d] for d in normal_days if d in trade_stats]
    stress_trade = [trade_stats[d] for d in stress_days if d in trade_stats]
    normal_depth = [depth_stats[d] for d in normal_days if d in depth_stats]
    stress_depth = [depth_stats[d] for d in stress_days if d in depth_stats]
    
    def safe_median(lst, key, default=0):
        vals = [x.get(key, default) for x in lst if x]
        return float(np.median(vals)) if vals else default
    
    def safe_percentile(lst, key, p, default=0):
        vals = [x.get(key, default) for x in lst if x]
        return float(np.percentile(vals, p)) if vals else default
    
    targets = {
        "_meta": {
            "normal_days": normal_days,
            "stress_days": stress_days,
            "n_trade_days": len(trade_stats),
            "n_depth_days": len(depth_stats),
            "generated_at": datetime.utcnow().isoformat(),
        },
        "normal": {
            "sigma_hourly": safe_median(normal_trade, "sigma_hourly", 0.005),
            "sigma_p90": safe_percentile(normal_trade, "sigma_hourly", 90, 0.008),
            "jump_rate_per_hour": safe_median(normal_trade, "jump_rate_per_hour", 0.2),
            "jump_mean": safe_median(normal_trade, "jump_mean", 0.0),
            "jump_std": safe_median(normal_trade, "jump_std", 0.002),
            "shock_return": safe_percentile(normal_trade, "shock_example", 10, -0.008),
            "spread_rel": safe_median(normal_depth, "spread_rel_median", 0.0001),
            "depth_btc": safe_median(normal_depth, "depth_total_median", 100.0),
            "depth_p10": safe_percentile(normal_depth, "depth_total_median", 10, 50.0),
        },
        "stress": {
            "sigma_hourly": safe_median(stress_trade, "sigma_hourly", 0.012),
            "sigma_p90": safe_percentile(stress_trade, "sigma_hourly", 90, 0.025),
            "jump_rate_per_hour": safe_median(stress_trade, "jump_rate_per_hour", 1.0),
            "jump_mean": safe_median(stress_trade, "jump_mean", -0.001),
            "jump_std": safe_median(stress_trade, "jump_std", 0.005),
            "shock_return": safe_percentile(stress_trade, "shock_example", 10, -0.06),
            "spread_rel": safe_median(stress_depth, "spread_rel_median", 0.0002),
            "depth_btc": safe_median(stress_depth, "depth_total_median", 50.0),
            "depth_p10": safe_percentile(stress_depth, "depth_total_median", 10, 10.0),
        },
        "funding": funding_stats,
        "stylized_facts": {
            "return_kurtosis_normal": safe_median(normal_trade, "return_kurtosis", 3.0),
            "return_kurtosis_stress": safe_median(stress_trade, "return_kurtosis", 5.0),
        },
        "hyperliquid_reference": hl_book,
        "raw_trade_stats": {d: {k: v for k, v in s.items()} for d, s in trade_stats.items()},
        "raw_depth_stats": {d: {k: v for k, v in s.items()} for d, s in depth_stats.items()},
    }
    
    # Save
    out_path = os.path.join(PROCESSED_DIR, "calibration_targets.json")
    with open(out_path, "w") as f:
        json.dump(targets, f, indent=2, default=str)
    
    print(f"\n{'=' * 60}")
    print("Calibration targets computed:")
    print(f"\n  Normal regime:")
    print(f"    sigma_hourly:     {targets['normal']['sigma_hourly']:.5f} ({targets['normal']['sigma_hourly']*100:.2f}%/h)")
    print(f"    jump_rate/hour:   {targets['normal']['jump_rate_per_hour']:.2f}")
    print(f"    spread_rel:       {targets['normal']['spread_rel']:.6f} ({targets['normal']['spread_rel']*100:.4f}%)")
    print(f"    depth_btc:        {targets['normal']['depth_btc']:.1f} BTC")
    print(f"    shock_return:     {targets['normal']['shock_return']:.4f} ({targets['normal']['shock_return']*100:.2f}%)")
    
    print(f"\n  Stress regime:")
    print(f"    sigma_hourly:     {targets['stress']['sigma_hourly']:.5f} ({targets['stress']['sigma_hourly']*100:.2f}%/h)")
    print(f"    jump_rate/hour:   {targets['stress']['jump_rate_per_hour']:.2f}")
    print(f"    spread_rel:       {targets['stress']['spread_rel']:.6f} ({targets['stress']['spread_rel']*100:.4f}%)")
    print(f"    depth_btc:        {targets['stress']['depth_btc']:.1f} BTC")
    print(f"    shock_return:     {targets['stress']['shock_return']:.4f} ({targets['stress']['shock_return']*100:.2f}%)")
    
    print(f"\n  Funding:")
    print(f"    mean:             {targets['funding']['funding_mean']:.8f}/h")
    print(f"    cap:              {targets['funding']['funding_cap']:.6f}/h")
    
    print(f"\n  Stylized facts:")
    print(f"    kurtosis (normal):{targets['stylized_facts']['return_kurtosis_normal']:.2f} (should be > 3)")
    print(f"    kurtosis (stress):{targets['stylized_facts']['return_kurtosis_stress']:.2f} (should be > 3)")
    
    if hl_book:
        print(f"\n  Hyperliquid live reference:")
        print(f"    mid_price:        ${hl_book['mid_price']:,.0f}")
        print(f"    spread_rel:       {hl_book['spread_rel']:.8f}")
        print(f"    depth_05pct:      {hl_book['depth_total_05pct']:.1f} BTC")
    
    print(f"\nSaved to {out_path}")
    print(f"\nNext step: run run_calibration.py")


if __name__ == "__main__":
    main()
