"""Read and write the Budget sheet's planning grid
(Budget!A13:<last-col><last-row> in data/Personal Budget.xlsx) for the
finance hub's Budget-plan tab.

Reuses budget_dashboard.workbook's _read_journal/_read_budget_grid so
"actual" and "budgeted" numbers are computed identically here and on the
Report tab, and reuses journal_entry.xlsx_append's lock-check,
backup/restore, and raw-XML zip-rewrite helpers so the Budget writer
follows the exact same safety discipline as the Journal writer --
openpyxl.save() cannot round-trip this workbook's data-validation
dropdowns and other constructs, so both writers edit worksheet XML
directly instead of doing a full load/save.
"""
from __future__ import annotations

import calendar
import math
import posixpath
import re
import shutil
import threading
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence

from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string, get_column_letter

from budget_dashboard.workbook import (
    EXPENSE_CATEGORIES,
    INCOME_CATEGORIES,
    _month_key,
    _read_budget_grid,
    _read_journal,
)
from journal_entry.xlsx_append import (
    VerificationError,
    WorkbookLockedError,
    _check_not_locked,
    _excel_serial,
    _format_number,
    _invalidate_cached_formulas,
    _write_zip_with_replacements,
)

_FIRST_CATEGORY_ROW = 16
_LAST_CATEGORY_ROW = 47
_MONTH_HEADER_ROW = 13


# --------------------------------------------------------------------- read --
def _trailing_window(latest_period: str, months: int = 12) -> list[str]:
    year, month = map(int, latest_period.split("-"))
    result = []
    for _ in range(months):
        result.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(result))


def _trend_for_category(
    category: str,
    actuals: dict[tuple[str, str], float],
    raw_budget_by_month: dict[str, dict[str, float | None]],
    latest_period: str | None,
) -> float | None:
    """Average(actual - budget) over the trailing 12 months ending at
    latest_period, restricted to months where a budget entry exists.
    Raw signed values throughout, so no per-kind sign-flipping is needed."""
    if latest_period is None:
        return None
    diffs = []
    for month in _trailing_window(latest_period):
        budget = raw_budget_by_month.get(month, {}).get(category)
        if budget is None:
            continue
        diffs.append(actuals.get((month, category), 0.0) - budget)
    return sum(diffs) / len(diffs) if diffs else None


def _average_budget_for_category(
    category: str,
    raw_budget_by_month: dict[str, dict[str, float | None]],
    latest_period: str | None,
) -> float | None:
    if latest_period is None:
        return None
    values = [
        raw_budget_by_month.get(month, {}).get(category)
        for month in _trailing_window(latest_period)
    ]
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _load_budget_plan_source(path: Path) -> Any:
    """Load the (actuals, raw_budget_by_month) pair read_budget_plan needs.

    This is the expensive part -- a full Journal parse -- so it's the part
    BudgetPlanSourceCache caches.
    """
    workbook = load_workbook(path, read_only=True, data_only=True)
    missing = {"Journal", "Budget"}.difference(workbook.sheetnames)
    if missing:
        workbook.close()
        raise ValueError(f"Workbook is missing required sheet(s): {', '.join(sorted(missing))}")
    try:
        _check_grid_layout_has_no_duplicates(workbook["Budget"])
        journal_data = _read_journal(workbook)
        return journal_data.actuals, _read_budget_grid(workbook)
    finally:
        workbook.close()


class BudgetPlanSourceCache:
    """Caches read_budget_plan's expensive Journal/Budget-grid parse, keyed
    on the workbook's mtime -- the same pattern budget_api.BudgetPayloadCache
    already uses for the sibling Report tab, so repeated payload GETs while
    the Budget-plan tab is open don't re-parse the whole Journal each time.
    """

    def __init__(self, workbook_path: str | Path):
        self._workbook_path = Path(workbook_path)
        self._lock = threading.Lock()
        self._mtime: float | None = None
        self._source: Any = None

    def invalidate(self) -> None:
        with self._lock:
            self._mtime = None

    def get(self) -> Any:
        with self._lock:
            mtime = self._workbook_path.stat().st_mtime
            if self._source is None or mtime != self._mtime:
                self._source = _load_budget_plan_source(self._workbook_path)
                self._mtime = mtime
            return self._source


