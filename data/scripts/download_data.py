"""
Download free BTC perpetual futures data for calibration.

Sources (all free, no API key needed):
  1. Binance Vision — historical agg trades for BTCUSDT (spot, as proxy for price)
     URL pattern: https://data.binance.vision/data/futures/um/daily/aggTrades/BTCUSDT/BTCUSDT-aggTrades-YYYY-MM-DD.zip
  2. Binance Vision — historical book depth snapshots
     URL pattern: https://data.binance.vision/data/futures/um/daily/bookDepth/BTCUSDT/BTCUSDT-bookDepth-YYYY-MM-DD.zip
  3. Hyperliquid API — funding rate history
     URL: https://api.hyperliquid.xyz/info (POST)

This script downloads a small set of dates (enough for calibration):
  - A few "normal" trading days (low volatility)
  - A few "stress" trading days (known crash events)

Usage:
  python download_data.py                    # download default date set
  python download_data.py --dates 2025-10-09 2025-10-10  # specific dates
"""

import os
import sys
import json
import time
import zipfile
import argparse
import requests
from datetime import date, timedelta
from io import BytesIO

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

RAW_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "raw")
RAW_DIR = os.path.normpath(RAW_DIR)

# Default dates to download:
#   - Normal days: recent low-volatility periods
#   - Stress days: known BTC crash events in 2025
DEFAULT_DATES = {
    # Normal regime: 3 recent quiet days
    "normal": ["2025-09-15", "2025-09-16", "2025-09-17"],
    # Stress regime: Oct 2025 BTC drop (the one your teammate already looked at)
    # plus a couple other known volatile days
    "stress": ["2025-10-09", "2025-10-10", "2025-08-25"],
}

BINANCE_VISION = "https://data.binance.vision/data/futures/um/daily"
SYMBOL = "BTCUSDT"

HYPERLIQUID_API = "https://api.hyperliquid.xyz/info"

# ---------------------------------------------------------------------------
# Binance Vision: agg trades (price + volume per trade)
# ---------------------------------------------------------------------------

def download_binance_file(data_type: str, day: str) -> str:
    """Download a single daily file from Binance Vision.
    
    data_type: "aggTrades" or "bookDepth"
    day: "YYYY-MM-DD"
    
    Returns path to the extracted CSV, or None if download failed.
    """
    url = f"{BINANCE_VISION}/{data_type}/{SYMBOL}/{SYMBOL}-{data_type}-{day}.zip"
    dest_dir = os.path.join(RAW_DIR, "binance", data_type)
    os.makedirs(dest_dir, exist_ok=True)
    csv_path = os.path.join(dest_dir, f"{SYMBOL}-{data_type}-{day}.csv")
    
    if os.path.exists(csv_path):
        size_mb = os.path.getsize(csv_path) / 1e6
        print(f"  [skip] {data_type}/{day} already exists ({size_mb:.1f} MB)")
        return csv_path
    
    print(f"  [download] {data_type}/{day} ...")
    try:
        resp = requests.get(url, timeout=60)
        if resp.status_code == 404:
            print(f"  [warn] {data_type}/{day} not found (404) — may be too recent or wrong symbol")
            return None
        resp.raise_for_status()
        
        zf = zipfile.ZipFile(BytesIO(resp.content))
        extracted = zf.namelist()
        zf.extractall(dest_dir)
        
        # The extracted file might have a different name; find it
        if extracted:
            extracted_path = os.path.join(dest_dir, extracted[0])
            if extracted_path != csv_path:
                os.rename(extracted_path, csv_path)
        
        size_mb = os.path.getsize(csv_path) / 1e6
        print(f"  [ok] {data_type}/{day} ({size_mb:.1f} MB)")
        return csv_path
    
    except Exception as e:
        print(f"  [error] {data_type}/{day}: {e}")
        return None


def download_agg_trades(day: str) -> str:
    """Download aggregated trades for a day. Columns: agg_id, price, qty, timestamp, ..."""
    return download_binance_file("aggTrades", day)


def download_book_depth(day: str) -> str:
    """Download order book depth snapshots for a day."""
    return download_binance_file("bookDepth", day)


# ---------------------------------------------------------------------------
# Hyperliquid: funding rate history
# ---------------------------------------------------------------------------

