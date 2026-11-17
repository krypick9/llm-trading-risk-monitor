"""Extract empirical calibration targets with an explicit date split.

Normal training: 2025-09-24 through 2025-10-07
Stress training: 2025-10-08 and 2025-10-09
Known crash replay only: 2025-10-10 (excluded from fitting and held-out scoring)
Post-crash diagnostic held-out dates: Oct 11-13 and Oct 20, 2025.

One-minute returns are computed within each calendar day. These selected
post-crash days have already influenced prior analysis and are not a fresh,
untouched validation set.
"""

import json
import math
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", ".."))
REFERENCE_DIR = os.path.join(REPO_ROOT, "data", "reference")
RAW_DIR = os.path.join(REPO_ROOT, "raw")
PROCESSED_DIR = os.path.join(REPO_ROOT, "data", "processed")
os.makedirs(PROCESSED_DIR, exist_ok=True)

NORMAL_TRAIN_DATES = [f"2025-09-{day:02d}" for day in range(24, 31)] + [
    f"2025-10-{day:02d}" for day in range(1, 8)
]
STRESS_TRAIN_DATES = ["2025-10-08", "2025-10-09"]
HELD_OUT_DATES = ["2025-10-11", "2025-10-12", "2025-10-13", "2025-10-20"]
CRASH_REPLAY_DATE = "2025-10-10"


def safe_std(values, ddof=0):
    return float(np.std(values, ddof=ddof)) if len(values) > ddof else 0.0


def safe_mean(values):
    return float(np.mean(values)) if len(values) else 0.0


def safe_median(values):
    return float(np.median(values)) if len(values) else 0.0


def safe_percentile(values, percentile):
    return float(np.percentile(values, percentile)) if len(values) else 0.0


def excess_kurtosis(values):
    values = np.asarray(values, dtype=float)
    if len(values) < 4:
        return 0.0
    sd = np.std(values, ddof=1)
    return float(np.mean(((values - np.mean(values)) / sd) ** 4) - 3.0) if sd else 0.0


def autocorrelation(values, lag=1):
    values = np.asarray(values, dtype=float)
    if len(values) <= lag:
        return 0.0
    centered = values - np.mean(values)
    variance = np.mean(centered ** 2)
    return float(np.mean(centered[:-lag] * centered[lag:]) / variance) if variance else 0.0


def within_day_log_returns(frame):
    """Compute log returns without crossing dates or missing-data gaps."""
    chunks = []
    for _, day in frame.sort_values("ts_utc").groupby("date", sort=True):
        prices = day["close"].to_numpy(dtype=float)
        if len(prices) > 1:
            chunks.append(np.diff(np.log(prices)))
    return np.concatenate(chunks) if chunks else np.array([], dtype=float)


def load_reference_data():
    bybit_path = os.path.join(REFERENCE_DIR, "bybit_orderbook_BTCUSDT_1min.csv")
    binance_path = os.path.join(REFERENCE_DIR, "binance_aggTrades_BTCUSDT_1min.csv")
    if not os.path.exists(bybit_path) or not os.path.exists(binance_path):
        raise FileNotFoundError(f"Required reference data missing: {bybit_path} / {binance_path}")

    print("Loading teammate reference data...")
    bybit = pd.read_csv(bybit_path, parse_dates=["ts_utc"])
    binance = pd.read_csv(binance_path, parse_dates=["ts_utc"])
    print(f"  Bybit: {len(bybit)} rows, {bybit.ts_utc.dt.date.nunique()} days")
    print(f"  Binance: {len(binance)} rows, {binance.ts_utc.dt.date.nunique()} days")

    merged = pd.merge_asof(
        bybit.sort_values("ts_utc"),
        binance[["ts_utc", "close", "vol_usd", "signed_usd", "n_trades"]].sort_values("ts_utc"),
        on="ts_utc", direction="nearest", tolerance=pd.Timedelta("1min"),
    ).dropna(subset=["close"])
    merged["date"] = merged.ts_utc.dt.strftime("%Y-%m-%d")
    print(f"  Merged: {len(merged)} rows, {merged.ts_utc.dt.date.nunique()} days")
    print(f"  Date range: {merged.ts_utc.min()} to {merged.ts_utc.max()}")
    return merged