def read_budget_plan(
    workbook_path: str | Path,
    year: int,
    *,
    cache: BudgetPlanSourceCache | None = None,
) -> dict[str, Any]:
    """Every category/month cell for `year`, plus a trend-adjusted suggestion.

    Reuses _read_journal/_read_budget_grid so "actual" and "budgeted"
    numbers can never drift from what the Report tab shows for the same
    cells. Pass `cache` (a BudgetPlanSourceCache) to avoid re-parsing the
    whole Journal on every call, e.g. for repeated payload requests from a
    long-lived server process.
    """
    if isinstance(year, bool) or not isinstance(year, int) or not 1900 <= year <= 9999:
        raise ValueError("year must be an integer between 1900 and 9999")

    path = Path(workbook_path)
    if not path.is_file():
        raise FileNotFoundError(f"Budget workbook not found: {path}")

    actuals, raw_budget_by_month = cache.get() if cache is not None else _load_budget_plan_source(path)

    categories = [*INCOME_CATEGORIES, *EXPENSE_CATEGORIES]
    months = [f"{year:04d}-{m:02d}" for m in range(1, 13)]
    actual_periods = {period for period, _category in actuals}
    latest_actual_period = max(actual_periods) if actual_periods else None
    latest_saved_month = max(
        (month for month, row in raw_budget_by_month.items() if any(v is not None for v in row.values())),
        default=None,
    )

    cells: dict[str, dict[str, dict[str, Any]]] = {}
    for category in categories:
        trend = _trend_for_category(category, actuals, raw_budget_by_month, latest_actual_period)
        fallback_avg = _average_budget_for_category(category, raw_budget_by_month, latest_actual_period)
        month_cells: dict[str, dict[str, Any]] = {}
        for month in months:
            prior_month = f"{year - 1:04d}-{month[5:]}"
            saved = raw_budget_by_month.get(month, {}).get(category)
            prior_year_budget = raw_budget_by_month.get(prior_month, {}).get(category)
            prior_year_actual = actuals.get((prior_month, category))
            if prior_year_budget is not None:
                suggested = prior_year_budget + (trend if trend is not None else 0.0)
            else:
                suggested = fallback_avg
            month_cells[month] = {
                "saved": saved,
                "suggested": suggested,
                "prior_year_budget": prior_year_budget,
                "prior_year_actual": prior_year_actual,
            }
        cells[category] = month_cells

    return {
        "workbook": path.name,
        "year": year,
        "months": months,
        "income_categories": list(INCOME_CATEGORIES),
        "expense_categories": list(EXPENSE_CATEGORIES),
        "latest_saved_month": latest_saved_month,
        "cells": cells,
    }


# -------------------------------------------------------------------- write --
def _resolve_sheet_part(zf: zipfile.ZipFile, sheet_name: str) -> str:
    """Find the worksheet XML part for ``sheet_name`` from workbook rels."""
    workbook_xml = zf.read("xl/workbook.xml").decode("utf-8")
    sheet_match = re.search(
        rf"<sheet\b(?=[^>]*\bname=\"{re.escape(sheet_name)}\")(?=[^>]*\br:id=\"([^\"]+)\")[^>]*/?>",
        workbook_xml,
    )
    if not sheet_match:
        raise ValueError(f"workbook.xml has no sheet named {sheet_name!r}")
    rid = sheet_match.group(1)

    rels_xml = zf.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    rel_match = re.search(
        rf"<Relationship\b(?=[^>]*\bId=\"{re.escape(rid)}\")([^>]*)/?>",
        rels_xml,
    )
    if not rel_match:
        raise ValueError(f"workbook.xml.rels has no relationship {rid!r}")
    target_match = re.search(r'\bTarget="([^"]+)"', rel_match.group(0))
    if not target_match:
        raise ValueError(f"Relationship {rid!r} has no Target")
    target = target_match.group(1)
    # Relationship targets are relative to /xl/. Normalize both the usual
    # ``worksheets/sheet2.xml`` form and less common absolute targets.
    if target.startswith("/"):
        part = target.lstrip("/")
    else:
        part = posixpath.normpath(posixpath.join("xl", target))
    if not part.startswith("xl/"):
        raise ValueError(f"Relationship {rid!r} points outside the xl package")
    return part


