from __future__ import annotations

import pytest

from journal_entry.csv_import import (
    ColumnMapping,
    CsvFormatError,
    compute_fingerprint,
    guess_date_column,
    guess_date_format,
    header_signature,
    parse_csv,
    preview_date_parses,
    sample_column_values,
    sniff_headers,
)

_SIMPLE_CSV = """Date,Description,Amount
2026-08-01,EXAMPLE MARKET #1042,-41.20
2026-08-02,PAYROLL DEPOSIT,1200.00
2026-08-03,"UBER *EATS, TORONTO",-12.40
"""

_SPLIT_CSV = """Date,Details,Funds Out,Funds In
01/08/2026,CORNER STORE,"18.75",
02/08/2026,PAY BILL,,"209.27"
03/08/2026,NO ACTIVITY,,
"""


def test_sniff_headers():
    assert sniff_headers(_SIMPLE_CSV) == ["Date", "Description", "Amount"]


def test_sniff_headers_empty_text():
    assert sniff_headers("") == []


def test_header_signature_stable_and_order_sensitive():
    a = header_signature(["Date", "Description", "Amount"])
    b = header_signature(["Date", "Description", "Amount"])
    c = header_signature(["Description", "Date", "Amount"])
    assert a == b
    assert a != c


def test_mapping_requires_exactly_one_amount_style():
    with pytest.raises(ValueError):
        ColumnMapping(date_col="Date", desc_col="Description")
    with pytest.raises(ValueError):
        ColumnMapping(
            date_col="Date", desc_col="Description",
            amount_col="Amount", out_col="Out", in_col="In",
        )


def test_parse_single_amount_column():
    mapping = ColumnMapping(date_col="Date", desc_col="Description", amount_col="Amount")
    rows = parse_csv(_SIMPLE_CSV, "Rewards Card", mapping, "%Y-%m-%d", known_fingerprints=set())
    assert len(rows) == 3
    assert rows[0].account == "Rewards Card"
    assert rows[0].txn_date == "2026-08-01"
    assert rows[0].description == "EXAMPLE MARKET #1042"
    assert rows[0].amount == -41.20
    assert rows[1].amount == 1200.00
    assert rows[2].description == "UBER *EATS, TORONTO"
    assert all(not r.duplicate for r in rows)


def test_parse_split_out_in_columns():
    mapping = ColumnMapping(date_col="Date", desc_col="Details", out_col="Funds Out", in_col="Funds In")
    rows = parse_csv(_SPLIT_CSV, "Secondary Checking", mapping, "%d/%m/%Y", known_fingerprints=set())
    assert len(rows) == 3
    assert rows[0].amount == -18.75
    assert rows[1].amount == 209.27
    assert rows[2].amount == 0.0


def test_parse_marks_known_fingerprints_as_duplicate():
    mapping = ColumnMapping(date_col="Date", desc_col="Description", amount_col="Amount")
    first_pass = parse_csv(_SIMPLE_CSV, "Rewards Card", mapping, "%Y-%m-%d", known_fingerprints=set())
    seen = {r.fingerprint for r in first_pass}
    second_pass = parse_csv(_SIMPLE_CSV, "Rewards Card", mapping, "%Y-%m-%d", known_fingerprints=seen)
    assert all(r.duplicate for r in second_pass)


def test_fingerprint_is_stable_and_sensitive_to_amount():
    fp1 = compute_fingerprint("Cash", "2026-08-01", -10.0, "Coffee")
    fp2 = compute_fingerprint("Cash", "2026-08-01", -10.0, "Coffee")
    fp3 = compute_fingerprint("Cash", "2026-08-01", -10.01, "Coffee")
    assert fp1 == fp2
    assert fp1 != fp3


def test_sign_flips_amount_when_configured():
    csv_text = "Date,Description,Amount\n2026-08-01,PAYMENT,631.11\n"
    mapping = ColumnMapping(date_col="Date", desc_col="Description", amount_col="Amount", sign=-1)
    rows = parse_csv(csv_text, "Rewards Card", mapping, "%Y-%m-%d", known_fingerprints=set())
    assert rows[0].amount == -631.11


def test_missing_column_raises_csv_format_error():
    mapping = ColumnMapping(date_col="Date", desc_col="Missing Column", amount_col="Amount")
    with pytest.raises(CsvFormatError):
        parse_csv(_SIMPLE_CSV, "Cash", mapping, "%Y-%m-%d", known_fingerprints=set())


def test_parse_tolerates_space_after_delimiter_in_header():
    # Some bank exports put a space after each comma in the header row
    # ("Date, Transaction Details, Funds Out, Funds In"). sniff_headers()
    # strips these before the mapping is saved, so the saved column names
    # are clean; parse_csv must match against the same stripped names
    # instead of the raw (space-prefixed) DictReader fieldnames.
    csv_text = (
        "Date, Transaction Details, Funds Out, Funds In\n"
        "01/08/2026, CORNER STORE, 18.75, \n"
    )
    assert sniff_headers(csv_text) == ["Date", "Transaction Details", "Funds Out", "Funds In"]
    mapping = ColumnMapping(
        date_col="Date", desc_col="Transaction Details", out_col="Funds Out", in_col="Funds In",
    )
    rows = parse_csv(csv_text, "Secondary Checking", mapping, "%d/%m/%Y", known_fingerprints=set())
    assert len(rows) == 1
    assert rows[0].description == "CORNER STORE"
    assert rows[0].amount == -18.75


