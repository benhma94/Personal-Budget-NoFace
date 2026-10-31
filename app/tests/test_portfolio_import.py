"""Tests for the Journal tab's portfolio-transactions upload: format
detection, the preview summary, and saving where the pipeline auto-detects."""
from __future__ import annotations

from datetime import date

import pytest

from finance_hub.portfolio_api import (
    detect_portfolio_format,
    preview_portfolio_import,
    save_portfolio_import,
)

_HEADER = (
    "transaction_date,settlement_date,account_id,account_type,activity_type,"
    "activity_sub_type,direction,symbol,name,currency,quantity,unit_price,"
    "commission,net_cash_amount"
)
_CSV = "\n".join([
    _HEADER,
    "2024-01-02,2024-01-02,ACC1,TFSA,MoneyMovement,EFT,,,,CAD,,,,5000",
    "2024-01-04,2024-01-04,ACC1,TFSA,Trade,BUY,LONG,ZSP,BMO,CAD,58,68.65,0,-3981.7",
    "2024-02-15,2024-02-15,ACC1,TFSA,Dividend,,,ZSP,BMO,CAD,,,,12.5",
    "2024-03-01,2024-03-01,ACC1,TFSA,Correction,,,ZSP,BMO,CAD,,,,1",
])


@pytest.mark.parametrize("date_col", ["transaction_date", "effective_at", "effective_date"])
def test_each_wealthsimple_date_column_generation_is_detected(date_col):
    assert detect_portfolio_format([date_col, "activity_type", "net_cash_amount"]) == "wealthsimple"


def test_a_bank_csv_is_not_a_portfolio_format():
    assert detect_portfolio_format(["Date", "Description", "Amount"]) is None


def test_preview_summarises_rows_dates_types_and_skips():
    summary = preview_portfolio_import(_CSV)
    assert summary["format"] == "wealthsimple"
    assert summary["rows"] == 3
    assert summary["first_date"] == "2024-01-02"
    assert summary["last_date"] == "2024-02-15"
    assert summary["by_type"] == {"BUY": 1, "CONTRIB": 1, "DIV": 1}
    assert summary["skipped"] == 1
    assert "Correction" in summary["warnings"][0]


def test_preview_rejects_an_unrecognised_file():
    with pytest.raises(ValueError, match="Wealthsimple"):
        preview_portfolio_import("Date,Description,Amount\n2024-01-01,Coffee,-4")


def test_save_writes_a_dated_export_the_pipeline_picks_up(tmp_path):
    from portfolio_tracker import cli

    (tmp_path / "activities-export-2026-01-01.csv").write_text(_HEADER, encoding="utf-8")
    result = save_portfolio_import(_CSV, tmp_path, today=date(2026, 10, 31))
    assert result["saved_as"] == "activities-export-2026-10-31.csv"
    assert (tmp_path / result["saved_as"]).read_text(encoding="utf-8") == _CSV
    assert not list(tmp_path.glob("*.tmp"))

    df, _, path = cli._load_transactions(cli._parse_args([]), tmp_path / "portfolio.xlsx")
    assert path == str(tmp_path / "activities-export-2026-10-31.csv")
    assert len(df) == 3


def test_same_day_save_replaces_the_earlier_upload(tmp_path):
    save_portfolio_import(_HEADER, tmp_path, today=date(2026, 10, 31))
    save_portfolio_import(_CSV, tmp_path, today=date(2026, 10, 31))
    assert (tmp_path / "activities-export-2026-10-31.csv").read_text(encoding="utf-8") == _CSV


def test_rejected_upload_writes_nothing(tmp_path):
    with pytest.raises(ValueError):
        save_portfolio_import("Date,Description,Amount\n", tmp_path)
    assert list(tmp_path.iterdir()) == []
