---
name: today-gain
description: Extract Taiwan brokerage holdings from screenshots, persist closing prices in SQLite, calculate daily and 20-trading-day gains, and publish Google Sheets reports. Use when the user says "today.gain", "today-gain", "跑 today.gain", requests a holdings profit/loss sheet, asks for fast mode or SQLite mode, or wants scheduled daily-close and intraday-price collection.
---

# Today Gain

## Choose the workflow

- For Taiwan holdings, always use the persistent SQLite database for yesterday and 20D closing prices, then publish the resulting values to Google Sheets. This is the default screenshot-to-report workflow; the user does not need to say “fast mode.”
- For US or other non-Taiwan holdings, use Google Sheet mode without SQLite and report that the Taiwan collector does not support those markets.
- Preserve an existing database and update it in place. Never recreate it merely because a new screenshot arrives.

## Extract holdings

Read every visible holding exactly once. Capture:

`股票,代號,GoogleFinance代號,股數,截圖市價,成本價,成本,目前市值,庫存損益,庫存報酬率`

For Taiwan stocks, keep the broker ticker in `代號`. Use `<ticker>.tw` for listed shares and `<ticker>.two` for OTC shares in `GoogleFinance代號`. Visually verify ambiguous names and tickers before fetching prices.

Store the extracted rows in UTF-8 CSV. New screenshots are authoritative for the active stock universe: add newly seen stocks, update existing stocks, and mark absent stocks inactive without deleting their historical closes.

Reuse a stored market symbol when a later CSV leaves `GoogleFinance代號` blank. When a valid `.tw`/`.two` value changes for an existing ticker, update `stock_universe` and record the old and new mappings in `ticker_transitions`. Visually verify the change before calculation; never guess an exchange transition.

## SQLite-backed calculation

Use [`scripts/today_gain_prices.py`](scripts/today_gain_prices.py). Its stable default database is `~/.codex/data/today-gain/today_gain.sqlite3`, shared by future Today Gain runs. Use another path only when the user explicitly requests it.

SQLite fast mode currently supports Taiwan-listed holdings only. Before writing the database, confirm every `GoogleFinance代號` ends with `.tw` or `.two`. If US, OTCMKTS, NYSE, NASDAQ, BATS, NYSEARCA, or another non-Taiwan symbol appears, do not run the SQLite collector; report that US SQLite support needs a separate price-source implementation instead of storing misleading TWSE/FinMind rows.

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

For every Taiwan screenshot report, run the combined calculation command instead of calling GoogleFinance formulas:

```bash
python3 <skill-dir>/scripts/today_gain_prices.py \
  calculate --holdings <holdings.csv> --as-of <screenshot-date> \
  --output <calculated.csv>
```

Omit `--db` to use the stable default. `calculate` synchronizes holdings, fills missing closes through the prior calendar day, ensures 21 closes per active holding, and writes Sheet-ready static values. Treat `昨日收盤日期` and `20D基準日期` in the output as verification fields; do not add them to the required Google Sheet columns unless requested.

### Storage contract

- `stock_universe`: ticker, name, market symbol, exchange, first/last seen dates, and active status.
- `prices`: one closing price per ticker and trading day. Retain these indefinitely after initialization.
- `intraday_prices`: at most five snapshots per active ticker for the current trading day.
- `collector_runs`: collector audit records.
- `ticker_transitions`: audited `.tw`/`.two` mapping changes for existing tickers.
- `latest_stock_prices`: convenient view joining latest close and latest intraday price.
- On first setup, retain at least 21 closes for every active stock: the prior close plus the close 20 trading sessions earlier.
- When a new stock appears, add it and backfill at least 21 closes.
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
- Every active stock has at least 21 closes after initialization/backfill.
- Every calculated holding has 21 closes through the day before the screenshot.
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

