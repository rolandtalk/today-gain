#!/usr/bin/env python3
"""Maintain Today Gain prices and calculate SQLite-backed gain reports."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


TAIPEI = ZoneInfo("Asia/Taipei")
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
TWSE_MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
INTRADAY_SLOTS = {"09:06", "10:06", "11:06", "12:06", "13:06"}
TAIWAN_SYMBOL_SUFFIXES = (".tw", ".two")
BASELINE_SESSIONS = 20
REQUIRED_CLOSES = BASELINE_SESSIONS + 1
DEFAULT_DB = Path.home() / ".codex" / "data" / "today-gain" / "today_gain.sqlite3"
REPORT_COLUMNS = [
    "股票", "代號", "GoogleFinance代號", "股數", "截圖市價", "成本價", "成本",
    "目前市值", "庫存損益", "庫存報酬率", "昨日收盤價", "本日盈虧",
    "本日報酬率", "20日盈虧", "20日報酬率", "昨日收盤日期", "20D基準日期",
]


def now_taipei() -> datetime:
    return datetime.now(TAIPEI)


def number(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip().replace(",", "")
    if not text:
        return None
    if text.endswith("%"):
        return float(text[:-1]) / 100
    return float(text)


def request_json(url: str, attempts: int = 3) -> dict:
    error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Codex today-gain-prices"})
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except Exception as exc:  # network retry boundary
            error = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"request failed: {url}: {error}")


def connect(db_path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA journal_mode = WAL")
    return db


def create_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS stock_universe (
            ticker TEXT PRIMARY KEY,
            stock_name TEXT NOT NULL,
            market_symbol TEXT NOT NULL,
            exchange TEXT NOT NULL CHECK (exchange IN ('tse', 'otc')),
            first_seen_date TEXT NOT NULL,
            last_seen_date TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
        );

        CREATE TABLE IF NOT EXISTS prices (
            ticker TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            close REAL NOT NULL,
            source TEXT NOT NULL,
            captured_at TEXT,
            PRIMARY KEY (ticker, trade_date)
        );

        CREATE TABLE IF NOT EXISTS intraday_prices (
            ticker TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            sample_time TEXT NOT NULL,
            price REAL NOT NULL,
            quote_time TEXT,
            captured_at TEXT NOT NULL,
            source TEXT NOT NULL,
            PRIMARY KEY (ticker, trade_date, sample_time),
            FOREIGN KEY (ticker) REFERENCES stock_universe(ticker)
        );

        CREATE INDEX IF NOT EXISTS idx_prices_ticker_date
            ON prices (ticker, trade_date DESC);
        CREATE INDEX IF NOT EXISTS idx_intraday_date_slot
            ON intraday_prices (trade_date, sample_time, ticker);

        CREATE TABLE IF NOT EXISTS collector_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            command TEXT NOT NULL,
            status TEXT NOT NULL,
            rows_written INTEGER NOT NULL DEFAULT 0,
            detail TEXT
        );

        CREATE TABLE IF NOT EXISTS ticker_transitions (
            transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            old_symbol TEXT NOT NULL,
            new_symbol TEXT NOT NULL,
            old_exchange TEXT NOT NULL,
            new_exchange TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT 'holdings-sync'
        );

        DROP VIEW IF EXISTS latest_stock_prices;
        CREATE VIEW latest_stock_prices AS
        SELECT
            u.ticker,
            u.stock_name,
            u.market_symbol,
            (SELECT trade_date FROM prices p
              WHERE p.ticker = u.ticker ORDER BY trade_date DESC LIMIT 1) AS close_date,
            (SELECT close FROM prices p
              WHERE p.ticker = u.ticker ORDER BY trade_date DESC LIMIT 1) AS last_close,
            (SELECT trade_date FROM intraday_prices i
              WHERE i.ticker = u.ticker ORDER BY trade_date DESC, sample_time DESC LIMIT 1) AS intraday_date,
            (SELECT sample_time FROM intraday_prices i
              WHERE i.ticker = u.ticker ORDER BY trade_date DESC, sample_time DESC LIMIT 1) AS intraday_sample,
            (SELECT price FROM intraday_prices i
              WHERE i.ticker = u.ticker ORDER BY trade_date DESC, sample_time DESC LIMIT 1) AS current_trade_price
        FROM stock_universe u
        WHERE u.active = 1;
        """
    )
    columns = {row[1] for row in db.execute("PRAGMA table_info(prices)")}
    if "captured_at" not in columns:
        db.execute("ALTER TABLE prices ADD COLUMN captured_at TEXT")