def download_funding_history(coin: str = "BTC", start_date: str = "2025-09-01",
                             end_date: str = "2025-11-01") -> str:
    """Download funding rate history from Hyperliquid API.
    
    Saves to raw/hyperliquid/funding_BTC_YYYYMMDD_YYYYMMDD.json
    """
    dest_dir = os.path.join(RAW_DIR, "hyperliquid")
    os.makedirs(dest_dir, exist_ok=True)
    fname = f"funding_{coin}_{start_date.replace('-','')}_{end_date.replace('-','')}.json"
    fpath = os.path.join(dest_dir, fname)
    
    if os.path.exists(fpath):
        print(f"  [skip] funding history already exists")
        return fpath
    
    print(f"  [download] Hyperliquid funding history for {coin}...")
    
    # Hyperliquid uses a POST API. The fundingHistory endpoint returns
    # funding rates between two timestamps.
    # Convert dates to millisecond timestamps
    start_ms = int(time.mktime(time.strptime(start_date, "%Y-%m-%d")) * 1000)
    end_ms = int(time.mktime(time.strptime(end_date, "%Y-%m-%d")) * 1000)
    
    payload = {
        "type": "fundingHistory",
        "coin": coin,
        "startTime": start_ms,
        "endTime": end_ms,
    }
    
    try:
        resp = requests.post(HYPERLIQUID_API, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        
        with open(fpath, "w") as f:
            json.dump(data, f, indent=2)
        
        print(f"  [ok] {len(data)} funding records saved to {fpath}")
        return fpath
    
    except Exception as e:
        print(f"  [error] funding history: {e}")
        # Fallback: Binance funding rate history (also free)
        print(f"  [fallback] trying Binance funding rate API...")
        return download_binance_funding(start_date, end_date)


def download_binance_funding(start_date: str, end_date: str) -> str:
    """Fallback: download funding rates from Binance public API."""
    dest_dir = os.path.join(RAW_DIR, "binance", "funding")
    os.makedirs(dest_dir, exist_ok=True)
    fname = f"funding_BTCUSDT_{start_date.replace('-','')}_{end_date.replace('-','')}.json"
    fpath = os.path.join(dest_dir, fname)
    
    url = "https://fapi.binance.com/fapi/v1/fundingRate"
    params = {
        "symbol": "BTCUSDT",
        "startTime": int(time.mktime(time.strptime(start_date, "%Y-%m-%d")) * 1000),
        "endTime": int(time.mktime(time.strptime(end_date, "%Y-%m-%d")) * 1000),
        "limit": 1000,
    }
    
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        
        with open(fpath, "w") as f:
            json.dump(data, f, indent=2)
        
        print(f"  [ok] {len(data)} Binance funding records saved to {fpath}")
        return fpath
    
    except Exception as e:
        print(f"  [error] Binance funding fallback: {e}")
        return None


# ---------------------------------------------------------------------------
# Hyperliquid: recent order book snapshot (for depth calibration)
# ---------------------------------------------------------------------------

def download_hyperliquid_orderbook() -> str:
    """Download a live order book snapshot from Hyperliquid for depth reference."""
    dest_dir = os.path.join(RAW_DIR, "hyperliquid")
    os.makedirs(dest_dir, exist_ok=True)
    fpath = os.path.join(dest_dir, "orderbook_snapshot.json")
    
    print(f"  [download] Hyperliquid live order book snapshot...")
    
    payload = {"type": "l2Book", "coin": "BTC"}
    
    try:
        resp = requests.post(HYPERLIQUID_API, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        
        with open(fpath, "w") as f:
            json.dump(data, f, indent=2)
        
        levels = len(data.get("levels", []))
        print(f"  [ok] {levels} price levels saved to {fpath}")
        return fpath
    
    except Exception as e:
        print(f"  [error] Hyperliquid orderbook: {e}")
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Download BTC data for calibration")
    parser.add_argument("--dates", nargs="*", help="Specific dates (YYYY-MM-DD)")
    args = parser.parse_args()
    
    os.makedirs(RAW_DIR, exist_ok=True)
    
    print("=" * 60)
    print("BTC Perpetual Futures Data Download (for Calibration)")
    print(f"Output directory: {RAW_DIR}")
    print("=" * 60)
    
    # Determine which dates to download
    if args.dates:
        dates = args.dates
        regime_map = {d: "custom" for d in dates}
    else:
        dates = DEFAULT_DATES["normal"] + DEFAULT_DATES["stress"]
        regime_map = {}
        for d in DEFAULT_DATES["normal"]:
            regime_map[d] = "normal"
        for d in DEFAULT_DATES["stress"]:
            regime_map[d] = "stress"
    
    print(f"\nDates to download ({len(dates)}): {dates}")
    
    # 1. Download agg trades (price/volume data)
    print(f"\n--- Aggregated trades (price + volume) ---")
    trade_files = {}
    for day in dates:
        path = download_agg_trades(day)
        if path:
            trade_files[day] = path
    
    # 2. Download book depth snapshots
    print(f"\n--- Order book depth snapshots ---")
    depth_files = {}
    for day in dates:
        path = download_book_depth(day)
        if path:
            depth_files[day] = path
    
    # 3. Download funding history
    print(f"\n--- Funding rate history ---")
    funding_path = download_funding_history(
        coin="BTC",
        start_date="2025-09-01",
        end_date="2025-11-01"
    )
    
    # 4. Download a live Hyperliquid order book snapshot
    print(f"\n--- Hyperliquid live order book (depth reference) ---")
    download_hyperliquid_orderbook()
    
    # Summary
    print(f"\n{'=' * 60}")
    print("Download summary:")
    print(f"  Agg trades:  {len(trade_files)}/{len(dates)} days")
    print(f"  Book depth:  {len(depth_files)}/{len(dates)} days")
    print(f"  Funding:     {'yes' if funding_path else 'failed'}")
    
    # Save a manifest
    manifest = {
        "dates": regime_map,
        "trade_files": trade_files,
        "depth_files": depth_files,
        "funding_file": funding_path,
    }
    manifest_path = os.path.join(RAW_DIR, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"\nManifest saved to {manifest_path}")
    print(f"\nNext step: run compute_calibration_targets.py")


if __name__ == "__main__":
    main()