def _daily_drawdown(frame):
    drawdowns = []
    for _, day in frame.groupby("date", sort=True):
        prices = day.close.to_numpy(dtype=float)
        peaks = np.maximum.accumulate(prices)
        drawdowns.extend((prices - peaks) / peaks)
    return float(np.min(drawdowns)) if drawdowns else 0.0


def _regime_stats(frame):
    returns = within_day_log_returns(frame)
    hourly_vols = []
    for _, day in frame.groupby("date", sort=True):
        daily_returns = within_day_log_returns(day)
        for start in range(0, len(daily_returns), 60):
            chunk = daily_returns[start:start + 60]
            if len(chunk) >= 30:
                hourly_vols.append(np.std(chunk) * math.sqrt(60))

    sd_returns = np.std(returns) if len(returns) else 0.0
    jumps = returns[np.abs(returns) > 3 * sd_returns] if sd_returns else np.array([])
    negative_jumps = jumps[jumps < 0]
    spread_bps = frame.spread_bps_mean.to_numpy(dtype=float)
    depth_btc = (frame.near_depth_usd.to_numpy(dtype=float) /
                 frame.mid_bybit.to_numpy(dtype=float))
    n_zero = int(np.sum(returns == 0))
    hours = len(returns) / 60.0

    return {
        "sigma_hourly": safe_median(hourly_vols),
        "sigma_p90": safe_percentile(hourly_vols, 90),
        "jump_rate_per_hour": len(jumps) / hours if hours else 0.0,
        "jump_mean": safe_mean(negative_jumps),
        "jump_std": safe_std(negative_jumps),
        "shock_return_1min": float(np.min(returns)) if len(returns) else 0.0,
        "shock_return_1min_pct": float(np.min(returns) * 100) if len(returns) else 0.0,
        "max_drawdown_daily": _daily_drawdown(frame),
        "max_drawdown_daily_pct": _daily_drawdown(frame) * 100,
        "spread_bps_median": safe_median(spread_bps),
        "spread_rel": safe_median(spread_bps) / 10000,
        "spread_rel_p5": safe_percentile(spread_bps, 5) / 10000,
        "spread_rel_p95": safe_percentile(spread_bps, 95) / 10000,
        "depth_btc": safe_median(depth_btc),
        "depth_p10": safe_percentile(depth_btc, 10),
        "depth_p90": safe_percentile(depth_btc, 90),
        "depth_band_fraction": 0.001,
        "depth_band_label": "within ±0.10% of mid (per Bybit DATA_README)",
        "kurtosis_with_zeros": excess_kurtosis(returns),
        "kurtosis_without_zeros": excess_kurtosis(returns[returns != 0]),
        "vol_clustering_acf1_with_zeros": autocorrelation(returns ** 2),
        "vol_clustering_acf1_without_zeros": autocorrelation(returns[returns != 0] ** 2),
        "order_flow_acf1": autocorrelation(frame.signed_usd.to_numpy(dtype=float) /
                                           (frame.vol_usd.to_numpy(dtype=float) + 1e-12)),
        "zero_return_pct": n_zero / len(returns) * 100 if len(returns) else 0.0,
        "n_zero_returns": n_zero,
        "n_total_returns": len(returns),
        "max_pos_1min_return": float(np.max(returns)) if len(returns) else 0.0,
        "trades_per_min_median": safe_median(frame.n_trades.to_numpy(dtype=float)),
        "btc_per_min_median": safe_median(frame.vol_usd.to_numpy(dtype=float) /
                                          frame.mid_bybit.to_numpy(dtype=float)),
    }


