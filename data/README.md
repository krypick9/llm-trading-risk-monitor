# Data

## Directory Structure

```
data/
├── raw/           # Downloaded raw data (gitignored — store in shared Google Drive)
├── processed/     # Calibration results (small JSON files, tracked in git)
├── reference/     # Teammate's processed 1-min reference data (tracked in git)
├── scripts/       # Data acquisition & calibration scripts (tracked in git)
├── CALIBRATION.md # Calibration data sources and pipeline documentation
└── README.md      # This file
```

## Data Dictionary

### Block A: Core (standard trade and quote data)
| Feature | Data needed | Source | Status |
|---------|------------|--------|--------|
| Returns, rolling volatility σ | Trades (price, size, time) | CryptoHFTData; venue WS | Available |
| Relative spread ℓ = spread/mid | Best bid and ask quotes | CryptoHFTData; venue WS | Available |
| Top-of-book depth; flow imbalance | Quoted sizes; signed trades | CryptoHFTData; venue WS | Available |

### Block B: Extended (Level-2 order-book data)
| Feature | Data needed | Source | Status |
|---------|------------|--------|--------|
| Multi-level depth; decline speed | Book snapshots by price level | CryptoHFTData; Tardis (1st/mo) | Available |
| Cancel-to-trade ratio; arrival rate | Order messages (add/cancel/exec) | 0xArchive (paid, events only) | Buy when needed |

### Block C: Newly observable in perpetuals
| Feature | Data needed | Source | Status |
|---------|------------|--------|--------|
| Funding rate + rate of change | Funding settlement stream | Hyperliquid info API; venue WS | Available |
| Liquidation intensity + cascade depth | Liquidation event stream | Venue WS; 0xArchive | Available |
| Open interest + mark-price basis | Venue state snapshots | Venue REST API | Available |

## Rule

> A feature enters the detector only if the same field exists in both the simulator and the chosen dataset.

## Shared Storage

Large raw data files (~706 MB) are stored locally in `data/raw/` (gitignored). To share with the team:

1. Upload `data/raw/` to the team's shared Google Drive or other cloud storage
2. Update the link here:
   - **Drive link:** _[add link when available]_
   - **Folder structure mirrors `data/raw/`**

### Raw data contents

| Path | Source | Days | Size | Used for |
|------|--------|------|------|----------|
| `data/raw/binance/aggTrades/` | Binance Vision | Sep 15-17, Oct 9-10, Aug 25 (6 days) | ~697 MB | sigma, jumps, kurtosis |
| `data/raw/binance/bookDepth/` | Binance Vision | Same 6 days | ~9 MB | spread, depth |
| `data/raw/hyperliquid/` | Hyperliquid API | Sep-Nov 2025 | ~64 KB | funding rate, live orderbook |

### Re-downloading raw data

```bash
python data/scripts/download_data.py
```

This re-downloads all raw data from Binance Vision (free, no API key) and Hyperliquid API (free).

## Reference data (teammate)

`data/reference/` contains processed 1-minute data from a teammate's Phase 3 reference tool:

| File | Source | Days | Description |
|------|--------|------|-------------|
| `binance_aggTrades_BTCUSDT_1min.csv` | Binance aggTrades | Sep 24 - Oct 20 (22 days) | 1-min OHLC + volume + signed flow |
| `bybit_orderbook_BTCUSDT_1min.csv` | Bybit orderbook | Same 22 days | 1-min mid, spread, depth, imbalance |
| `crash_window.png` | - | - | Visualization of crash window |
| `DATA_README.md` | - | - | Original data documentation |

These are **complementary** to our calibration data: different dates, different exchange (Bybit), coarser granularity.

## Sources

| Source | What | Access | Cost |
|--------|------|--------|------|
| CryptoHFTData | L2 book Parquet: Hyperliquid, Binance, OKX, Bybit | Free, API key | $0 |
| Hyperliquid WS | Live L2 book, trades, funding, liquidations | Free | $0 |
| Binance bookDepth | Depth by ±% band, 30s, from Jan 2023 | Free | $0 |
| Tardis.dev | Incremental L2, 69 venues, from 2019 | Free on 1st of month | $0 |
| 0xArchive | L2/L4 order data for Hyperliquid | Paid | ~$3/GB |
| SEC Market Structure Data | Daily cancels, trades, quote life | Free | $0 |
