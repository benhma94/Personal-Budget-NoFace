"""Append rows to the Journal sheet of Personal Budget.xlsx without a full
openpyxl load/save round-trip.

openpyxl cannot preserve charts, tables, threaded comments, rich data, web
extensions, or the `x14:dataValidations` extension that drives the
Debit/Credit dropdowns — see budget_dashboard/workbook.py for the read-only
workaround this project already uses. This module instead edits only the
worksheet XML and shared-strings table inside the `.xlsx` zip, byte-copying
every other part untouched.

The workbook's AutoFilter is deliberately left alone: its range
(`A2:E5521` as of this writing) already stops well short of the sheet's
real data, so appended rows always land outside it and are never affected
by a stale filter. Leaving it means we never touch the user's current
Excel view state.
"""
from __future__ import annotations

import re
import shutil
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Sequence

from openpyxl import load_workbook

from .accounts import read_ledger_accounts

_EXCEL_EPOCH = date(1899, 12, 30)  # 1900 date system, including the Lotus leap-year bug
_DATE_STYLE = "4"
_AMOUNT_STYLE = "137"


class WorkbookLockedError(RuntimeError):
    """Raised when the workbook appears to be open in Excel."""


class UnknownAccountError(ValueError):
    """Raised when a Debit/Credit name is not present in Ledger!A:A."""


class VerificationError(RuntimeError):
    """Raised when the written workbook does not read back as expected."""


@dataclass(frozen=True)
class JournalLine:
    """One row to append to the Journal sheet.

    `components` holds the individual amounts being summed (as your existing
    rows do, e.g. ``11.58+8.64+97.33``). A single-element list is written as
    a plain value, matching the convention already used in the sheet.
    """

    posting_date: date
    debit: str
    credit: str
    components: Sequence[float]
    note: str

    @property
    def amount(self) -> float:
        return round(sum(self.components), 10)


@dataclass(frozen=True)
class BatchResult:
    first_row: int
    last_row: int
    backup_path: Path


def _excel_serial(d: date) -> int:
    return (d - _EXCEL_EPOCH).days


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _resolve_journal_part(zf: zipfile.ZipFile) -> str:
    """Find the worksheet XML part for the sheet named 'Journal'."""
    workbook_xml = zf.read("xl/workbook.xml").decode("utf-8")
    match = re.search(r'<sheet [^>]*name="Journal"[^>]*/>', workbook_xml)
    if not match:
        raise ValueError("workbook.xml has no sheet named 'Journal'")
    rid_match = re.search(r'r:id="([^"]+)"', match.group(0))
    if not rid_match:
        raise ValueError("Journal <sheet> element has no r:id")
    rid = rid_match.group(1)

    rels_xml = zf.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    rel_match = re.search(rf'<Relationship Id="{re.escape(rid)}"[^>]*/>', rels_xml)
    if not rel_match:
        raise ValueError(f"workbook.xml.rels has no relationship {rid!r}")
    target_match = re.search(r'Target="([^"]+)"', rel_match.group(0))
    if not target_match:
        raise ValueError(f"Relationship {rid!r} has no Target")
    target = target_match.group(1)
    return target if target.startswith("xl/") else f"xl/{target}"


def _parse_shared_strings(xml_text: str) -> tuple[list[str], dict[str, int]]:
    """Return (ordered texts, text -> first index) for every <si> entry."""
    entries = re.findall(r"<si>(.*?)</si>", xml_text, re.S)
    texts: list[str] = []
    index: dict[str, int] = {}
    for i, entry in enumerate(entries):
        # Simple <t>text</t> or <t xml:space="preserve">text</t>; strip any tags defensively.
        inner = re.sub(r"<[^>]+>", "", entry)
        texts.append(inner)
        index.setdefault(inner, i)
    return texts, index


