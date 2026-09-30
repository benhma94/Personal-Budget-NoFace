"""Shared helper: build a minimal but structurally faithful Personal
Budget.xlsx (Journal + Budget + Ledger) for budget_plan_api tests, with a
hand-built Budget sheet so tests never depend on openpyxl's own
serialization shape for the raw-XML surgery in budget_plan_api.write_budget_plan.

Row layout mirrors the real sheet: row 13 holds month-end dates in columns
B onward; rows 16-47 are the numeric grid, one row per category, with
every row present as a <row> element (even genuinely blank spacer rows)
since a formatted Excel grid keeps row elements once touched, and
write_budget_plan relies on that when appending a new month column.
"""
from __future__ import annotations

import zipfile
from datetime import date
from pathlib import Path

_EXCEL_EPOCH = date(1899, 12, 30)
_DATE_STYLE = "4"
_AMOUNT_STYLE = "137"

_STYLE_XFS = "".join(
    f'<xf numFmtId="{14 if i == 4 else 0}" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    for i in range(138)
)
_STYLES_XML = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="1"><fill><patternFill patternType="none"/></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="138">{_STYLE_XFS}</cellXfs>
</styleSheet>"""

_CONTENT_TYPES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/worksheets/sheet3.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
<Override PartName="/xl/calcChain.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.calcChain+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""

_ROOT_RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

_WORKBOOK_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets>
<sheet name="Journal" sheetId="1" r:id="rId1"/>
<sheet name="Budget" sheetId="2" r:id="rId2"/>
<sheet name="Ledger" sheetId="3" r:id="rId3"/>
</sheets>
<calcPr calcId="191029"/>
</workbook>"""

_WORKBOOK_RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet3.xml"/>
<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
<Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/calcChain" Target="calcChain.xml"/>
<Relationship Id="rId6" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