def load_funding():
    path = os.path.join(RAW_DIR, "hyperliquid", "funding_history.json")
    if not os.path.exists(path):
        print(f"  [info] no funding data at {path}, using defaults")
        return {"funding_mean": 0.0000125, "funding_std": 0.0001,
                "funding_cap": 0.0000125, "n_records": 0, "source": "default"}
    with open(path) as handle:
        payload = json.load(handle)
    rates = []
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                rate = item.get("fundingRate") or item.get("funding_rate") or item.get("rate")
                if rate is not None:
                    rates.append(float(rate))
    if not rates:
        return {"funding_mean": 0.0000125, "funding_std": 0.0001,
                "funding_cap": 0.0000125, "n_records": 0, "source": "default"}
    rates = np.asarray(rates)
    return {"funding_mean": float(np.mean(rates)), "funding_std": float(np.std(rates)),
            "funding_cap": float(np.max(np.abs(rates))),
            "funding_p95": float(np.percentile(np.abs(rates), 95)),
            "n_records": len(rates), "source": "hyperliquid_api"}


def extract_replay_paths(frame):
    paths = {}
    for key, day_label, start_hour in (
        ("normal", "2025-09-24", 0), ("stress", "2025-10-09", 0),
        ("extreme", "2025-10-10", 12),
    ):
        day = frame[frame.date == day_label].sort_values("ts_utc")
        if start_hour:
            day = day[day.ts_utc.dt.hour >= start_hour]
        prices = day.close.to_numpy(dtype=float)[:600]
        if len(prices) < 60:
            print(f"  [warn] replay {key}: only {len(prices)} prices")
            continue
        paths[key] = {"date": day_label, "n_steps": len(prices),
                      "start_price": float(prices[0]), "end_price": float(prices[-1]),
                      "prices": [float(value) for value in prices]}
    return paths