def _shared_string_index(
    text: str,
    index: dict[str, int],
    new_entries: list[str],
    existing_count: int,
) -> int:
    """Return the shared-string index for `text`, adding a new entry if needed."""
    if text in index:
        return index[text]
    # Shared-string indexes address every <si> entry, not the number of unique
    # strings in ``index``.  Keep the immutable pre-append count as the base;
    # ``index`` itself grows each time a new string is discovered.
    position = existing_count + len(new_entries)
    new_entries.append(text)
    index[text] = position
    return position


def _si_xml(text: str) -> str:
    escaped = _xml_escape(text)
    if text != text.strip():
        return f'<si><t xml:space="preserve">{escaped}</t></si>'
    return f"<si><t>{escaped}</t></si>"


def _dimension_end(sheet_xml: str) -> tuple[str, int]:
    match = re.search(r'<dimension ref="[A-Z]+\d+:([A-Z]+)(\d+)"/>', sheet_xml)
    if not match:
        raise ValueError("worksheet is missing a two-cell <dimension> element")
    return match.group(1), int(match.group(2))


def _row_xml(row_number: int, line: JournalLine, debit_idx: int, credit_idx: int, note_idx: int) -> str:
    date_cell = f'<c r="A{row_number}" s="{_DATE_STYLE}"><v>{_excel_serial(line.posting_date)}</v></c>'
    debit_cell = f'<c r="B{row_number}" t="s"><v>{debit_idx}</v></c>'
    credit_cell = f'<c r="C{row_number}" t="s"><v>{credit_idx}</v></c>'
    if len(line.components) > 1:
        formula = "+".join(_format_number(v) for v in line.components)
        amount_cell = (
            f'<c r="D{row_number}" s="{_AMOUNT_STYLE}">'
            f"<f>{_xml_escape(formula)}</f><v>{_format_number(line.amount)}</v></c>"
        )
    else:
        amount_cell = f'<c r="D{row_number}" s="{_AMOUNT_STYLE}"><v>{_format_number(line.amount)}</v></c>'
    note_cell = f'<c r="E{row_number}" t="s"><v>{note_idx}</v></c>'
    return f'<row r="{row_number}" spans="1:5">{date_cell}{debit_cell}{credit_cell}{amount_cell}{note_cell}</row>'


def _format_number(value: float) -> str:
    text = repr(float(value))
    if text.endswith(".0"):
        text = text[:-2]
    return text


def _check_not_locked(workbook_path: Path) -> None:
    lock_file = workbook_path.with_name(f"~${workbook_path.name}")
    if lock_file.exists():
        raise WorkbookLockedError(
            f"{workbook_path.name} appears to be open in Excel (found {lock_file.name}). "
            "Close it and try again."
        )
    try:
        with open(workbook_path, "r+b"):
            pass
    except PermissionError as exc:
        raise WorkbookLockedError(
            f"{workbook_path.name} could not be opened for writing. "
            "Close it in Excel and try again."
        ) from exc


def _known_accounts(workbook_path: Path) -> set[str]:
    return set(read_ledger_accounts(workbook_path))


def append_journal_rows(
    workbook_path: str | Path,
    lines: Sequence[JournalLine],
    *,
    backup_dir: str | Path | None = None,
) -> BatchResult:
    """Append `lines` to the Journal sheet, backing up the workbook first.

    Validates every Debit/Credit name against Ledger!A:A before writing
    anything, refuses to run if the workbook looks locked by Excel, and
    verifies the appended rows read back correctly afterward.
    """
    if not lines:
        raise ValueError("no lines to append")

    workbook_path = Path(workbook_path)
    if not workbook_path.is_file():
        raise FileNotFoundError(f"workbook not found: {workbook_path}")

    _check_not_locked(workbook_path)

    known_accounts = _known_accounts(workbook_path)
    unknown = sorted(
        {line.debit for line in lines if line.debit not in known_accounts}
        | {line.credit for line in lines if line.credit not in known_accounts}
    )
    if unknown:
        raise UnknownAccountError(
            f"not present in Ledger!A:A: {', '.join(unknown)}"
        )

    backup_dir = Path(backup_dir) if backup_dir else workbook_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_path = backup_dir / f"{workbook_path.stem}.{stamp}{workbook_path.suffix}"
    shutil.copy2(workbook_path, backup_path)

    try:
        first_row, last_row = _write(workbook_path, lines)
    except Exception:
        shutil.copy2(backup_path, workbook_path)
        raise

    try:
        _verify(workbook_path, lines, first_row)
    except Exception:
        shutil.copy2(backup_path, workbook_path)
        raise VerificationError(
            "appended rows did not read back correctly; workbook restored from backup"
        ) from None

    return BatchResult(first_row=first_row, last_row=last_row, backup_path=backup_path)