def _check_grid_layout_has_no_duplicates(budget_sheet: Any) -> None:
    """Raise the same error _read_grid_layout would raise for a malformed
    sheet, so the read path (payload GET) fails the same way the write path
    already does instead of silently loading a mismatched view."""
    header_row = next(
        budget_sheet.iter_rows(
            min_row=_MONTH_HEADER_ROW, max_row=_MONTH_HEADER_ROW, values_only=True
        ),
        (),
    )
    seen_months: set[str] = set()
    for value in header_row:
        if isinstance(value, (date, datetime)):
            month = _month_key(value)
            if month in seen_months:
                raise ValueError(f"Budget sheet has duplicate month header {month}")
            seen_months.add(month)

    seen_categories: set[str] = set()
    for row in budget_sheet.iter_rows(
        min_row=_FIRST_CATEGORY_ROW, max_row=_LAST_CATEGORY_ROW, max_col=1, values_only=True
    ):
        if row and isinstance(row[0], str) and row[0].strip():
            category = row[0].strip()
            if category in seen_categories:
                raise ValueError(f"Budget sheet has duplicate category {category!r}")
            seen_categories.add(category)


def _read_grid_layout(workbook_path: Path) -> tuple[dict[str, int], dict[str, int]]:
    """Return ``(month -> column, category -> row)`` from the Budget sheet."""
    wb = load_workbook(workbook_path, read_only=True, data_only=True)
    try:
        if "Budget" not in wb.sheetnames:
            raise ValueError("Workbook is missing required sheet(s): Budget")
        budget_sheet = wb["Budget"]
        header_row = next(
            budget_sheet.iter_rows(
                min_row=_MONTH_HEADER_ROW,
                max_row=_MONTH_HEADER_ROW,
                values_only=True,
            ),
            (),
        )
        month_columns: dict[str, int] = {}
        for index, value in enumerate(header_row):
            if isinstance(value, (date, datetime)):
                month = _month_key(value)
                if month in month_columns:
                    raise ValueError(f"Budget sheet has duplicate month header {month}")
                month_columns[month] = index + 1

        category_rows: dict[str, int] = {}
        for row_number, row in enumerate(
            budget_sheet.iter_rows(
                min_row=_FIRST_CATEGORY_ROW,
                max_row=_LAST_CATEGORY_ROW,
                max_col=1,
                values_only=True,
            ),
            start=_FIRST_CATEGORY_ROW,
        ):
            if row and isinstance(row[0], str) and row[0].strip():
                category = row[0].strip()
                if category in category_rows:
                    raise ValueError(f"Budget sheet has duplicate category {category!r}")
                category_rows[category] = row_number
    finally:
        wb.close()
    return month_columns, category_rows


def _next_month(month_key: str) -> str:
    year, month = map(int, month_key.split("-"))
    if month == 12:
        if year == 9999:
            raise ValueError("cannot create a month after 9999-12")
        year, month = year + 1, 1
    else:
        month += 1
    return f"{year:04d}-{month:02d}"


def _months_from(start_month: str, end_month: str) -> list[str]:
    months = [start_month]
    while months[-1] < end_month:
        months.append(_next_month(months[-1]))
    return months


