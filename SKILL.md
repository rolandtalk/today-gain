---
name: today-gain
description: Build or update a Google Sheet for stock profit/loss from a brokerage holdings or inventory screenshot. Use when the user says "today.gain", "today-gain", "跑 today.gain", or asks to identify stocks/share counts from a holdings screenshot, fetch current/previous/20D close prices, calculate sortable today's gain/loss and 20D gain/loss percentages, and deliver the result as a Google Sheet.
---

# Today Gain

## Workflow

Convert a holdings screenshot into a clean Google Sheet:

1. Read the screenshot or table and extract each holding's stock name, ticker/code, share count, current screenshot price, cost price, total cost, current market value, inventory gain/loss, and inventory return if visible.
2. Normalize tickers for their market. For Taiwan stocks, keep the broker code in `代號` and add a visible `GoogleFinance代號` column. Use `.tw` for listed shares and `.two` for OTC shares when showing the user-facing symbol. In formulas, convert `.tw` symbols to GoogleFinance's `TPE:` prefix when that is what resolves; try `TWO:` as an OTC fallback.
3. Fetch or verify market data for the trading date: previous close and, when requested by default, the 20-trading-day base close. Prefer live primary market data. If live data is unavailable, fill static sourced values, add a cell note when possible, and clearly mention the fallback in the final answer.
4. Calculate:
   - `本日盈虧 = (截圖市價 - 昨日收盤價) * 股數`
   - `本日報酬率 = (截圖市價 - 昨日收盤價) / 昨日收盤價`
   - `20日盈虧 = (截圖市價 - 20D基準價) * 股數`
   - `20日報酬率 = (截圖市價 - 20D基準價) / 20D基準價`
   - Add a bottom summary row labeled `合計` that sums the full `本日盈虧` column and the full `20日盈虧` column.
5. Create or update a Google Sheet named `本日個股盈虧計算` unless the user requests another name. If the user asks for a date-specific title such as `台股+today's date`, use the exact current date in `YYYY-MM-DD`.
6. Use these columns, in this order, and do not add a `資料來源` column:
   `股票`, `代號`, `GoogleFinance代號`, `股數`, `截圖市價`, `成本價`, `成本`, `目前市值`, `庫存損益`, `庫存報酬率`, `昨日收盤價`, `本日盈虧`, `本日報酬率`, `20日盈虧`, `20日報酬率`.

## Formatting Rules

- Show `本日盈虧` as a signed whole number with no decimals.
- Show `本日報酬率`, `20日盈虧`, and `20日報酬率` as signed values; percentages use 1 decimal place and gains use whole numbers.
- Show summary-row totals for `本日盈虧` and `20日盈虧` as signed whole numbers with no decimals.
- Keep losses as negative numbers.
- Use percent formatting for `庫存報酬率`, `本日報酬率`, and `20日報酬率`.
- Freeze the header row and columns A:B, make the header bold, add filters over the populated data range, and apply alternating row highlighting to the data rows for readability.
- Keep the `合計` row visually distinct and below the holdings table. If possible, keep filters on the holdings rows and leave the summary row outside the sortable/filterable data range.
- If the screenshot has multiple pages or sections, combine all holdings into one table before adding the summary row.

## Google Sheets Delivery

- Prefer the connected Google Sheets/Drive tools when available.
- Put the table on a sheet named `本日個股盈虧`.
- Use Google Sheets formulas for `昨日收盤價`, `本日盈虧`, `本日報酬率`, `20日盈虧`, and `20日報酬率` when the user may edit prices after delivery; otherwise static computed values are acceptable.
- Keep `GoogleFinance代號` visible so users can correct symbols such as `3131.two`, `3443.tw`, or `6290.two` without editing hidden formulas.
- When GoogleFinance does not resolve an OTC symbol, replace only the unresolved market-data cell or formula input with a static sourced value; keep downstream gain/% cells formula-driven.
- Apply number formats in Google Sheets:
  - `股數`, `成本`, `目前市值`, `庫存損益`, `本日盈虧`, `20日盈虧`: whole numbers.
  - `截圖市價`, `成本價`, `昨日收盤價`: 1-2 decimals as appropriate for the market.
  - `庫存報酬率`, `本日報酬率`, `20日報酬率`: percentages, with calculated returns at 1 decimal.
- Share or provide the Google Sheet link after verifying the data and formulas.

## XLSX Fallback Script

Use `scripts/build_today_gain_xlsx.py` only as a fallback or import helper when Google Sheets tools are unavailable. It has no third-party dependencies.

Expected CSV columns:

```text
股票,代號,GoogleFinance代號,股數,截圖市價,成本價,成本,目前市值,庫存損益,庫存報酬率,昨日收盤價,20日基準價
```

`GoogleFinance代號` and `20日基準價` are optional. If `GoogleFinance代號` is omitted, the script derives `<代號>.tw`. If `20日基準價` is omitted, the 20D columns are left blank.

Run:

```bash
python3 scripts/build_today_gain_xlsx.py holdings.csv 本日個股盈虧計算.xlsx
```

The script computes `本日盈虧`, `本日報酬率`, `20日盈虧`, and `20日報酬率`, formats an `.xlsx` workbook, and leaves missing daily/20D values blank when source prices are unavailable. If this fallback is used, import or upload the workbook into Google Sheets before delivery whenever possible.
It also appends a `合計` row that totals `本日盈虧` and `20日盈虧`.

## Quality Checks

- Confirm every visible holding from the screenshot appears exactly once.
- Recalculate at least two rows by hand before delivering.
- Verify filters cover every sortable column, including `庫存損益`, `%`, `本日盈虧`, `本日報酬率`, `20日盈虧`, and `20日報酬率`.
- Verify the `合計` row totals all visible holding rows for `本日盈虧` and `20日盈虧`.
- Check that no spreadsheet cells contain formula errors.
- Mention the market-data timestamp/date used, especially if the user asked for "today".
