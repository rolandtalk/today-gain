#!/usr/bin/env python3
"""Maintain Today Gain daily closes and five intraday snapshots in SQLite."""

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


def backfill_stock(db: sqlite3.Connection, ticker: str, as_of: str, keep: int = 20) -> int:
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
            exchange = "otc" if symbol.endswith(".two") else "tse"
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
            if ticker not in existing:
                stored_closes = db.execute(
                    "SELECT COUNT(*) FROM prices WHERE ticker=?", (ticker,)
                ).fetchone()[0]
                if stored_closes < 20:
                    price_rows += backfill_stock(db, ticker, as_of, keep=20)
                added += 1
    if seen:
        placeholders = ",".join("?" for _ in seen)
        db.execute(f"UPDATE stock_universe SET active=0 WHERE ticker NOT IN ({placeholders})", tuple(sorted(seen)))
    return added, price_rows


def migrate_existing_prices(db: sqlite3.Connection, as_of: str) -> int:
    """On first migration, keep only the most recent 20 closes per stock."""
    marker = db.execute("SELECT COUNT(*) FROM collector_runs WHERE command='initial-prune-20d'").fetchone()[0]
    if marker:
        return 0
    before = db.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
    db.execute(
        """
        DELETE FROM prices
        WHERE rowid IN (
            SELECT rowid FROM (
                SELECT rowid,
                       ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY trade_date DESC) AS rn
                FROM prices
            ) WHERE rn > 20
        )
        """
    )
    removed = before - db.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
    db.execute(
        "INSERT INTO collector_runs(started_at, command, status, rows_written, detail) VALUES (?, 'initial-prune-20d', 'ok', 0, ?)",
        (now_taipei().isoformat(timespec="seconds"), f"removed {removed} older daily closes; as-of {as_of}"),
    )
    return removed


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
    parser.add_argument("--db", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--holdings", type=Path, required=True)
    sync = sub.add_parser("sync-holdings")
    sync.add_argument("--holdings", type=Path, required=True)
    sample = sub.add_parser("sample")
    sample.add_argument("--time", choices=sorted(INTRADAY_SLOTS))
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
            pruned = migrate_existing_prices(db, today)
            rows = backfilled
            detail = f"new stocks={added}; backfilled={backfilled}; initial older closes removed={pruned}"
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
    print(json.dumps({"command": args.command, "rows_written": rows, "detail": detail}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"today-gain collector error: {exc}", file=sys.stderr)
        raise SystemExit(1)
