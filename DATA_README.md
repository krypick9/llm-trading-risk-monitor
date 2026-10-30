# Source data used by the Crypto Risk Monitor

Symbol: BTCUSDT perpetual. Days: 2025-09-24 to 2025-10-13 (train + test) plus 2025-10-19 and 2025-10-20 (calm-day check). 22 days, 1-minute rows, timestamps in UTC.

These are the processed minute bars the tool actually uses. The raw files were downloaded, aggregated to 1 minute and then deleted, because one raw Bybit order-book day is about 300 MB.

## 1. binance_aggTrades_BTCUSDT_1min.csv  (from Binance aggregated trades)
Raw source, one zip per day:
https://data.binance.vision/data/futures/um/daily/aggTrades/BTCUSDT/BTCUSDT-aggTrades-2025-10-10.zip

| Column | Meaning |
|---|---|
| open, high, low, close | Trade prices in that minute |
| vol_usd | Traded notional (price x quantity), USD |
| signed_usd | Buyer-initiated minus seller-initiated notional (is_buyer_maker = true counts as a sell) |
| n_trades | Number of aggregated trades |

## 2. bybit_orderbook_BTCUSDT_1min.csv  (from Bybit order-book history)
Raw source, one zip per day (snapshot + delta messages; 200 levels, older dates 500):
https://quote-saver.bycsi.com/orderbook/linear/BTCUSDT/2025-10-10_BTCUSDT_ob200.data.zip
The book is rebuilt from the messages, sampled once per second, then summarised per minute.

| Column | Meaning |
|---|---|
| mid_bybit | Mid price at the end of the minute |
| spread_bps_mean / spread_bps_max | Bid-ask spread in basis points, mean and max over the minute's seconds. Usually one tick ($0.10), so about 0.009 bps; it widens in stress |
| tob_depth_usd | Best bid size + best ask size, USD |
| near_depth_usd / near_depth_min_usd | Depth within 0.10% of mid, mean and minimum over the minute, USD |
| tob_imb | Top-of-book imbalance (bid - ask) / (bid + ask), from -1 to 1 |
| book_secs | Seconds in the minute with a valid book (60 = full coverage) |

## Re-downloading the raw data
`python -m crm` re-downloads any day missing from `cache/`. Delete a cache file to force a fresh download. The download code is in `crm/data.py` and `crm/bybit_book.py`.
