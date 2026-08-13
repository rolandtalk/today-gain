#!/usr/bin/env python3
"""Build a no-dependency XLSX workbook for the today-gain skill."""

from __future__ import annotations

import csv
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape


HEADERS = [
    "股票",
    "代號",
    "GoogleFinance代號",
    "股數",
    "截圖市價",
    "成本價",
    "成本",
    "目前市值",
    "庫存損益",
    "庫存報酬率",
    "昨日收盤價",
    "本日盈虧",
    "本日報酬率",
    "20日盈虧",
    "20日報酬率",
]


def parse_number(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    is_percent = text.endswith("%")
    if is_percent:
        text = text[:-1].strip()
    try:
        number = float(text)
    except ValueError:
        return None
    return number / 100 if is_percent else number


def cell_ref(row: int, col: int) -> str:
    letters = ""
    while col:
        col, rem = divmod(col - 1, 26)
        letters = chr(65 + rem) + letters
    return f"{letters}{row}"


def cell_xml(row: int, col: int, value, style: int | None = None) -> str:
    ref = cell_ref(row, col)
    style_attr = f' s="{style}"' if style is not None else ""
    if value is None or value == "":
        return f'<c r="{ref}"{style_attr}/>'
    if isinstance(value, str):
        return f'<c r="{ref}" t="inlineStr"{style_attr}><is><t>{escape(value)}</t></is></c>'
    return f'<c r="{ref}"{style_attr}><v>{value}</v></c>'


def style_id(base_style: int | None, row: int) -> int | None:
    if row == 1:
        return 1
    if base_style is None:
        return 6 if row % 2 == 0 else None
    if row % 2 == 0:
        return base_style + 10
    return base_style


def sheet_xml(rows: list[list], styles: list[list[int | None]]) -> str:
    row_xml = []
    for r_idx, row in enumerate(rows, start=1):
        cells = [cell_xml(r_idx, c_idx, value, style_id(styles[r_idx - 1][c_idx - 1], r_idx)) for c_idx, value in enumerate(row, start=1)]
        row_xml.append(f'<row r="{r_idx}">{"".join(cells)}</row>')
    dimension = f"A1:O{len(rows)}"
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <dimension ref="{dimension}"/>
  <sheetViews><sheetView workbookViewId="0"><pane xSplit="2" ySplit="1" topLeftCell="C2" activePane="bottomRight" state="frozen"/></sheetView></sheetViews>
  <sheetFormatPr defaultRowHeight="18"/>
  <cols>
    <col min="1" max="2" width="12" customWidth="1"/>
    <col min="3" max="3" width="20" customWidth="1"/>
    <col min="4" max="15" width="14" customWidth="1"/>
  </cols>
  <sheetData>{"".join(row_xml)}</sheetData>
  <autoFilter ref="{dimension}"/>
</worksheet>'''


def styles_xml() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <numFmts count="2">
    <numFmt numFmtId="164" formatCode="+#,##0;-#,##0;0"/>
    <numFmt numFmtId="165" formatCode="+0.0%;-0.0%;0.0%"/>
  </numFmts>
  <fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
  <fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFD9EAF7"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFFFF5CC"/><bgColor indexed="64"/></patternFill></fill></fills>
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="16">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="4" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
    <xf numFmtId="10" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
    <xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
    <xf numFmtId="0" fontId="0" fillId="3" borderId="0" xfId="0" applyFill="1"/>
    <xf numFmtId="0" fontId="0" fillId="3" borderId="0" xfId="0" applyFill="1"/>
    <xf numFmtId="0" fontId="0" fillId="3" borderId="0" xfId="0" applyFill="1"/>
    <xf numFmtId="4" fontId="0" fillId="3" borderId="0" xfId="0" applyNumberFormat="1" applyFill="1"/>
    <xf numFmtId="10" fontId="0" fillId="3" borderId="0" xfId="0" applyNumberFormat="1" applyFill="1"/>
    <xf numFmtId="164" fontId="0" fillId="3" borderId="0" xfId="0" applyNumberFormat="1" applyFill="1"/>
    <xf numFmtId="0" fontId="0" fillId="3" borderId="0" xfId="0" applyFill="1"/>
    <xf numFmtId="4" fontId="0" fillId="3" borderId="0" xfId="0" applyNumberFormat="1" applyFill="1"/>
    <xf numFmtId="10" fontId="0" fillId="3" borderId="0" xfId="0" applyNumberFormat="1" applyFill="1"/>
    <xf numFmtId="164" fontId="0" fillId="3" borderId="0" xfId="0" applyNumberFormat="1" applyFill="1"/>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''


def stock_symbol(record: dict[str, str]) -> str:
    explicit = (record.get("GoogleFinance代號") or record.get("googlefinance代號") or "").strip()
    if explicit:
        return explicit
    code = (record.get("代號") or "").strip()
    return f"{code}.tw" if code else ""


def build_rows(input_csv: Path) -> tuple[list[list], list[list[int | None]]]:
    rows = [HEADERS]
    styles = [[1] * len(HEADERS)]
    with input_csv.open(newline="", encoding="utf-8-sig") as handle:
        for record in csv.DictReader(handle):
            shares = parse_number(record.get("股數"))
            current = parse_number(record.get("截圖市價"))
            prev_close = parse_number(record.get("昨日收盤價"))
            base_20d = parse_number(record.get("20日基準價") or record.get("20D基準價") or record.get("20d基準價"))
            daily_gain = None
            daily_return = None
            gain_20d = parse_number(record.get("20日盈虧"))
            return_20d = parse_number(record.get("20日報酬率"))
            if shares is not None and current is not None and prev_close not in (None, 0):
                daily_gain = round((current - prev_close) * shares)
                daily_return = (current - prev_close) / prev_close
            if gain_20d is None and shares is not None and current is not None and base_20d not in (None, 0):
                gain_20d = round((current - base_20d) * shares)
            if return_20d is None and current is not None and base_20d not in (None, 0):
                return_20d = (current - base_20d) / base_20d
            row = [
                record.get("股票", "").strip(),
                record.get("代號", "").strip(),
                stock_symbol(record),
                shares,
                current,
                parse_number(record.get("成本價")),
                parse_number(record.get("成本")),
                parse_number(record.get("目前市值")),
                parse_number(record.get("庫存損益")),
                parse_number(record.get("庫存報酬率")),
                prev_close,
                daily_gain,
                daily_return,
                gain_20d,
                return_20d,
            ]
            rows.append(row)
            styles.append([None, None, None, 2, 3, 3, 3, 3, 5, 4, 3, 5, 4, 5, 4])
    return rows, styles


def write_xlsx(rows: list[list], styles: list[list[int | None]], output: Path) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    files = {
        "[Content_Types].xml": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/><Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/><Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/></Types>''',
        "_rels/.rels": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/></Relationships>''',
        "docProps/app.xml": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>Codex</Application></Properties>''',
        "docProps/core.xml": f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>本日個股盈虧計算</dc:title><dc:creator>Codex</dc:creator><dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified></cp:coreProperties>''',
        "xl/workbook.xml": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="本日個股盈虧" sheetId="1" r:id="rId1"/></sheets></workbook>''',
        "xl/_rels/workbook.xml.rels": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>''',
        "xl/styles.xml": styles_xml(),
        "xl/worksheets/sheet1.xml": sheet_xml(rows, styles),
    }
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: build_today_gain_xlsx.py holdings.csv output.xlsx", file=sys.stderr)
        return 2
    input_csv = Path(sys.argv[1])
    output = Path(sys.argv[2])
    rows, styles = build_rows(input_csv)
    write_xlsx(rows, styles, output)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