def _month_end(month_key: str) -> date:
    year, month = map(int, month_key.split("-"))
    return date(year, month, calendar.monthrange(year, month)[1])


def _row_xml_block(sheet_xml: str, row_number: int) -> str | None:
    pattern = re.compile(
        rf'<row\b(?=[^>]*\br="{row_number}"(?:\s|/?>))\s*(?:[^>]*?/>|[^>]*?>.*?</row>)',
        re.S,
    )
    match = pattern.search(sheet_xml)
    return match.group(0) if match else None


def _cell_xml_blocks(row_xml: str) -> list[re.Match[str]]:
    return list(
        re.finditer(
            r'<c\b(?=[^>]*\br="([A-Z]+)\d+")\s*(?:[^>]*?/>|[^>]*?>.*?</c>)',
            row_xml,
            re.S,
        )
    )


def _cell_xml_block(sheet_xml: str, ref: str) -> re.Match[str] | None:
    return re.search(
        rf'<c\b(?=[^>]*\br="{re.escape(ref)}"(?:\s|/?>))\s*(?:[^>]*?/>|[^>]*?>.*?</c>)',
        sheet_xml,
        re.S,
    )


def _cell_has_value(cell_xml: str) -> bool:
    return bool(re.search(r"<(?:v|f)\b[^>]*>\s*[^<]+\s*</(?:v|f)>", cell_xml))


def _populated_grid_columns(sheet_xml: str) -> set[int]:
    """Columns containing data/formulas in the planner's header/grid rows."""
    populated: set[int] = set()
    for row_number in range(_MONTH_HEADER_ROW, _LAST_CATEGORY_ROW + 1):
        row_xml = _row_xml_block(sheet_xml, row_number)
        if row_xml is None:
            continue
        for cell_match in _cell_xml_blocks(row_xml):
            if _cell_has_value(cell_match.group(0)):
                col_letter = re.match(r"([A-Z]+)", cell_match.group(1)).group(1)
                populated.add(column_index_from_string(col_letter))
    return populated


def _last_cell_style(row_xml: str | None) -> str | None:
    if not row_xml:
        return None
    cells = _cell_xml_blocks(row_xml)
    if not cells:
        return None
    style_match = re.search(r'\bs="([^"]+)"', cells[-1].group(0))
    return style_match.group(1) if style_match else None


def _cell_xml(ref: str, style: str | None, value_text: str | None) -> str:
    style_attr = f' s="{style}"' if style else ""
    if value_text is None:
        return f'<c r="{ref}"{style_attr}/>'
    return f'<c r="{ref}"{style_attr}><v>{value_text}</v></c>'


def _append_cell_to_row(sheet_xml: str, row_number: int, new_cell_xml: str) -> str:
    """Append a cell to an existing row, preserving all row attributes."""
    pattern = re.compile(
        rf'<row\b(?=[^>]*\br="{row_number}"(?:\s|/?>))\s*(?:[^>]*?/>|[^>]*?>.*?</row>)',
        re.S,
    )
    match = pattern.search(sheet_xml)
    if not match:
        raise ValueError(
            f"row {row_number} has no <row> element in the Budget sheet"
        )
    row_block = match.group(0)
    if re.search(r"/\s*>$", row_block):
        new_row = re.sub(r"/\s*>$", ">" + new_cell_xml + "</row>", row_block, count=1)
    else:
        new_row = row_block[:-len("</row>")] + new_cell_xml + "</row>"
    return sheet_xml[: match.start()] + new_row + sheet_xml[match.end() :]


def _cell_attrs_without_type(cell_xml: str) -> str:
    """Keep formatting/extension attrs when replacing a cell's value."""
    match = re.search(r"<c\b([^>]*)", cell_xml)
    attrs = match.group(1).rstrip("/") if match else ""
    return re.sub(r'\s+t="[^"]*"', "", attrs)


