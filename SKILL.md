---
name: today-gain
description: Extract Taiwan brokerage holdings from screenshots and calculate stock gains using either a Google Sheet or a persistent SQLite price database. Use when the user says "today.gain", "today-gain", "跑 today.gain", requests a holdings profit/loss sheet, asks for fast mode or SQLite mode, or wants scheduled daily-close and intraday-price collection for the screenshot’s stock universe.
---

# Today Gain

## Choose the mode

- Use **Google Sheet mode** for the normal screenshot-to-report request.
- Use **SQLite fast mode** when the user mentions fast mode, SQLite, a price database, scheduled collection, daily closes, or intraday snapshots.
- Preserve an existing database and update it in place. Never recreate it merely because a new screenshot arrives.

## Extract holdings

Read every visible holding exactly once. Capture:

`股票,代號,GoogleFinance代號,股數,截圖市價,成本價,成本,目前市值,庫存損益,庫存報酬率`

For Taiwan stocks, keep the broker ticker in `代號`. Use `<ticker>.tw` for listed shares and `<ticker>.two` for OTC shares in `GoogleFinance代號`. Visually verify ambiguous names and tickers before fetching prices.

Store the extracted rows in UTF-8 CSV when using SQLite mode. New screenshots are authoritative for the active stock universe: add newly seen stocks, update existing stocks, and mark absent stocks inactive without deleting their historical closes.

## SQLite fast mode

Use [`scripts/today_gain_prices.py`](scripts/today_gain_prices.py). Default the database to `outputs/today-gain/today_gain.sqlite3` inside the active project unless the user supplies another path.

Initialize or migrate the database and backfill new stocks:

```bash
python3 <skill-dir>/scripts/today_gain_prices.py \
  --db <db-path> init --holdings <holdings.csv>
```

For later screenshots:

```bash
python3 <skill-dir>/scripts/today_gain_prices.py \
  --db <db-path> sync-holdings --holdings <holdings.csv>
```

### Storage contract

- `stock_universe`: ticker, name, market symbol, exchange, first/last seen dates, and active status.
- `prices`: one closing price per ticker and trading day. Retain these indefinitely after initialization.
- `intraday_prices`: at most five snapshots per active ticker for the current trading day.
- `collector_runs`: collector audit records.
- `latest_stock_prices`: convenient view joining latest close and latest intraday price.
- On first setup, retain the latest 20 trading-day closes for every active stock.
- When a new stock appears, add it and backfill its latest 20 trading-day closes.
- At the start of a later calendar day, delete older intraday rows only. Never purge daily closes.
- Do not store a weekend/holiday stale quote as a current-day snapshot.

### Price sources and schedule

- Use FinMind `TaiwanStockPrice` for historical and final daily closes.
- Use TWSE MIS for real-time listed and OTC quotes.
- Capture intraday prices at `09:06`, `10:06`, `11:06`, `12:06`, and `13:06` Asia/Taipei.
- Refresh prior closes at `08:55` and the current final close at `14:10`.
- On macOS, use a `launchd` LaunchAgent with those seven calendar intervals. Generate paths from the actual project and Python locations; do not copy hardcoded paths from another project.
- If the Mac is asleep or the market endpoint returns another trading date, leave that sample missing rather than fabricating an on-time observation.

Manual operations:

```bash
# Scheduled intraday slot
python3 <skill-dir>/scripts/today_gain_prices.py --db <db-path> sample --time 09:06

# Refresh final daily closes and purge older intraday rows
python3 <skill-dir>/scripts/today_gain_prices.py --db <db-path> close
```

### SQLite queries

```sql
SELECT * FROM latest_stock_prices ORDER BY ticker;

SELECT p.ticker, u.stock_name, p.trade_date, p.close
FROM prices p
JOIN stock_universe u USING (ticker)
ORDER BY p.ticker, p.trade_date;

SELECT ticker, trade_date, sample_time, price, quote_time
FROM intraday_prices
ORDER BY trade_date, sample_time, ticker;
```

### Fast-mode verification

Perform one final values check only. Verify:

- `PRAGMA integrity_check` returns `ok`.
- Every active stock has at least 20 closes after initialization/backfill.
- No duplicate `(ticker, trade_date)` or `(ticker, trade_date, sample_time)` keys exist.
- No older intraday rows remain after the date rolls over.
- The active stock count matches the extracted screenshot.

Report the database path, active-stock count, daily-close count, intraday-row count, and integrity result.

## Gain calculations

Use:

- `本日盈虧 = (截圖市價 - 昨日收盤價) * 股數`
- `本日報酬率 = (截圖市價 - 昨日收盤價) / 昨日收盤價`
- `20日盈虧 = (截圖市價 - 20D基準價) * 股數`
- `20日報酬率 = (截圖市價 - 20D基準價) / 20D基準價`

Add totals for the full `本日盈虧` and `20日盈虧` populations. Keep losses negative and show gain amounts as signed whole numbers and calculated percentages with one decimal place.

## Google Sheet mode

Create or update a Google Sheet named `台股YYMMDD`, using the Asia/Taipei date from the run date. For example, August 17, 2026 is `台股260817`. Use tab `本日個股盈虧`, unless the user supplies another name. Use these columns in order:

`股票,代號,GoogleFinance代號,股數,截圖市價,成本價,成本,目前市值,庫存損益,庫存報酬率,昨日收盤價,本日盈虧,本日報酬率,20日盈虧,20日報酬率`

Create and author the spreadsheet directly with connected Google Drive/Sheets tools:

1. Search Drive for an existing Google Sheet with the exact same `台股YYMMDD` title.
2. If a same-day file exists, update that spreadsheet in place: clear or overwrite the existing `本日個股盈虧` tab content and formatting for the populated report area, then write the new report. Do not create a duplicate same-day file.
3. If no same-day file exists, call the native Drive file-creation action with MIME type `application/vnd.google-apps.spreadsheet`.
4. Read the target spreadsheet metadata and use the returned spreadsheet ID, exact tab title, and `sheetId`.
5. Rename the tab to `本日個股盈虧` when needed.
6. Write the complete bounded values/formulas block with Sheets `batchUpdate` requests.
7. Apply number formats, header style, frozen row/columns, column widths, alternating-row formatting, and a basic filter over holding rows only.
8. Read the populated range and metadata back for verification.

Highlight the five largest numeric holding cells separately in each of these columns with an orange background:

`目前市值,庫存損益,庫存報酬率,本日盈虧,本日報酬率,20日盈虧,20日報酬率`

Exclude the header and `合計` row. Add one conditional-format rule per column, with the orange rule ahead of the alternating-row rule so the highlight remains visible. To select exactly five cells even when values tie, rank descending and break ties by row order; for example, for `H2:H21`, use `=RANK(H2,$H$2:$H$21)+COUNTIF($H$2:H2,H2)-1<=5`. Adjust the ending row to the actual last holding row and apply the equivalent formula to each target column. Use a light orange fill such as RGB `(1.0, 0.85, 0.65)`.

Never create, transform, upload, import, export, or verify an Excel/XLSX file for Google Sheet mode. If direct native Google Sheet creation or writing is unavailable, stop and report the connector limitation instead of falling back to Excel.

Keep the market symbol visible. Use formulas when the user may edit prices; otherwise sourced static values are acceptable. Freeze row 1 and columns A:B, make headers bold, filter all holding rows, apply subtle alternating rows, and leave a visually distinct `合計` row outside the filter.

Before delivery, confirm every screenshot holding appears once, recalculate two rows, verify totals, check for formula errors, confirm exactly five orange holding cells in each target column, and state the market-data date.
