"""Generic bank-CSV import: no per-bank parser.

A CSV's header row is hashed into a `header_signature`. The first time a
signature is seen, the caller (the server, on behalf of the UI) asks the
user which columns are date / description / amount (or separate debit/
credit columns) and which account the file belongs to; that mapping is
saved in the store and reused automatically on later imports with the
same signature.

Every parsed row gets a `fingerprint` — a hash of account + date + amount +
description — so re-importing an overlapping statement is a no-op.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable


@dataclass(frozen=True)
class ColumnMapping:
    """How to read one bank's CSV layout.

    Either `amount_col` (a single signed-or-unsigned column, combined with
    `sign`) or both `out_col`/`in_col` (separate debit/credit columns, values
    given as positive magnitudes) must be set.
    """

    date_col: str
    desc_col: str
    amount_col: str | None = None
    sign: int = 1
    out_col: str | None = None
    in_col: str | None = None

    def __post_init__(self) -> None:
        has_single = self.amount_col is not None
        has_split = self.out_col is not None and self.in_col is not None
        if has_single == has_split:
            raise ValueError("provide exactly one of amount_col or (out_col and in_col)")


@dataclass(frozen=True)
class ParsedRow:
    account: str
    txn_date: str  # ISO yyyy-mm-dd
    description: str
    amount: float  # negative = money out, positive = money in
    fingerprint: str
    duplicate: bool


class CsvFormatError(ValueError):
    """Raised when a CSV doesn't match the mapping it's being read with."""


def sniff_headers(text: str) -> list[str]:
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return []
    return [h.strip() for h in header]


def header_signature(headers: Iterable[str]) -> str:
    normalized = "|".join(h.strip() for h in headers)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


_DATE_HEADER_RE = re.compile(r"date", re.IGNORECASE)

_DATE_FORMAT_CANDIDATES: list[str] = [
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%m/%d/%y",
    "%d/%m/%y",
    "%m-%d-%Y",
    "%d-%m-%Y",
    "%b %d, %Y",
    "%d %b %Y",
]


def guess_date_column(headers: Iterable[str]) -> str | None:
    """First header that looks like a date column, or None if none do."""
    for header in headers:
        if _DATE_HEADER_RE.search(header):
            return header
    return None


def sample_column_values(text: str, date_col: str, sample_size: int = 20) -> list[str]:
    """First `sample_size` non-blank values of `date_col`, in file order.

    Returns an empty list if the column is missing or has no non-blank values.
    """
    reader = csv.DictReader(io.StringIO(text))
    reader.fieldnames = [h.strip() for h in (reader.fieldnames or [])]
    if date_col not in reader.fieldnames:
        return []

    samples: list[str] = []
    for raw in reader:
        value = (raw.get(date_col) or "").strip()
        if value:
            samples.append(value)
        if len(samples) >= sample_size:
            break
    return samples


def guess_date_format(text: str, date_col: str, sample_size: int = 20) -> str | None:
    """Guess a strptime format for `date_col` by sampling its values.

    Tries `_DATE_FORMAT_CANDIDATES` in order and returns the first format
    under which every sampled value parses. Returns None if the column is
    missing, has no non-blank values, or no candidate fits every sample —
    callers fall back to asking the user.
    """
    samples = sample_column_values(text, date_col, sample_size)
    if not samples:
        return None

    for fmt in _DATE_FORMAT_CANDIDATES:
        try:
            for value in samples:
                datetime.strptime(value, fmt)
        except ValueError:
            continue
        return fmt
    return None


def preview_date_parses(samples: list[str], date_format: str) -> list[dict[str, str | None]]:
    """Try `date_format` against each sample, for showing a user a live preview.

    Each result is `{"raw": <sample>, "parsed": <ISO date or None>}` — `parsed`
    is None when `date_format` doesn't match that particular sample.
    """
    results: list[dict[str, str | None]] = []
    for raw in samples:
        try:
            parsed = datetime.strptime(raw, date_format).date().isoformat()
        except ValueError:
            parsed = None
        results.append({"raw": raw, "parsed": parsed})
    return results


def compute_fingerprint(account: str, txn_date: str, amount: float, description: str) -> str:
    key = f"{account}|{txn_date}|{amount:.2f}|{description}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


_MONEY_JUNK = re.compile(r"[,$\s]")


def _to_float(raw: str | None) -> float:
    if raw is None:
        return 0.0
    text = _MONEY_JUNK.sub("", raw.strip())
    if not text:
        return 0.0
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    value = float(text)
    return -value if negative else value


def parse_csv(
    text: str,
    account: str,
    mapping: ColumnMapping,
    date_format: str,
    known_fingerprints: set[str],
) -> list[ParsedRow]:
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = [h.strip() for h in (reader.fieldnames or [])]
    reader.fieldnames = fieldnames
    required = [mapping.date_col, mapping.desc_col]
    required += [mapping.amount_col] if mapping.amount_col else [mapping.out_col, mapping.in_col]
    missing = [col for col in required if col not in fieldnames]
    if missing:
        raise CsvFormatError(f"CSV is missing expected column(s): {', '.join(missing)}")

    rows: list[ParsedRow] = []
    for raw in reader:
        raw_date = (raw.get(mapping.date_col) or "").strip()
        if not raw_date:
            continue
        txn_date = datetime.strptime(raw_date, date_format).date().isoformat()
        description = (raw.get(mapping.desc_col) or "").strip()
        if mapping.amount_col:
            amount = _to_float(raw.get(mapping.amount_col)) * mapping.sign
        else:
            amount = _to_float(raw.get(mapping.in_col)) - _to_float(raw.get(mapping.out_col))
        fingerprint = compute_fingerprint(account, txn_date, amount, description)
        rows.append(
            ParsedRow(
                account=account,
                txn_date=txn_date,
                description=description,
                amount=amount,
                fingerprint=fingerprint,
                duplicate=fingerprint in known_fingerprints,
            )
        )
    return rows