def _patch_cell(sheet_xml: str, row_number: int, col_letter: str, value: float) -> str:
    ref = f"{col_letter}{row_number}"
    pattern = re.compile(
        rf'<c\b(?=[^>]*\br="{re.escape(ref)}"(?:\s|/?>))\s*(?:[^>]*?/>|[^>]*?>.*?</c>)',
        re.S,
    )
    match = pattern.search(sheet_xml)
    if match:
        attrs = _cell_attrs_without_type(match.group(0))
        new_cell = f'<c{attrs}><v>{_format_number(value)}</v></c>'
        return sheet_xml[: match.start()] + new_cell + sheet_xml[match.end() :]

    # Excel commonly stores formatted blank cells explicitly, but sparse
    # worksheets can omit them. Insert an omitted target in column order.
    row_pattern = re.compile(
        rf'<row\b(?=[^>]*\br="{row_number}"(?:\s|/?>))\s*(?:[^>]*?/>|[^>]*?>.*?</row>)',
        re.S,
    )
    row_match = row_pattern.search(sheet_xml)
    if not row_match:
        raise ValueError(f"row {row_number} not found in the Budget sheet")
    row_block = row_match.group(0)
    style = _last_cell_style(row_block)
    new_cell = _cell_xml(ref, style, _format_number(value))
    target_index = column_index_from_string(col_letter)
    insertion = len(row_block) - len("</row>") if row_block.endswith("</row>") else len(row_block)
    for cell_match in _cell_xml_blocks(row_block):
        cell_col = re.match(r"([A-Z]+)", cell_match.group(1)).group(1)
        if column_index_from_string(cell_col) > target_index:
            insertion = cell_match.start()
            break
    if row_block.endswith("/>"):
        row_block = row_block[:-2] + ">" + new_cell + "</row>"
    else:
        row_block = row_block[:insertion] + new_cell + row_block[insertion:]
    return sheet_xml[: row_match.start()] + row_block + sheet_xml[row_match.end() :]


def _update_dimension(sheet_xml: str, new_last_col_letter: str) -> str:
    pattern = re.compile(r'<dimension\b([^>]*\bref=")([^"]+)("[^>]*/?>)', re.S)
    match = pattern.search(sheet_xml)
    if not match:
        raise ValueError("worksheet is missing a <dimension> element")
    current_ref = match.group(2)
    end_match = re.search(r":([A-Z]+)(\d+)$", current_ref)
    if not end_match:
        raise ValueError("worksheet dimension has no ending cell")
    if column_index_from_string(new_last_col_letter) <= column_index_from_string(end_match.group(1)):
        return sheet_xml
    start = current_ref.split(":", 1)[0]
    new_ref = f"{start}:{new_last_col_letter}{end_match.group(2)}"
    return sheet_xml[: match.start(2)] + new_ref + sheet_xml[match.end(2) :]