def test_blank_date_rows_are_skipped():
    csv_text = "Date,Description,Amount\n,Filler,0\n2026-08-01,Real,-1.00\n"
    mapping = ColumnMapping(date_col="Date", desc_col="Description", amount_col="Amount")
    rows = parse_csv(csv_text, "Cash", mapping, "%Y-%m-%d", known_fingerprints=set())
    assert len(rows) == 1
    assert rows[0].description == "Real"


def test_ragged_row_with_missing_trailing_columns_is_skipped_not_crashed():
    # DictReader fills missing trailing fields with None, not "" — a short row
    # (e.g. a truncated export) must not raise on .strip().
    csv_text = "Date,Description,Amount\n2026-08-01,Real,-1.00\n2026-08-02\n"
    mapping = ColumnMapping(date_col="Date", desc_col="Description", amount_col="Amount")
    rows = parse_csv(csv_text, "Cash", mapping, "%Y-%m-%d", known_fingerprints=set())
    assert len(rows) == 2
    assert rows[1].description == ""
    assert rows[1].amount == 0.0


def test_money_junk_stripped_dollar_commas_parens():
    csv_text = 'Date,Description,Amount\n2026-08-01,Big,"$1,234.56"\n2026-08-02,Neg,"($5.00)"\n'
    mapping = ColumnMapping(date_col="Date", desc_col="Description", amount_col="Amount")
    rows = parse_csv(csv_text, "Cash", mapping, "%Y-%m-%d", known_fingerprints=set())
    assert rows[0].amount == 1234.56
    assert rows[1].amount == -5.00


def test_guess_date_column_matches_header_containing_date():
    assert guess_date_column(["Transaction Date", "Description", "Amount"]) == "Transaction Date"


def test_guess_date_column_case_insensitive():
    assert guess_date_column(["DATE", "Description", "Amount"]) == "DATE"


def test_guess_date_column_returns_first_match():
    assert guess_date_column(["Post Date", "Value Date", "Amount"]) == "Post Date"


def test_guess_date_column_none_when_no_date_like_header():
    assert guess_date_column(["Description", "Amount"]) is None


def test_guess_date_format_detects_iso():
    csv_text = "Date,Description,Amount\n2026-08-01,EXAMPLE MARKET,-41.20\n2026-08-15,CORNER STORE,-18.75\n"
    assert guess_date_format(csv_text, "Date") == "%Y-%m-%d"


def test_guess_date_format_detects_us_slash_when_unambiguous():
    csv_text = "Date,Description,Amount\n08/25/2026,EXAMPLE MARKET,-41.20\n08/26/2026,CORNER STORE,-18.75\n"
    assert guess_date_format(csv_text, "Date") == "%m/%d/%Y"


def test_guess_date_format_none_when_no_candidate_fits_all_samples():
    csv_text = "Date,Description,Amount\n2026-08-01,EXAMPLE MARKET,-41.20\nnot-a-date,CORNER STORE,-18.75\n"
    assert guess_date_format(csv_text, "Date") is None


def test_guess_date_format_none_when_column_missing():
    csv_text = "Date,Description,Amount\n2026-08-01,EXAMPLE MARKET,-41.20\n"
    assert guess_date_format(csv_text, "Nope") is None


def test_guess_date_format_none_when_all_values_blank():
    csv_text = "Date,Description,Amount\n,EXAMPLE MARKET,-41.20\n,CORNER STORE,-18.75\n"
    assert guess_date_format(csv_text, "Date") is None


def test_guess_date_format_only_samples_first_sample_size_rows():
    # 20 valid ISO rows followed by one malformed row that must never be
    # sampled (it's past the default sample_size=20 cutoff).
    good_rows = "".join(f"2026-08-{d:02d},ROW{d},-1.00\n" for d in range(1, 21))
    csv_text = "Date,Description,Amount\n" + good_rows + "not-a-date,ROW21,-1.00\n"
    assert guess_date_format(csv_text, "Date") == "%Y-%m-%d"


def test_sample_column_values_returns_non_blank_values_in_order():
    csv_text = "Date,Description,Amount\n2026-08-01,EXAMPLE MARKET,-41.20\n,Filler,0\n2026-08-02,CORNER STORE,-18.75\n"
    assert sample_column_values(csv_text, "Date") == ["2026-08-01", "2026-08-02"]


def test_sample_column_values_respects_sample_size():
    rows = "".join(f"2026-08-{d:02d},ROW{d},-1.00\n" for d in range(1, 11))
    csv_text = "Date,Description,Amount\n" + rows
    assert len(sample_column_values(csv_text, "Date", sample_size=3)) == 3


def test_sample_column_values_empty_when_column_missing():
    csv_text = "Date,Description,Amount\n2026-08-01,EXAMPLE MARKET,-41.20\n"
    assert sample_column_values(csv_text, "Nope") == []


def test_preview_date_parses_reports_matches_and_mismatches():
    result = preview_date_parses(["08/25/2026", "not-a-date"], "%m/%d/%Y")
    assert result == [
        {"raw": "08/25/2026", "parsed": "2026-08-25"},
        {"raw": "not-a-date", "parsed": None},
    ]


def test_preview_date_parses_empty_samples_returns_empty_list():
    assert preview_date_parses([], "%Y-%m-%d") == []