def _write(workbook_path: Path, lines: Sequence[JournalLine]) -> tuple[int, int]:
    tmp_path = workbook_path.with_suffix(workbook_path.suffix + ".tmp")

    with zipfile.ZipFile(workbook_path, "r") as src:
        journal_part = _resolve_journal_part(src)
        sheet_xml = src.read(journal_part).decode("utf-8")
        shared_strings_xml = src.read("xl/sharedStrings.xml").decode("utf-8")
        content_types_xml = src.read("[Content_Types].xml").decode("utf-8")
        rels_xml = src.read("xl/_rels/workbook.xml.rels").decode("utf-8")
        workbook_xml = src.read("xl/workbook.xml").decode("utf-8")

        col_letter, last_existing_row = _dimension_end(sheet_xml)
        row_numbers = [int(n) for n in re.findall(r'<row r="(\d+)"', sheet_xml)]
        last_row_in_data = max(row_numbers) if row_numbers else last_existing_row
        first_new_row = last_row_in_data + 1

        existing_strings, string_index = _parse_shared_strings(shared_strings_xml)
        existing_string_count = len(existing_strings)
        new_strings: list[str] = []
        new_rows_xml = []
        for offset, line in enumerate(lines):
            row_number = first_new_row + offset
            debit_idx = _shared_string_index(
                line.debit, string_index, new_strings, existing_string_count
            )
            credit_idx = _shared_string_index(
                line.credit, string_index, new_strings, existing_string_count
            )
            note_idx = _shared_string_index(
                line.note, string_index, new_strings, existing_string_count
            )
            new_rows_xml.append(_row_xml(row_number, line, debit_idx, credit_idx, note_idx))
        last_new_row = first_new_row + len(lines) - 1

        updated_sheet = sheet_xml.replace(
            "</sheetData>", "".join(new_rows_xml) + "</sheetData>", 1
        )
        updated_sheet = re.sub(
            r'<dimension ref="[A-Z]+\d+:[A-Z]+\d+"/>',
            f'<dimension ref="A1:{col_letter}{last_new_row}"/>',
            updated_sheet,
            count=1,
        )

        if new_strings:
            additions = "".join(_si_xml(text) for text in new_strings)
            updated_shared = shared_strings_xml.replace("</sst>", additions + "</sst>", 1)
            total_count_added = len(lines) * 3  # each row references 3 shared strings
            updated_shared = re.sub(
                r'count="(\d+)"',
                lambda m: f'count="{int(m.group(1)) + total_count_added}"',
                updated_shared,
                count=1,
            )
            updated_shared = re.sub(
                r'uniqueCount="(\d+)"',
                lambda m: f'uniqueCount="{int(m.group(1)) + len(new_strings)}"',
                updated_shared,
                count=1,
            )
        else:
            total_count_added = len(lines) * 3
            updated_shared = re.sub(
                r'count="(\d+)"',
                lambda m: f'count="{int(m.group(1)) + total_count_added}"',
                shared_strings_xml,
                count=1,
            )

        updated_content_types, updated_rels, updated_workbook = _invalidate_cached_formulas(
            content_types_xml, rels_xml, workbook_xml
        )

        replacements = {
            journal_part: updated_sheet,
            "xl/sharedStrings.xml": updated_shared,
            "xl/_rels/workbook.xml.rels": updated_rels,
            "[Content_Types].xml": updated_content_types,
            "xl/workbook.xml": updated_workbook,
        }
        _write_zip_with_replacements(src, tmp_path, replacements)

    tmp_path.replace(workbook_path)
    return first_new_row, last_new_row