def compute_targets(frame):
    frame = frame.sort_values("ts_utc").reset_index(drop=True).copy()
    frame["date"] = frame.ts_utc.dt.strftime("%Y-%m-%d")
    present = set(frame.date.unique())
    required = set(NORMAL_TRAIN_DATES + STRESS_TRAIN_DATES + HELD_OUT_DATES + [CRASH_REPLAY_DATE])
    missing = sorted(required - present)
    if missing:
        raise ValueError(f"Reference dataset missing explicit split dates: {missing}")

    normal = frame[frame.date.isin(NORMAL_TRAIN_DATES)]
    stress = frame[frame.date.isin(STRESS_TRAIN_DATES)]
    held = frame[frame.date.isin(HELD_OUT_DATES)]
    print(f"Training days: {len(NORMAL_TRAIN_DATES) + len(STRESS_TRAIN_DATES)}")
    print(f"  Normal training: {NORMAL_TRAIN_DATES}")
    print(f"  Stress training: {STRESS_TRAIN_DATES}")
    print(f"  Post-crash held-out: {HELD_OUT_DATES}")
    print(f"  Oct 10 reserved for known replay; excluded from fitting and held-out score")
    excluded = sorted(present - set(NORMAL_TRAIN_DATES + STRESS_TRAIN_DATES + HELD_OUT_DATES + [CRASH_REPLAY_DATE]))

    normal_stats = _regime_stats(normal)
    normal_stats["training_days"] = NORMAL_TRAIN_DATES
    stress_stats = _regime_stats(stress)
    stress_stats["training_days"] = STRESS_TRAIN_DATES

    held_returns = within_day_log_returns(held)
    held_depth = held.near_depth_usd.to_numpy(dtype=float) / held.mid_bybit.to_numpy(dtype=float)
    held_spread = held.spread_bps_mean.to_numpy(dtype=float)
    hourly = []
    for _, day in held.groupby("date", sort=True):
        daily_returns = within_day_log_returns(day)
        for start in range(0, len(daily_returns), 60):
            chunk = daily_returns[start:start + 60]
            if len(chunk) >= 30:
                hourly.append(np.std(chunk) * math.sqrt(60))
    held_stats = {
        "days": HELD_OUT_DATES,
        "n_days": len(HELD_OUT_DATES),
        "n_minutes": len(held),
        "sigma_hourly": safe_median(hourly),
        "kurtosis_with_zeros": excess_kurtosis(held_returns),
        "kurtosis_without_zeros": excess_kurtosis(held_returns[held_returns != 0]),
        "vol_clustering_acf1_with_zeros": autocorrelation(held_returns ** 2),
        "vol_clustering_acf1_without_zeros": autocorrelation(held_returns[held_returns != 0] ** 2),
        "zero_return_pct": float(np.mean(held_returns == 0) * 100) if len(held_returns) else 0.0,
        "spread_bps_median": safe_median(held_spread),
        "depth_btc_median": safe_median(held_depth),
        "depth_band_fraction": 0.001,
        "depth_band_label": "within ±0.10% of mid (per Bybit DATA_README)",
        "max_neg_1min": float(np.min(held_returns)) if len(held_returns) else 0.0,
        "max_pos_1min": float(np.max(held_returns)) if len(held_returns) else 0.0,
        "max_drawdown": _daily_drawdown(held),
    }

    all_returns = within_day_log_returns(frame)
    all_hourly = []
    for _, day in frame.groupby("date", sort=True):
        daily_returns = within_day_log_returns(day)
        for start in range(0, len(daily_returns), 60):
            chunk = daily_returns[start:start + 60]
            if len(chunk) >= 30:
                all_hourly.append(np.std(chunk) * math.sqrt(60))
    full = {"n_days": frame.date.nunique(), "n_minutes": len(frame),
            "n_returns": len(all_returns), "sigma_hourly": safe_median(all_hourly),
            "kurtosis_with_zeros": excess_kurtosis(all_returns),
            "kurtosis_without_zeros": excess_kurtosis(all_returns[all_returns != 0]),
            "vol_clustering_acf1_with_zeros": autocorrelation(all_returns ** 2),
            "vol_clustering_acf1_without_zeros": autocorrelation(all_returns[all_returns != 0] ** 2),
            "zero_return_pct": float(np.mean(all_returns == 0) * 100),
            "max_neg_1min": float(np.min(all_returns)),
            "max_pos_1min": float(np.max(all_returns)),
            "max_worst_1min_by_day": min(
                float(np.min(within_day_log_returns(day)))
                for _, day in frame.groupby("date", sort=True)
                if len(day) > 1
            ),
            "max_drawdown": _daily_drawdown(frame)}

    paths = extract_replay_paths(frame)
    funding = load_funding()
    return {
        "normal": normal_stats,
        "stress": stress_stats,
        "held_out": held_stats,
        "full_dataset": full,
        "funding": funding,
        "_meta": {
            "data_source": "teammate_bybit_orderbook + teammate_binance_aggTrades_1min",
            "data_period": f"{frame.ts_utc.min()} to {frame.ts_utc.max()}",
            "n_days": int(frame.date.nunique()),
            "train_split": "explicit dates: 2025-09-24..2025-10-07 normal; 2025-10-08..2025-10-09 stress",
            "held_out_split": "post-crash diagnostics: 2025-10-11, 2025-10-12, 2025-10-13, 2025-10-20; not a fresh untouched validation set",
            "excluded_dates": excluded,
            "known_crash_replay_date": CRASH_REPLAY_DATE,
            "split_dates": {
                "normal_train": NORMAL_TRAIN_DATES,
                "stress_train": STRESS_TRAIN_DATES,
                "held_out_diagnostic": HELD_OUT_DATES,
                "known_crash_replay_only": CRASH_REPLAY_DATE,
            },
            "return_sampling": "within-day only; no return crosses an overnight or missing-data gap",
            "zero_returns": "retained in all statistics",
            "shock_definition": "maximum one-minute log return; not a daily drawdown",
            "spread_source": "Bybit spread_bps_mean",
            "depth_source": "Bybit near_depth_usd / mid, documented ±0.10% band",
            "generated_at": datetime.utcnow().isoformat(),
        },
        "replay_paths": paths,
    }


def main():
    frame = load_reference_data()
    targets = compute_targets(frame)
    out_path = os.path.join(PROCESSED_DIR, "calibration_targets.json")
    replay_path = os.path.join(PROCESSED_DIR, "replay_prices.json")
    with open(out_path, "w") as handle:
        json.dump(targets, handle, indent=2, default=str)
    with open(replay_path, "w") as handle:
        json.dump(targets["replay_paths"], handle, indent=2)
    print(f"Saved targets to {out_path}")
    print(f"Saved replay paths to {replay_path}")


if __name__ == "__main__":
    main()