def finmind_history(ticker: str, start_date: str, end_date: str) -> list[tuple[str, float]]:
    query = urllib.parse.urlencode(
        {
            "dataset": "TaiwanStockPrice",
            "data_id": ticker,
            "start_date": start_date,
            "end_date": end_date,
        }
    )
    payload = request_json(f"{FINMIND_URL}?{query}")
    if payload.get("status") != 200:
        raise RuntimeError(f"{ticker}: {payload.get('msg', 'FinMind error')}")
    return [(row["date"], float(row["close"])) for row in payload.get("data", [])]


def backfill_stock(
    db: sqlite3.Connection, ticker: str, as_of: str, keep: int = REQUIRED_CLOSES
) -> int:
    end = datetime.strptime(as_of, "%Y-%m-%d").date()
    start = end - timedelta(days=60)
    rows = finmind_history(ticker, start.isoformat(), as_of)[-keep:]
    captured = now_taipei().isoformat(timespec="seconds")
    db.executemany(
        """
        INSERT INTO prices (ticker, trade_date, close, source, captured_at)
        VALUES (?, ?, ?, 'FinMind TaiwanStockPrice', ?)
        ON CONFLICT(ticker, trade_date) DO UPDATE SET
            close=excluded.close, source=excluded.source, captured_at=excluded.captured_at
        """,
        [(ticker, trade_date, close, captured) for trade_date, close in rows],
    )
    return len(rows)


