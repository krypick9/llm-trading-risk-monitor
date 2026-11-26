# Reserved confirmatory dates

Purpose: keep at least one day of real data that no calibration, tuning or held-out check has used, so there is one
result that is not contaminated by earlier looks. The held-out days (11, 12, 13, 20 Oct 2025) were inspected before and
are diagnostics only.

## Reserved

| Date (UTC) | Rows | Used by calibration, held-out or crash replay? |
|---|---|---|
| 2025-10-19 | 1,440 one-minute rows in `binance_aggTrades_BTCUSDT_1min.csv` | No |

Checked against the day lists in `calibration_result.json` and the scripts: normal training 24 Sep to 7 Oct, stress
training 8 to 9 Oct, crash replay 10 Oct, held-out 11, 12, 13 and 20 Oct. The Bybit order-book file was not checked
for this day; confirm it has the same 1,440 rows before relying on it.

## Limits

- One day is about one regime and 1,440 minutes. It can reveal a failure but cannot support strong claims.
- Someone may already have looked at this day informally. Treat that as a risk and say so in the report.
- Add more days if possible (the files end on 20 Oct; 14 to 18 Oct are missing). Download them with
  `data/scripts/download_data.py` (check its date arguments), add them to this file, and do not open them until the
  freeze below.

## Rules

1. Freeze first. Record the git commit of the simulator, the dials and the flow count before opening the reserved day.
2. Run the frozen-dial check once and report whatever it gives, pass or fail.
3. Do not retune after looking. If the simulator is changed because of this day, the day becomes training data and a
   new reserved day is needed.
4. Log every use below.

## Use log

| Date opened | Simulator commit | Dials and flow count | Who | Result |
|---|---|---|---|---|
| not yet used | | | | |