Create or update a Google Sheet named with the market prefix plus `YYMMDD`, using the Asia/Taipei screenshot date. Use `美股YYMMDD` when the target symbols are US-listed equities, including `NYSE:*`, `NASDAQ:*`, `BATS:*`, `NYSEARCA:*`, or `OTCMKTS:*`. Use `台股YYMMDD` for Taiwan-listed holdings. For example, August 17, 2026 becomes `美股260817` for US holdings and `台股260817` for Taiwan holdings. Use tab `本日個股盈虧`, unless the user supplies another name. Use these columns in order:

`股票,代號,GoogleFinance代號,股數,截圖市價,成本價,成本,目前市值,庫存損益,庫存報酬率,昨日收盤價,本日盈虧,本日報酬率,20日盈虧,20日報酬率`

Use the native Google Sheets template:

- Title: `Today Gain 台股報表範本`
- File ID: `1kq-r2Bpgaa9sxcyrDfxQ_Og7ni_eWDAQJVzflno1GSU`
- URL: `https://docs.google.com/spreadsheets/d/1kq-r2Bpgaa9sxcyrDfxQ_Og7ni_eWDAQJVzflno1GSU/edit`
- Tab: `本日個股盈虧`
- Header: row 1
- Preformatted holding slots: rows 2:51, hidden while unused
- Fixed total row: row 52
- Capacity: 50 holdings

Never write report data into the template itself. For a new report, copy the entire native template with the Drive file-copy action and set the copy title to the market-prefixed `YYMMDD` title. Verify that the destination spreadsheet ID differs from the template ID before writing.

Create and author the spreadsheet directly with connected Google Drive/Sheets tools:

1. Search Drive for an existing Google Sheet with the exact same market-prefixed `YYMMDD` title.
2. If a same-day file exists, update that spreadsheet in place. Do not create a duplicate same-day file.
3. If no same-day file exists, copy the native template and give the copy the exact report title. Do not create a blank spreadsheet.
4. Read the target spreadsheet metadata and use its returned spreadsheet ID, exact tab title, and `sheetId`.
5. For a template-derived report with at most 50 holdings, perform one coherent `batchUpdate`: clear values in `A2:O51`, hide rows 2:51, unhide exactly the populated holding rows, and write static report values into those rows. Keep row 52 and its total formulas intact.
6. Preserve the template’s number formats, header style, frozen row/columns, column widths, alternating-row rule, filter, and conditional-format rules. Do not recreate them on every run.
7. If the report has more than 50 holdings, insert sufficient rows before row 52, copy a complete holding-row format into them, move/update the total row, and extend the filter and conditional-format ranges before writing.
8. For an older same-day report that does not have the template’s row-52 total topology, update its existing bounded report area safely instead of forcing a template conversion.
9. Read the populated range and metadata back for verification.

Highlight the five largest numeric holding cells separately in each of these columns with an orange background:

`目前市值,庫存損益,庫存報酬率,本日盈虧,本日報酬率,20日盈虧,20日報酬率`

Exclude the header, blank slots, and `合計` row. The template already contains one orange conditional-format rule per target column ahead of the alternating-row rule. Its rules cover rows 2:51, test `ISNUMBER`, rank only numeric cells with `FILTER`, and break ties by row order. Preserve these rules for reports with at most 50 holdings. Use a light orange fill such as RGB `(1.0, 0.85, 0.65)`.

Never create, transform, upload, import, export, or verify an Excel/XLSX file for Google Sheet mode. If direct native Google Sheet creation or writing is unavailable, stop and report the connector limitation instead of falling back to Excel.

Keep the market symbol visible. Use formulas when the user may edit prices; otherwise sourced static values are acceptable. Freeze row 1 and columns A:B, make headers bold, filter all holding rows, apply subtle alternating rows, and leave a visually distinct `合計` row outside the filter.

For Taiwan reports, write the static calculated values from SQLite output into `昨日收盤價`, `本日盈虧`, `本日報酬率`, `20日盈虧`, and `20日報酬率`. Do not replace them with GoogleFinance formulas. GoogleFinance is not the fallback for missing Taiwan history: rerun/backfill FinMind and stop if the database still lacks 21 closes.

Before delivery, confirm every screenshot holding appears once, recalculate two rows, verify totals, check for formula errors, confirm exactly five orange holding cells in each target column, and state the market-data date.
