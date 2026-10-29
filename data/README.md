# Data

## Directory Structure

```
data/
├── raw/           # Downloaded data (gitignored — store in shared Google Drive)
├── processed/     # Cleaned, feature-ready data (gitignored for large files)
├── scripts/       # Data acquisition scripts (in git)
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

Large data files are stored in the team's shared Google Drive (not in git).
- **Drive link:** _[add link here]_
- **Folder structure mirrors `data/raw/`**

## Sources

| Source | What | Access | Cost |
|--------|------|--------|------|
| CryptoHFTData | L2 book Parquet: Hyperliquid, Binance, OKX, Bybit | Free, API key | $0 |
| Hyperliquid WS | Live L2 book, trades, funding, liquidations | Free | $0 |
| Binance bookDepth | Depth by ±% band, 30s, from Jan 2023 | Free | $0 |
| Tardis.dev | Incremental L2, 69 venues, from 2019 | Free on 1st of month | $0 |
| 0xArchive | L2/L4 order data for Hyperliquid | Paid | ~$3/GB |
| SEC Market Structure Data | Daily cancels, trades, quote life | Free | $0 |