_CALC_CHAIN_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<calcChain xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><c r="D3" i="1"/></calcChain>"""

_FIRST_CATEGORY_ROW = 16
_LAST_CATEGORY_ROW = 47


def _excel_serial(d: date) -> int:
    return (d - _EXCEL_EPOCH).days


def build_budget_plan_fixture(
    path: Path,
    *,
    month_end_dates: list[date],
    category_rows: dict[int, str],
    values: dict[tuple[int, int], float],
    accounts: list[str] | None = None,
) -> None:
    """Write a workbook with Journal + Budget + Ledger sheets.

    `month_end_dates` become row 13's columns B, C, D, ... in order.
    `category_rows` maps row number (16-47) -> category label; rows in
    16-47 not present in this dict are still emitted as empty <row>
    elements, mirroring a real formatted-but-unused grid row.
    `values` maps (row_number, column_index_0_based_from_B) -> numeric
    value for that row/month cell.
    """
    accounts = accounts or ["Primary Checking", "Salary", "Food"]

    for row_number in category_rows:
        if not (_FIRST_CATEGORY_ROW <= row_number <= _LAST_CATEGORY_ROW):
            raise ValueError(
                f"category_rows has row {row_number}, outside the valid "
                f"range [{_FIRST_CATEGORY_ROW}, {_LAST_CATEGORY_ROW}]"
            )
    for row_number, col_offset in values:
        if row_number not in category_rows:
            raise ValueError(
                f"values has key ({row_number}, {col_offset}) but row "
                f"{row_number} is not in category_rows (valid rows: "
                f"{sorted(category_rows)})"
            )
        if not (0 <= col_offset < len(month_end_dates)):
            raise ValueError(
                f"values has key ({row_number}, {col_offset}) but "
                f"col_offset {col_offset} is out of range for "
                f"{len(month_end_dates)} month_end_dates (valid offsets: "
                f"0-{len(month_end_dates) - 1})"
            )

    strings = ["Journal", "Date", "Debit", "Credit", "Amount", "Note",
               "Ledger", "Account", "Beginning Balance",
               *accounts, *category_rows.values()]
    # de-dup while preserving first-seen order (accounts and category
    # labels may overlap, e.g. "Salary")
    seen: dict[str, int] = {}
    ordered: list[str] = []
    for s in strings:
        if s not in seen:
            seen[s] = len(ordered)
            ordered.append(s)
    index = seen

    sheet1 = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<dimension ref="A1:E2"/>'
        "<sheetData>"
        f'<row r="1" spans="1:5"><c r="A1" t="s"><v>{index["Journal"]}</v></c></row>'
        f'<row r="2" spans="1:5"><c r="A2" t="s"><v>{index["Date"]}</v></c>'
        f'<c r="B2" t="s"><v>{index["Debit"]}</v></c><c r="C2" t="s"><v>{index["Credit"]}</v></c>'
        f'<c r="D2" t="s"><v>{index["Amount"]}</v></c><c r="E2" t="s"><v>{index["Note"]}</v></c></row>'
        "</sheetData></worksheet>"
    )

    last_col_letter = chr(ord("B") + len(month_end_dates) - 1) if month_end_dates else "A"
    header_cells = "".join(
        f'<c r="{chr(ord("B") + i)}13" s="{_DATE_STYLE}"><v>{_excel_serial(d)}</v></c>'
        for i, d in enumerate(month_end_dates)
    )
    header_row = f'<row r="13" spans="1:{ord(last_col_letter) - ord("A") + 1}">{header_cells}</row>'

    grid_rows = []
    for row_number in range(_FIRST_CATEGORY_ROW, _LAST_CATEGORY_ROW + 1):
        label = category_rows.get(row_number)
        cells = ""
        if label is not None:
            cells += f'<c r="A{row_number}" t="s"><v>{index[label]}</v></c>'
        for col_offset in range(len(month_end_dates)):
            col_letter = chr(ord("B") + col_offset)
            value = values.get((row_number, col_offset))
            if value is not None:
                cells += f'<c r="{col_letter}{row_number}" s="{_AMOUNT_STYLE}"><v>{value}</v></c>'
            else:
                cells += f'<c r="{col_letter}{row_number}" s="{_AMOUNT_STYLE}"/>'
        grid_rows.append(f'<row r="{row_number}" spans="1:{ord(last_col_letter) - ord("A") + 1}">{cells}</row>')

    sheet2 = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="A1:{last_col_letter}{_LAST_CATEGORY_ROW}"/>'
        "<sheetData>"
        + header_row
        + "".join(grid_rows)
        + "</sheetData></worksheet>"
    )

    ledger_rows = "".join(
        f'<row r="{3 + i}" spans="1:2"><c r="A{3 + i}" t="s"><v>{index[account]}</v></c>'
        f'<c r="B{3 + i}"><v>0</v></c></row>'
        for i, account in enumerate(accounts)
    )
    sheet3 = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="A1:B{2 + len(accounts)}"/>'
        "<sheetData>"
        f'<row r="1" spans="1:2"><c r="A1" t="s"><v>{index["Ledger"]}</v></c></row>'
        f'<row r="2" spans="1:2"><c r="A2" t="s"><v>{index["Account"]}</v></c>'
        f'<c r="B2" t="s"><v>{index["Beginning Balance"]}</v></c></row>'
        + ledger_rows
        + "</sheetData></worksheet>"
    )

    shared_strings = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{len(ordered)}" uniqueCount="{len(ordered)}">'
        + "".join(f"<si><t>{s}</t></si>" for s in ordered)
        + "</sst>"
    )

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES_XML)
        zf.writestr("_rels/.rels", _ROOT_RELS_XML)
        zf.writestr("xl/workbook.xml", _WORKBOOK_XML)
        zf.writestr("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS_XML)
        zf.writestr("xl/worksheets/sheet1.xml", sheet1)
        zf.writestr("xl/worksheets/sheet2.xml", sheet2)
        zf.writestr("xl/worksheets/sheet3.xml", sheet3)
        zf.writestr("xl/sharedStrings.xml", shared_strings)
        zf.writestr("xl/styles.xml", _STYLES_XML)
        zf.writestr("xl/calcChain.xml", _CALC_CHAIN_XML)