def sync_holdings(db: sqlite3.Connection, csv_path: Path, as_of: str) -> tuple[int, int]:
    existing = {row[0] for row in db.execute("SELECT ticker FROM stock_universe")}
    seen: set[str] = set()
    added = 0
    price_rows = 0
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            ticker = row["代號"].strip()
            symbol = row["GoogleFinance代號"].strip().lower()
            prior = db.execute(
                "SELECT market_symbol, exchange FROM stock_universe WHERE ticker=?", (ticker,)
            ).fetchone()
            if not symbol and prior:
                symbol = prior["market_symbol"]
            if not symbol.endswith(TAIWAN_SYMBOL_SUFFIXES):
                raise ValueError(
                    f"{ticker}: SQLite fast mode currently supports Taiwan symbols only "
                    f"(.tw/.two), got {row['GoogleFinance代號'].strip()!r}"
                )
            exchange = "otc" if symbol.endswith(".two") else "tse"
            if prior and (prior["market_symbol"] != symbol or prior["exchange"] != exchange):
                db.execute(
                    """
                    INSERT INTO ticker_transitions
                        (ticker, changed_at, old_symbol, new_symbol, old_exchange, new_exchange)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ticker,
                        now_taipei().isoformat(timespec="seconds"),
                        prior["market_symbol"],
                        symbol,
                        prior["exchange"],
                        exchange,
                    ),
                )
            seen.add(ticker)
            db.execute(
                """
                INSERT INTO stock_universe
                    (ticker, stock_name, market_symbol, exchange, first_seen_date, last_seen_date, active)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(ticker) DO UPDATE SET
                    stock_name=excluded.stock_name,
                    market_symbol=excluded.market_symbol,
                    exchange=excluded.exchange,
                    last_seen_date=excluded.last_seen_date,
                    active=1
                """,
                (ticker, row["股票"].strip(), symbol, exchange, as_of, as_of),
            )
            stored_closes = db.execute(
                "SELECT COUNT(*) FROM prices WHERE ticker=?", (ticker,)
            ).fetchone()[0]
            if stored_closes < REQUIRED_CLOSES:
                price_rows += backfill_stock(db, ticker, as_of)
            if ticker not in existing:
                added += 1
    if seen:
        placeholders = ",".join("?" for _ in seen)
        db.execute(f"UPDATE stock_universe SET active=0 WHERE ticker NOT IN ({placeholders})", tuple(sorted(seen)))
    return added, price_rows


def refresh_daily_closes(db: sqlite3.Connection, as_of: str) -> int:
    rows_written = 0
    captured = now_taipei().isoformat(timespec="seconds")
    for row in db.execute("SELECT ticker FROM stock_universe WHERE active=1 ORDER BY ticker"):
        ticker = row[0]
        latest = db.execute("SELECT MAX(trade_date) FROM prices WHERE ticker=?", (ticker,)).fetchone()[0]
        if latest:
            start = (datetime.strptime(latest, "%Y-%m-%d").date() + timedelta(days=1)).isoformat()
        else:
            start = (datetime.strptime(as_of, "%Y-%m-%d").date() - timedelta(days=60)).isoformat()
        if start > as_of:
            continue
        history = finmind_history(ticker, start, as_of)
        db.executemany(
            """
            INSERT INTO prices (ticker, trade_date, close, source, captured_at)
            VALUES (?, ?, ?, 'FinMind TaiwanStockPrice', ?)
            ON CONFLICT(ticker, trade_date) DO UPDATE SET
                close=excluded.close, source=excluded.source, captured_at=excluded.captured_at
            """,
            [(ticker, trade_date, close, captured) for trade_date, close in history],
        )
        rows_written += len(history)
    return rows_written


def ensure_report_history(db: sqlite3.Connection, ticker: str, cutoff: str) -> int:
    count = db.execute(
        "SELECT COUNT(*) FROM prices WHERE ticker=? AND trade_date<=?", (ticker, cutoff)
    ).fetchone()[0]
    if count >= REQUIRED_CLOSES:
        return 0
    return backfill_stock(db, ticker, cutoff, keep=REQUIRED_CLOSES)


def calculate_report(db: sqlite3.Connection, csv_path: Path, as_of: str) -> list[dict]:
    cutoff = (datetime.strptime(as_of, "%Y-%m-%d").date() - timedelta(days=1)).isoformat()
    refresh_daily_closes(db, cutoff)
    source_rows: list[dict] = []
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        source_rows = list(csv.DictReader(handle))
    output: list[dict] = []
    for source in source_rows:
        ticker = source["代號"].strip()
        ensure_report_history(db, ticker, cutoff)
        closes = list(
            db.execute(
                """
                SELECT trade_date, close FROM prices
                WHERE ticker=? AND trade_date<=?
                ORDER BY trade_date DESC LIMIT ?
                """,
                (ticker, cutoff, REQUIRED_CLOSES),
            )
        )
        if len(closes) < REQUIRED_CLOSES:
            raise RuntimeError(
                f"{ticker}: need {REQUIRED_CLOSES} closes through {cutoff}, got {len(closes)}"
            )
        shares = number(source["股數"])
        current = number(source["截圖市價"])
        if shares is None or current is None:
            raise ValueError(f"{ticker}: 股數 and 截圖市價 are required")
        prior = float(closes[0]["close"])
        baseline = float(closes[BASELINE_SESSIONS]["close"])
        row = {column: source.get(column, "") for column in REPORT_COLUMNS[:10]}
        row.update(
            {
                "昨日收盤價": prior,
                "本日盈虧": (current - prior) * shares,
                "本日報酬率": (current - prior) / prior,
                "20日盈虧": (current - baseline) * shares,
                "20日報酬率": (current - baseline) / baseline,
                "昨日收盤日期": closes[0]["trade_date"],
                "20D基準日期": closes[BASELINE_SESSIONS]["trade_date"],
            }
        )
        output.append(row)
    return output


def write_report_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def purge_old_intraday(db: sqlite3.Connection, today: str) -> int:
    cursor = db.execute("DELETE FROM intraday_prices WHERE trade_date < ?", (today,))
    return cursor.rowcount


def realtime_quotes(db: sqlite3.Connection) -> dict[str, dict]:
    rows = list(db.execute("SELECT ticker, exchange FROM stock_universe WHERE active=1 ORDER BY ticker"))
    channels = "|".join(f"{row['exchange']}_{row['ticker']}.tw" for row in rows)
    query = urllib.parse.urlencode({"ex_ch": channels, "json": "1", "delay": "0"})
    payload = request_json(f"{TWSE_MIS_URL}?{query}")
    if payload.get("rtcode") != "0000":
        raise RuntimeError(f"TWSE MIS error: {payload.get('rtmessage')}")
    return {item["c"]: item for item in payload.get("msgArray", [])}


def capture_intraday(db: sqlite3.Connection, sample_time: str, today: str) -> int:
    if sample_time not in INTRADAY_SLOTS:
        raise ValueError(f"sample time must be one of: {', '.join(sorted(INTRADAY_SLOTS))}")
    quotes = realtime_quotes(db)
    captured = now_taipei().isoformat(timespec="seconds")
    written = 0
    for ticker, item in quotes.items():
        raw = item.get("z")
        if raw in (None, "", "-"):
            raw = item.get("pz")
        try:
            price = float(raw)
        except (TypeError, ValueError):
            continue
        quote_date = item.get("d") or item.get("^") or today.replace("-", "")
        trade_date = f"{quote_date[:4]}-{quote_date[4:6]}-{quote_date[6:8]}"
        # On weekends/holidays MIS can return the prior session's final quote.
        # Never store that stale quote as one of today's scheduled snapshots.
        if trade_date != today:
            continue
        db.execute(
            """
            INSERT INTO intraday_prices
                (ticker, trade_date, sample_time, price, quote_time, captured_at, source)
            VALUES (?, ?, ?, ?, ?, ?, 'TWSE MIS')
            ON CONFLICT(ticker, trade_date, sample_time) DO UPDATE SET
                price=excluded.price,
                quote_time=excluded.quote_time,
                captured_at=excluded.captured_at,
                source=excluded.source
            """,
            (ticker, trade_date, sample_time, price, item.get("t") or item.get("%"), captured),
        )
        written += 1
    return written


def log_run(db: sqlite3.Connection, command: str, rows: int, detail: str) -> None:
    db.execute(
        "INSERT INTO collector_runs(started_at, command, status, rows_written, detail) VALUES (?, ?, 'ok', ?, ?)",
        (now_taipei().isoformat(timespec="seconds"), command, rows, detail),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--holdings", type=Path, required=True)
    sync = sub.add_parser("sync-holdings")
    sync.add_argument("--holdings", type=Path, required=True)
    sample = sub.add_parser("sample")
    sample.add_argument("--time", choices=sorted(INTRADAY_SLOTS))
    calculate = sub.add_parser("calculate")
    calculate.add_argument("--holdings", type=Path, required=True)
    calculate.add_argument("--as-of", help="screenshot date in YYYY-MM-DD; default: today in Taipei")
    calculate.add_argument("--output", type=Path, help="write Sheet-ready UTF-8 CSV")
    sub.add_parser("close")
    sub.add_parser("run")
    args = parser.parse_args()

    current = now_taipei()
    today = current.date().isoformat()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    with connect(args.db) as db:
        create_schema(db)
        rows = 0
        detail = ""
        if args.command in {"init", "sync-holdings"}:
            added, backfilled = sync_holdings(db, args.holdings, today)
            rows = backfilled
            detail = f"new stocks={added}; backfilled={backfilled}"
        elif args.command == "calculate":
            report_date = args.as_of or today
            added, backfilled = sync_holdings(db, args.holdings, report_date)
            report = calculate_report(db, args.holdings, report_date)
            if args.output:
                write_report_csv(args.output, report)
            rows = len(report)
            detail = (
                f"report date={report_date}; holdings={len(report)}; new stocks={added}; "
                f"backfilled={backfilled}; output={args.output or 'stdout'}"
            )
        elif args.command == "sample":
            removed = purge_old_intraday(db, today)
            slot = args.time or current.strftime("%H:%M")
            rows = capture_intraday(db, slot, today)
            detail = f"slot={slot}; purged old intraday={removed}"
        elif args.command == "close":
            removed = purge_old_intraday(db, today)
            rows = refresh_daily_closes(db, today)
            detail = f"daily closes refreshed; purged old intraday={removed}"
        elif args.command == "run":
            removed = purge_old_intraday(db, today)
            clock = current.strftime("%H:%M")
            if clock in INTRADAY_SLOTS:
                rows = capture_intraday(db, clock, today)
                detail = f"slot={clock}; purged old intraday={removed}"
            else:
                rows = refresh_daily_closes(db, today)
                detail = f"close refresh; purged old intraday={removed}"
        log_run(db, args.command, rows, detail)
        db.commit()
    result = {"command": args.command, "rows_written": rows, "detail": detail, "db": str(args.db)}
    if args.command == "calculate" and not args.output:
        result["holdings"] = report
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"today-gain collector error: {exc}", file=sys.stderr)
        raise SystemExit(1)