def _write_budget_updates(workbook_path: Path, updates: Sequence[tuple[str, str, float]]) -> None:
    month_columns, category_rows = _read_grid_layout(workbook_path)
    unknown_categories = sorted({c for c, _m, _v in updates if c not in category_rows})
    if unknown_categories:
        raise ValueError(
            f"categories not present in the Budget sheet: {', '.join(unknown_categories)}"
        )

    requested_months = {m for _c, m, _v in updates}
    missing_months = sorted(m for m in requested_months if m not in month_columns)
    tmp_path = workbook_path.with_suffix(workbook_path.suffix + ".tmp")
    try:
        with zipfile.ZipFile(workbook_path, "r") as src:
            budget_part = _resolve_sheet_part(src, "Budget")
            sheet_xml = src.read(budget_part).decode("utf-8")
            content_types_xml = src.read("[Content_Types].xml").decode("utf-8")
            rels_xml = src.read("xl/_rels/workbook.xml.rels").decode("utf-8")
            workbook_xml = src.read("xl/workbook.xml").decode("utf-8")

            if missing_months:
                if not month_columns:
                    raise ValueError("Budget sheet has no existing month columns to extend from")
                last_month = max(month_columns)
                if min(missing_months) <= last_month:
                    raise ValueError(
                        f"cannot create a budget column for {min(missing_months)}: "
                        f"it is not after the sheet's last existing month ({last_month})"
                    )
                new_months = _months_from(_next_month(last_month), max(missing_months))
                last_month_col = max(month_columns.values())
                populated_columns = _populated_grid_columns(sheet_xml)
                trailing_populated = [
                    column for column in populated_columns if column > last_month_col
                ]
                next_col_index = last_month_col + 1
                if trailing_populated:
                    # The real workbook ends each year with a formula-driven
                    # annual-total column followed by a visual spacer. New
                    # months must begin after both; otherwise the blank header
                    # on the total column turns the full-year values into the
                    # following January's apparent monthly values.
                    next_col_index = max(trailing_populated) + 2
                if next_col_index + len(new_months) - 1 > 16_384:
                    raise ValueError("cannot extend the Budget sheet beyond Excel's XFD column")
                for month in new_months:
                    col_letter = get_column_letter(next_col_index)
                    header_style = _last_cell_style(_row_xml_block(sheet_xml, _MONTH_HEADER_ROW))
                    header_ref = f"{col_letter}{_MONTH_HEADER_ROW}"
                    existing_header = _cell_xml_block(sheet_xml, header_ref)
                    if existing_header:
                        if _cell_has_value(existing_header.group(0)):
                            raise ValueError(
                                f"preformatted Budget header cell {header_ref} already has a value"
                            )
                        sheet_xml = _patch_cell(
                            sheet_xml,
                            _MONTH_HEADER_ROW,
                            col_letter,
                            float(_excel_serial(_month_end(month))),
                        )
                    else:
                        sheet_xml = _append_cell_to_row(
                            sheet_xml,
                            _MONTH_HEADER_ROW,
                            _cell_xml(
                                header_ref,
                                header_style,
                                str(_excel_serial(_month_end(month))),
                            ),
                        )
                    for row_number in range(_FIRST_CATEGORY_ROW, _LAST_CATEGORY_ROW + 1):
                        row_block = _row_xml_block(sheet_xml, row_number)
                        if row_block is None:
                            raise ValueError(f"row {row_number} not present in the Budget sheet")
                        cell_ref = f"{col_letter}{row_number}"
                        if not _cell_xml_block(sheet_xml, cell_ref):
                            sheet_xml = _append_cell_to_row(
                                sheet_xml,
                                row_number,
                                _cell_xml(
                                    cell_ref,
                                    _last_cell_style(row_block),
                                    None,
                                ),
                            )
                    month_columns[month] = next_col_index
                    next_col_index += 1
                sheet_xml = _update_dimension(
                    sheet_xml, get_column_letter(next_col_index - 1)
                )

            for category, month, value in updates:
                sheet_xml = _patch_cell(
                    sheet_xml,
                    category_rows[category],
                    get_column_letter(month_columns[month]),
                    value,
                )

            updated_content_types, updated_rels, updated_workbook = _invalidate_cached_formulas(
                content_types_xml, rels_xml, workbook_xml
            )
            _write_zip_with_replacements(
                src,
                tmp_path,
                {
                    budget_part: sheet_xml,
                    "[Content_Types].xml": updated_content_types,
                    "xl/_rels/workbook.xml.rels": updated_rels,
                    "xl/workbook.xml": updated_workbook,
                },
            )
        tmp_path.replace(workbook_path)
    finally:
        tmp_path.unlink(missing_ok=True)


