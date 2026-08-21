#!/usr/bin/env python3
"""Prepare a complete Today Gain calculation and one-call Sheets update."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path

import today_gain_prices as prices


SHEET_COLUMNS = prices.REPORT_COLUMNS[:15]
PERSISTENT_OCR_PYTHON = prices.DEFAULT_DB.parent / "ocr-venv" / "bin" / "python"


def cell(value):
    if value in (None, ""):
        return {}
    if isinstance(value, (int, float)):
        return {"userEnteredValue": {"numberValue": value}}
    return {"userEnteredValue": {"stringValue": str(value)}}


def load_calculated(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    converted = []
    numeric = set(SHEET_COLUMNS[3:])
    for source in rows:
        row = dict(source)
        for column in numeric:
            row[column] = prices.number(row[column])
        converted.append(row)
    return converted


def build_batch(rows: list[dict], target: dict) -> list[dict]:
    sheet_id = int(target["sheet_id"])
    values = [[row[column] for column in SHEET_COLUMNS] for row in rows]
    if target["topology"] == "template-50":
        if len(values) > 50:
            raise RuntimeError("template-50 fast path supports at most 50 holdings")
        padded = values + [[None] * 15 for _ in range(50 - len(values))]
        return [
            {
                "updateCells": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "endRowIndex": 51,
                        "startColumnIndex": 0,
                        "endColumnIndex": 15,
                    },
                    "rows": [{"values": [cell(value) for value in row]} for row in padded],
                    "fields": "userEnteredValue",
                }
            },
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "ROWS",
                        "startIndex": 1,
                        "endIndex": 51,
                    },
                    "properties": {"hiddenByUser": True},
                    "fields": "hiddenByUser",
                }
            },
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "ROWS",
                        "startIndex": 1,
                        "endIndex": 1 + len(values),
                    },
                    "properties": {"hiddenByUser": False},
                    "fields": "hiddenByUser",
                }
            },
        ]
    return [
        {
            "updateCells": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": 1 + len(values),
                    "startColumnIndex": 0,
                    "endColumnIndex": 15,
                },
                "rows": [{"values": [cell(value) for value in row]} for row in values],
                "fields": "userEnteredValue",
            }
        }
    ]


def verification(rows: list[dict]) -> dict:
    names = [row["股票"] for row in rows]
    tickers = [row["代號"] for row in rows]
    top_columns = ["目前市值", "庫存損益", "庫存報酬率", "本日盈虧", "本日報酬率", "20日盈虧", "20日報酬率"]
    return {
        "holding_count": len(rows),
        "unique_tickers": len(set(tickers)),
        "duplicates": sorted({ticker for ticker in tickers if tickers.count(ticker) > 1}),
        "totals": {
            "shares": sum(row["股數"] for row in rows),
            "cost": sum(row["成本"] for row in rows),
            "market_value": sum(row["目前市值"] for row in rows),
            "inventory_gain": sum(row["庫存損益"] for row in rows),
            "today_gain": sum(row["本日盈虧"] for row in rows),
            "gain_20d": sum(row["20日盈虧"] for row in rows),
        },
        "top_five_rows": {
            column: [
                {"row": names.index(row["股票"]) + 2, "ticker": row["代號"], "value": row[column]}
                for row in sorted(rows, key=lambda item: item[column], reverse=True)[:5]
            ]
            for column in top_columns
        },
        "prior_close_dates": sorted({row["昨日收盤日期"] for row in rows}),
        "baseline_dates": sorted({row["20D基準日期"] for row in rows}),
    }


def run_ocr(image: Path, output: Path, python: str) -> dict:
    helper = Path(__file__).with_name("today_gain_ocr.py")
    started = time.monotonic()
    completed = subprocess.run(
        [python, str(helper), str(image), "--output", str(output)],
        check=False,
        capture_output=True,
        text=True,
    )
    return {
        "ok": completed.returncode == 0,
        "seconds": round(time.monotonic() - started, 3),
        "output": str(output),
        "error": completed.stderr.strip() or None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--holdings", type=Path, help="verified screenshot holdings CSV")
    parser.add_argument("--image", type=Path, help="optional screenshot for local OCR audit")
    parser.add_argument(
        "--ocr-python",
        default=str(PERSISTENT_OCR_PYTHON if PERSISTENT_OCR_PYTHON.exists() else Path(sys.executable)),
    )
    parser.add_argument("--date", required=True)
    parser.add_argument("--market", default="tw")
    parser.add_argument("--db", type=Path, default=prices.DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    ocr = None
    if args.image:
        ocr = run_ocr(args.image, args.output_dir / "ocr.json", args.ocr_python)
    if not args.holdings:
        print(json.dumps({
            "status": "needs_verified_holdings",
            "ocr": ocr,
            "reason": "OCR is an audit aid; no Sheet write is prepared without a verified holdings CSV.",
        }, ensure_ascii=False))
        return 2

    started = time.monotonic()
    calculated_path = args.output_dir / "calculated.csv"
    args.db.parent.mkdir(parents=True, exist_ok=True)
    with prices.connect(args.db) as db:
        prices.create_schema(db)
        added, backfilled = prices.sync_holdings(db, args.holdings, args.date)
        report = prices.calculate_report(db, args.holdings, args.date)
        prices.write_report_csv(calculated_path, report)
        target_row = db.execute(
            "SELECT * FROM report_targets WHERE market=? AND report_date=?",
            (args.market, args.date),
        ).fetchone()
        status = prices.database_status(db)
        db.commit()
    rows = load_calculated(calculated_path)
    target = dict(target_row) if target_row else None
    batch_path = args.output_dir / "sheets-batch.json"
    if target:
        batch_path.write_text(
            json.dumps(build_batch(rows, target), ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    manifest = {
        "status": "ready" if target else "needs_sheet_target",
        "seconds": round(time.monotonic() - started, 3),
        "date": args.date,
        "market": args.market,
        "new_stocks": added,
        "backfilled_rows": backfilled,
        "calculated_csv": str(calculated_path),
        "sheet_batch": str(batch_path) if target else None,
        "target": target,
        "ocr": ocr,
        "database": status,
        "verification": verification(rows),
    }
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"today-gain turbo error: {exc}", file=sys.stderr)
        raise SystemExit(1)