def _verify(workbook_path: Path, lines: Sequence[JournalLine], first_row: int) -> None:
    wb = load_workbook(workbook_path, read_only=True, data_only=True)
    try:
        journal = wb["Journal"]
        rows = list(
            journal.iter_rows(
                min_row=first_row, max_row=first_row + len(lines) - 1, max_col=5, values_only=True
            )
        )
    finally:
        wb.close()
    if len(rows) != len(lines):
        raise VerificationError(f"expected {len(lines)} rows, read back {len(rows)}")
    for line, row in zip(lines, rows):
        got_date, debit, credit, amount, note = row
        expected_date = datetime(line.posting_date.year, line.posting_date.month, line.posting_date.day)
        if got_date != expected_date:
            raise VerificationError(f"date mismatch: expected {expected_date}, got {got_date}")
        if debit != line.debit or credit != line.credit:
            raise VerificationError(f"debit/credit mismatch: expected {line.debit}/{line.credit}, got {debit}/{credit}")
        if amount is None or round(float(amount), 2) != round(line.amount, 2):
            raise VerificationError(f"amount mismatch: expected {line.amount}, got {amount}")
        if note != line.note:
            raise VerificationError(f"note mismatch: expected {line.note!r}, got {note!r}")


def _invalidate_cached_formulas(
    content_types_xml: str, rels_xml: str, workbook_xml: str
) -> tuple[str, str, str]:
    """Drop calcChain.xml's registration and force Excel to recompute every
    formula on open -- a write that changes cell values makes any cached
    calc chain stale, whether the write appended rows or edited one."""
    updated_content_types = content_types_xml
    ct_match = re.search(r'<Override[^>]*calcChain[^>]*/>', content_types_xml)
    if ct_match:
        updated_content_types = content_types_xml.replace(ct_match.group(0), "", 1)

    updated_rels = rels_xml
    rel_match = re.search(r'<Relationship [^>]*Target="calcChain\.xml"[^>]*/>', rels_xml)
    if rel_match:
        updated_rels = rels_xml.replace(rel_match.group(0), "", 1)

    calc_pr_match = re.search(r"<calcPr[^>]*/>", workbook_xml)
    if calc_pr_match and 'fullCalcOnLoad="1"' not in calc_pr_match.group(0):
        patched = calc_pr_match.group(0)[:-2] + ' fullCalcOnLoad="1"/>'
        updated_workbook = workbook_xml.replace(calc_pr_match.group(0), patched, 1)
    elif calc_pr_match:
        updated_workbook = workbook_xml
    else:
        updated_workbook = workbook_xml.replace(
            "</workbook>", '<calcPr fullCalcOnLoad="1"/></workbook>', 1
        )

    return updated_content_types, updated_rels, updated_workbook


def _write_zip_with_replacements(
    src: zipfile.ZipFile, tmp_path: Path, replacements: dict[str, str]
) -> None:
    """Rewrite the workbook's zip at `tmp_path`, substituting `replacements`
    by part name and byte-copying every other part untouched. Always drops
    calcChain.xml since every caller uses this only after a value-changing
    write."""
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            if item.filename == "xl/calcChain.xml":
                continue
            data = replacements.get(item.filename)
            if data is not None:
                dst.writestr(item, data.encode("utf-8"))
            else:
                dst.writestr(item, src.read(item.filename))


