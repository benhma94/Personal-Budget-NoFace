from __future__ import annotations

import zipfile
from datetime import date
from pathlib import Path

from journal_entry.xlsx_append import JournalLine, append_journal_rows
from journal_entry.xlsx_read import read_journal_rows

from .journal_fixture import build_fixture_workbook


def _workbook(tmp_path: Path) -> Path:
    path = tmp_path / "Personal Budget.xlsx"
    build_fixture_workbook(path, ["Cash", "Salary", "Pay", "Food", "Groceries"])
    return path


def test_read_journal_rows_returns_plain_value_row(tmp_path):
    workbook = _workbook(tmp_path)
    append_journal_rows(workbook, [JournalLine(date(2026, 8, 30), "Food", "Cash", [42.0], "Snack")])

    rows = read_journal_rows(workbook, 3, 3)

    assert len(rows) == 1
    row = rows[0]
    assert row["row_number"] == 3
    assert row["posting_date"] == "2026-08-30"
    assert row["debit"] == "Food"
    assert row["credit"] == "Cash"
    assert row["amount"] == 42.0
    assert row["note"] == "Snack"
    assert row["is_formula"] is False


def test_read_journal_rows_detects_formula(tmp_path):
    workbook = _workbook(tmp_path)
    append_journal_rows(
        workbook, [JournalLine(date(2026, 8, 30), "Food", "Cash", [12.5, 8.0], "Groceries")]
    )

    rows = read_journal_rows(workbook, 3, 3)

    assert rows[0]["amount"] == 20.5
    assert rows[0]["is_formula"] is True


def test_read_journal_rows_respects_row_range(tmp_path):
    workbook = _workbook(tmp_path)
    append_journal_rows(workbook, [
        JournalLine(date(2026, 8, 30), "Food", "Cash", [5.0], "first"),
        JournalLine(date(2026, 8, 31), "Cash", "Salary", [10.0], "second"),
    ])

    both = read_journal_rows(workbook, 3, 4)
    assert [r["note"] for r in both] == ["first", "second"]

    only_first = read_journal_rows(workbook, 3, 3)
    assert [r["note"] for r in only_first] == ["first"]


def test_read_journal_rows_handles_text_valued_date_and_amount(tmp_path):
    workbook = _workbook(tmp_path)
    append_journal_rows(workbook, [JournalLine(date(2026, 8, 30), "Food", "Cash", [42.0], "Snack")])
    with zipfile.ZipFile(workbook) as zf:
        sheet = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
        shared = zf.read("xl/sharedStrings.xml").decode("utf-8")
    # Simulate a hand-typed, non-date value in the Date cell and a
    # hand-typed, non-numeric value in the Amount cell -- exactly what a
    # user might type directly into Excel by mistake.
    new_strings = "<si><t>not a date</t></si><si><t>not a number</t></si>"
    unique_before = int(shared.split('uniqueCount="')[1].split('"')[0])
    patched_shared = shared.replace("</sst>", new_strings + "</sst>", 1)
    patched_shared = patched_shared.replace(
        f'uniqueCount="{unique_before}"', f'uniqueCount="{unique_before + 2}"', 1
    )
    patched_sheet = sheet.replace(
        '<c r="A3" s="4"><v>46264</v></c>', f'<c r="A3" t="s"><v>{unique_before}</v></c>', 1
    )
    patched_sheet = patched_sheet.replace(
        '<c r="D3" s="137"><v>42</v></c>', f'<c r="D3" t="s"><v>{unique_before + 1}</v></c>', 1
    )
    assert patched_sheet != sheet  # both replacements must have actually matched

    with zipfile.ZipFile(workbook, "r") as src:
        items = {item.filename: src.read(item.filename) for item in src.infolist()}
    items["xl/worksheets/sheet1.xml"] = patched_sheet.encode("utf-8")
    items["xl/sharedStrings.xml"] = patched_shared.encode("utf-8")
    with zipfile.ZipFile(workbook, "w", zipfile.ZIP_DEFLATED) as dst:
        for name, data in items.items():
            dst.writestr(name, data)

    rows = read_journal_rows(workbook, 3, 3)  # must not raise

    assert rows[0]["posting_date"] == "not a date"
    assert rows[0]["amount"] is None
