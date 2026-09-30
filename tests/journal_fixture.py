"""Shared helper: build a minimal but structurally faithful Personal
Budget.xlsx (Journal + Ledger, shared strings, styles, calcChain) for
journal_entry tests, so tests never depend on the user's real, gitignored
workbook.

openpyxl-saved workbooks use inline strings by default and have no
sharedStrings.xml part, so they can't stand in for the real file's shape.
This hand-builds the zip instead.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

# Style index 4 (dates) needs numFmtId 14 (built-in short date) or openpyxl
# reads the serial back as a plain number, not a datetime.
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
<sheet name="Ledger" sheetId="2" r:id="rId2"/>
</sheets>
<calcPr calcId="191029"/>
</workbook>"""

_WORKBOOK_RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/calcChain" Target="calcChain.xml"/>
<Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

_CALC_CHAIN_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<calcChain xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><c r="D3" i="1"/></calcChain>"""


def build_fixture_workbook(
    path: Path, accounts: list[str], opening_balances: dict[str, float] | None = None
) -> None:
    """Write a minimal workbook to `path` with `accounts` as Ledger!A3:A{n}."""
    opening_balances = opening_balances or {}
    strings = ["Journal", "Date", "Debit", "Credit", "Amount", "Note", "Ledger", "Account",
               "Beginning Balance", *accounts]
    index = {s: i for i, s in enumerate(strings)}

    sheet1 = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheetPr/><dimension ref="A1:E2"/>'
        "<sheetData>"
        f'<row r="1" spans="1:5"><c r="A1" t="s"><v>{index["Journal"]}</v></c></row>'
        f'<row r="2" spans="1:5"><c r="A2" t="s"><v>{index["Date"]}</v></c>'
        f'<c r="B2" t="s"><v>{index["Debit"]}</v></c><c r="C2" t="s"><v>{index["Credit"]}</v></c>'
        f'<c r="D2" t="s"><v>{index["Amount"]}</v></c><c r="E2" t="s"><v>{index["Note"]}</v></c></row>'
        "</sheetData>"
        '<extLst><ext uri="{CCE6A557-97BC-4b89-ADB6-D9C93CAAB3DF}" xmlns:x14="http://schemas.microsoft.com/office/spreadsheetml/2009/9/main">'
        '<x14:dataValidations count="1" xmlns:xm="http://schemas.microsoft.com/office/excel/2006/main">'
        '<x14:dataValidation type="list" allowBlank="1"><x14:formula1><xm:f>Ledger!$A:$A</xm:f></x14:formula1>'
        "<xm:sqref>B1:C1048576</xm:sqref></x14:dataValidation></x14:dataValidations></ext></extLst>"
        "</worksheet>"
    )

    ledger_rows = "".join(
        f'<row r="{3 + i}" spans="1:2"><c r="A{3 + i}" t="s"><v>{index[account]}</v></c>'
        f'<c r="B{3 + i}"><v>{opening_balances.get(account, 0)}</v></c></row>'
        for i, account in enumerate(accounts)
    )
    sheet2 = (
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
        f'count="{len(strings)}" uniqueCount="{len(strings)}">'
        + "".join(f"<si><t>{s}</t></si>" for s in strings)
        + "</sst>"
    )

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES_XML)
        zf.writestr("_rels/.rels", _ROOT_RELS_XML)
        zf.writestr("xl/workbook.xml", _WORKBOOK_XML)
        zf.writestr("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS_XML)
        zf.writestr("xl/worksheets/sheet1.xml", sheet1)
        zf.writestr("xl/worksheets/sheet2.xml", sheet2)
        zf.writestr("xl/sharedStrings.xml", shared_strings)
        zf.writestr("xl/styles.xml", _STYLES_XML)
        zf.writestr("xl/calcChain.xml", _CALC_CHAIN_XML)