def _write_update(workbook_path: Path, row_number: int, line: JournalLine) -> None:
    tmp_path = workbook_path.with_suffix(workbook_path.suffix + ".tmp")

    with zipfile.ZipFile(workbook_path, "r") as src:
        journal_part = _resolve_journal_part(src)
        sheet_xml = src.read(journal_part).decode("utf-8")
        shared_strings_xml = src.read("xl/sharedStrings.xml").decode("utf-8")
        content_types_xml = src.read("[Content_Types].xml").decode("utf-8")
        rels_xml = src.read("xl/_rels/workbook.xml.rels").decode("utf-8")
        workbook_xml = src.read("xl/workbook.xml").decode("utf-8")

        row_pattern = re.compile(
            rf'<row r="{row_number}"(?:[^>]*/>|[^>]*>.*?</row>)', re.S
        )
        if not row_pattern.search(sheet_xml):
            raise ValueError(f"row {row_number} not found in the Journal sheet")

        existing_strings, string_index = _parse_shared_strings(shared_strings_xml)
        existing_string_count = len(existing_strings)
        new_strings: list[str] = []
        debit_idx = _shared_string_index(line.debit, string_index, new_strings, existing_string_count)
        credit_idx = _shared_string_index(line.credit, string_index, new_strings, existing_string_count)
        note_idx = _shared_string_index(line.note, string_index, new_strings, existing_string_count)
        new_row_xml = _row_xml(row_number, line, debit_idx, credit_idx, note_idx)
        # A lambda replacement (not a plain string) avoids re.sub treating any
        # backslash the user typed into debit/credit/note as a backreference.
        updated_sheet = row_pattern.sub(lambda _m: new_row_xml, sheet_xml, count=1)

        if new_strings:
            additions = "".join(_si_xml(text) for text in new_strings)
            updated_shared = shared_strings_xml.replace("</sst>", additions + "</sst>", 1)
            updated_shared = re.sub(
                r'uniqueCount="(\d+)"',
                lambda m: f'uniqueCount="{int(m.group(1)) + len(new_strings)}"',
                updated_shared,
                count=1,
            )
        else:
            updated_shared = shared_strings_xml

        updated_content_types, updated_rels, updated_workbook = _invalidate_cached_formulas(
            content_types_xml, rels_xml, workbook_xml
        )

        replacements = {
            journal_part: updated_sheet,
            "xl/sharedStrings.xml": updated_shared,
            "xl/_rels/workbook.xml.rels": updated_rels,
            "[Content_Types].xml": updated_content_types,
            "xl/workbook.xml": updated_workbook,
        }
        _write_zip_with_replacements(src, tmp_path, replacements)

    tmp_path.replace(workbook_path)


def update_journal_row(
    workbook_path: str | Path,
    row_number: int,
    line: JournalLine,
    *,
    backup_dir: str | Path | None = None,
) -> Path:
    """Overwrite an existing Journal row's cells in place -- no row is
    inserted or removed, so no other row's position changes. Reuses
    append_journal_rows's safety net (lock check, account validation,
    backup, verify-and-rollback) but targets row_number instead of the end
    of the sheet, and always writes `line`'s amount as a plain value even
    if the row previously held a multi-component formula.
    """
    workbook_path = Path(workbook_path)
    if not workbook_path.is_file():
        raise FileNotFoundError(f"workbook not found: {workbook_path}")

    _check_not_locked(workbook_path)

    known_accounts = _known_accounts(workbook_path)
    unknown = sorted({line.debit, line.credit} - known_accounts)
    if unknown:
        raise UnknownAccountError(f"not present in Ledger!A:A: {', '.join(unknown)}")

    with zipfile.ZipFile(workbook_path) as zf:
        journal_part = _resolve_journal_part(zf)
        sheet_xml = zf.read(journal_part).decode("utf-8")
    if not re.search(rf'<row r="{row_number}"(?:[^>]*/>|[^>]*>)', sheet_xml):
        raise ValueError(f"row {row_number} not found in the Journal sheet")

    backup_dir = Path(backup_dir) if backup_dir else workbook_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_path = backup_dir / f"{workbook_path.stem}.{stamp}{workbook_path.suffix}"
    shutil.copy2(workbook_path, backup_path)

    try:
        _write_update(workbook_path, row_number, line)
    except Exception:
        shutil.copy2(backup_path, workbook_path)
        raise

    try:
        _verify(workbook_path, [line], row_number)
    except Exception:
        shutil.copy2(backup_path, workbook_path)
        raise VerificationError(
            "updated row did not read back correctly; workbook restored from backup"
        ) from None

    return backup_path
