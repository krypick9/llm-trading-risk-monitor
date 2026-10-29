# Data Acquisition Scripts

Scripts for downloading and recording market data.

## Scripts (to be created)

| Script | Source | What it does | Owner |
|--------|--------|-------------|-------|
| `download_cryptohftdata.py` | CryptoHFTData | Download L2 book Parquet files | Member C |
| `record_hyperliquid_ws.py` | Hyperliquid WebSocket | Record live L2 book, trades, funding, liquidations | Member C |
| `download_tardis_monthly.py` | Tardis.dev | Download monthly free data (1st of each month) | Member C |
| `download_binance_depth.py` | Binance Vision | Download historical depth data | Member C |
| `process_l2_to_calibration.py` | Local raw data | Process raw L2 into calibration targets | Member C |
| `buy_0xarchive_events.py` | 0xArchive | Download specific crisis windows (paid) | Member C |

## Usage

```bash
# Set up API keys first
cp .env.example .env
# Edit .env with your API keys

# Download CryptoHFTData sample
python data/scripts/download_cryptohftdata.py --symbol BTCUSDT --date 2025-10-10

# Start recording Hyperliquid live data
python data/scripts/record_hyperliquid_ws.py --output data/raw/hyperliquid_live/
```