def _verify_budget_updates(workbook_path: Path, updates: Sequence[tuple[str, str, float]]) -> None:
    wb = load_workbook(workbook_path, read_only=True, data_only=True)
    try:
        raw_budget_by_month = _read_budget_grid(wb)
    finally:
        wb.close()
    for category, month, value in updates:
        actual = raw_budget_by_month.get(month, {}).get(category)
        if actual is None or round(float(actual), 2) != round(float(value), 2):
            raise ValueError(
                f"budget cell mismatch for {category} {month}: expected {value}, got {actual}"
            )


def _validate_updates(
    updates: Sequence[tuple[str, str, float]],
) -> list[tuple[str, str, float]]:
    try:
        entries = list(updates)
    except TypeError as exc:
        raise ValueError("updates must be an iterable of (category, month, value) triples") from exc
    if not entries:
        raise ValueError("no updates to write")
    normalized: list[tuple[str, str, float]] = []
    seen: set[tuple[str, str]] = set()
    for index, entry in enumerate(entries):
        if isinstance(entry, (str, bytes)) or not isinstance(entry, Sequence) or len(entry) != 3:
            raise ValueError(f"update {index} must be a (category, month, value) triple")
        category, month, value = entry
        if not isinstance(category, str) or not category.strip():
            raise ValueError(f"update {index} has an invalid category")
        category = category.strip()
        if not isinstance(month, str) or not re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", month):
            raise ValueError(f"update {index} has an invalid month; expected YYYY-MM")
        if not 1900 <= int(month[:4]) <= 9999:
            raise ValueError(f"update {index} has an invalid month year; expected 1900-9999")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"update {index} has a non-numeric value")
        value = float(value)
        if not math.isfinite(value):
            raise ValueError(f"update {index} has a non-finite value")
        key = (category, month)
        if key in seen:
            raise ValueError(f"duplicate update for {category} {month}")
        seen.add(key)
        normalized.append((category, month, value))
    unknown = sorted(
        {category for category, _month, _value in normalized}
        - (set(INCOME_CATEGORIES) | set(EXPENSE_CATEGORIES))
    )
    if unknown:
        raise ValueError(f"unknown budget categories: {', '.join(unknown)}")
    return normalized


def _restore_from_backup(workbook_path: Path, backup_path: Path) -> None:
    """Swap ``backup_path`` back into ``workbook_path`` atomically, so a
    concurrent unlocked reader (e.g. a GET route) never observes a torn
    file mid-restore the way an in-place ``shutil.copy2`` would allow."""
    restore_tmp = workbook_path.with_suffix(workbook_path.suffix + ".restore.tmp")
    shutil.copy2(backup_path, restore_tmp)
    restore_tmp.replace(workbook_path)


def write_budget_plan(
    workbook_path: str | Path,
    updates: Sequence[tuple[str, str, float]],
) -> Path:
    """Write budget values by raw-XML patching, with backup and rollback.

    Not internally synchronized -- the hub's threaded local server already
    serializes every call through its own ``_WORKBOOK_WRITE_LOCK`` (shared
    with Journal writes, since both touch the same workbook file). Direct
    callers outside that server must provide their own serialization.
    """
    normalized = _validate_updates(updates)
    workbook_path = Path(workbook_path)
    if not workbook_path.is_file():
        raise FileNotFoundError(f"workbook not found: {workbook_path}")
    _check_not_locked(workbook_path)

    backup_dir = workbook_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S.%f")
    backup_path = backup_dir / f"{workbook_path.stem}.{stamp}{workbook_path.suffix}"
    shutil.copy2(workbook_path, backup_path)
    try:
        _write_budget_updates(workbook_path, normalized)
    except Exception:
        _restore_from_backup(workbook_path, backup_path)
        raise
    try:
        _verify_budget_updates(workbook_path, normalized)
    except Exception:
        _restore_from_backup(workbook_path, backup_path)
        raise VerificationError(
            "updated budget cells did not read back correctly; workbook restored from backup"
        ) from None
    return backup_path
