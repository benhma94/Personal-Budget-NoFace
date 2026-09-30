"""Tests for the Wealthsimple CSV importer."""
from __future__ import annotations

from io import StringIO

import pandas as pd
import pytest

from portfolio_tracker.io_csv import (
    normalize_ticker,
    read_wealthsimple_csv,
)


# ----- ticker normalization -----

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("ZSP", "ZSP.TO"),         # bare TSX symbol → .TO
        ("XAW", "XAW.TO"),
        ("AAPL", "AAPL"),          # US-listed override
        ("MSFT", "MSFT"),
        ("EXAMPLE.UN", "EXAMPLE-UN.TO"),
        ("ZSP.TO", "ZSP.TO"),      # already suffixed
        ("CURRENTFUND", "CURRENTFUND"),
        ("LEGACYFUND", "CURRENTFUND"),
        ("", ""),
    ],
)
def test_normalize_ticker(raw, expected):
    assert normalize_ticker(raw) == expected


# ----- CSV parsing -----

_HEADER = (
    "transaction_date,settlement_date,account_id,account_type,activity_type,"
    "activity_sub_type,direction,symbol,name,currency,quantity,unit_price,"
    "commission,net_cash_amount"
)


def _csv(*rows: str) -> str:
    return "\n".join([_HEADER, *rows])


def _read(csv_text: str):
    df, warnings = read_wealthsimple_csv(StringIO(csv_text))
    return df, warnings


def test_buy_row_maps_to_BUY():
    csv = _csv("2024-01-04,2024-01-04,ACC1,TFSA,Trade,BUY,LONG,ZSP,BMO,CAD,58,68.65,0,-3981.7")
    df, warnings = _read(csv)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["type"] == "BUY"
    assert row["ticker"] == "ZSP.TO"
    assert row["units"] == 58.0
    assert row["price_native"] == 68.65
    assert row["cash_native"] == -3981.7
    assert row["currency"] == "CAD"


def test_sell_row_maps_to_SELL_with_negative_units():
    csv = _csv("2024-06-01,2024-06-03,ACC1,TFSA,Trade,SELL,LONG,ZSP,BMO,CAD,10,70.00,0,700.00")
    df, _ = _read(csv)
    row = df.iloc[0]
    assert row["type"] == "SELL"
    assert row["units"] == -10.0  # flipped
    assert row["cash_native"] == 700.0


def test_dividend_row_maps_to_DIV():
    csv = _csv("2024-04-02,,ACC1,TFSA,Dividend,,,ZSP,BMO,CAD,41.36,,,41.36")
    df, _ = _read(csv)
    row = df.iloc[0]
    assert row["type"] == "DIV"
    assert row["ticker"] == "ZSP.TO"
    assert row["cash_native"] == 41.36
    assert row["units"] == 0.0  # quantity in dividend rows is per-share rate; not held units


def test_money_movement_eft_positive_is_CONTRIB():
    csv = _csv("2023-08-01,,ACC1,FHSA,MoneyMovement,EFT,,,,CAD,5000,,,5000")
    df, _ = _read(csv)
    row = df.iloc[0]
    assert row["type"] == "CONTRIB"
    assert row["cash_native"] == 5000.0
    assert row["external_flow_native"] == 5000.0
    assert row["ticker"] == ""


def test_money_movement_eft_negative_is_WITHDRAW():
    csv = _csv("2024-05-15,,ACC1,TFSA,MoneyMovement,EFT,,,,CAD,-1500,,,-1500")
    df, _ = _read(csv)
    row = df.iloc[0]
    assert row["type"] == "WITHDRAW"
    assert row["cash_native"] == -1500.0
    assert row["external_flow_native"] == -1500.0


def test_transfer_tf_is_INTERNAL_not_external():
    csv = _csv("2026-05-06,,ACC1,Non-registered,MoneyMovement,TRANSFER_TF,,,,CAD,-32002.56,,,-32002.56")
    df, _ = _read(csv)
    row = df.iloc[0]
    assert row["type"] == "INTERNAL"
    assert row["cash_native"] == -32002.56
    assert row["external_flow_native"] == 0.0


def test_fee_row_maps_to_FEE():
    csv = _csv("2024-12-31,,ACC1,TFSA,Fee,,,,,CAD,,,,-5.00")
    df, _ = _read(csv)
    row = df.iloc[0]
    assert row["type"] == "FEE"
    assert row["cash_native"] == -5.00


def test_interest_maps_to_DIV_internal_credit():
    csv = _csv("2024-08-01,,ACC1,TFSA,Interest,,,,,CAD,,,,12.34")
    df, _ = _read(csv)
    row = df.iloc[0]
    assert row["type"] == "DIV"
    assert row["cash_native"] == 12.34


def test_return_of_capital_maps_to_DIV():
    csv = _csv("2024-09-01,,ACC1,TFSA,ReturnOfCapital,,,ZSP,BMO,CAD,,,,10.50")
    df, _ = _read(csv)
    row = df.iloc[0]
    assert row["type"] == "DIV"
    assert row["cash_native"] == 10.50


def test_stock_split_subdivision_adds_units_no_cash():
    csv = _csv("2025-08-18,,ACC1,Non-registered,CorporateAction,SUBDIVISION,LONG,ZEQT,BMO,,8,,,")
    df, _ = _read(csv)
    row = df.iloc[0]
    assert row["type"] == "BUY"
    assert row["units"] == 8.0
    assert row["price_native"] == 0.0
    assert row["cash_native"] == 0.0


def test_stock_dividend_adds_units_no_cash():
    csv = _csv("2024-09-15,,ACC1,TFSA,StockDividend,,,ZSP,BMO,CAD,0.5,,,")
    df, _ = _read(csv)
    row = df.iloc[0]
    assert row["type"] == "BUY"
    assert row["units"] == 0.5
    assert row["cash_native"] == 0.0


def test_security_transfer_in_adds_units():
    csv = _csv("2024-06-01,,ACC1,RRSP,SecurityTransfer,,LONG,ZSP,BMO,CAD,100,,,7000")
    df, _ = _read(csv)
    row = df.iloc[0]
    assert row["type"] == "BUY"
    assert row["units"] == 100.0
    assert row["cash_native"] == 0.0
    assert row["external_flow_native"] == 7000.0


def test_security_transfer_out_removes_units_and_is_external_withdrawal():
    csv = _csv("2024-06-01,,ACC1,RRSP,SecurityTransfer,,,ZSP,BMO,CAD,-100,,,-7000")
    df, _ = _read(csv)
    row = df.iloc[0]
    assert row["type"] == "SELL"
    assert row["units"] == -100.0
    assert row["cash_native"] == 0.0
    assert row["external_flow_native"] == -7000.0


def test_security_transfer_uses_units_times_price_when_value_missing():
    csv = _csv("2024-06-01,,ACC1,RRSP,SecurityTransfer,,LONG,ZSP,BMO,CAD,100,70,,")
    df, _ = _read(csv)

    assert df.iloc[0]["external_flow_native"] == 7000.0


def test_administrative_payment_maps_to_DIV():
    csv = _csv("2024-12-15,,ACC1,TFSA,AdministrativePayment,MANAGEMENT_FEE_REFUND,,,,CAD,,,,3.42")
    df, _ = _read(csv)
    row = df.iloc[0]
    assert row["type"] == "DIV"
    assert row["cash_native"] == 3.42


def test_unknown_activity_type_warns_and_skips():
    csv = _csv("2024-01-01,,ACC1,TFSA,SomeNewType,,,,,CAD,,,,0")
    df, warnings = _read(csv)
    assert len(df) == 0
    assert any("SomeNewType" in w for w in warnings)


def test_trailing_as_of_row_is_dropped():
    csv = _csv(
        "2024-01-04,2024-01-04,ACC1,TFSA,Trade,BUY,LONG,ZSP,BMO,CAD,58,68.65,0,-3981.7",
        "As of 2026-05-28 14:56 GMT-04:00,,,,,,,,,,,,,",
    )
    df, _ = _read(csv)
    assert len(df) == 1


def test_account_column_combines_account_type_and_id():
    csv = _csv("2024-01-04,2024-01-04,ACC123,RRSP,Trade,BUY,LONG,ZSP,BMO,CAD,1,68.65,0,-68.65")
    df, _ = _read(csv)
    assert "RRSP" in df.iloc[0]["account"]
    assert "ACC123" in df.iloc[0]["account"]


def test_us_listed_ticker_not_suffixed():
    csv = _csv("2024-01-04,2024-01-04,ACC1,LIRA,Trade,BUY,LONG,AAPL,Example,CAD,14.36,316.25,0,-4542.82")
    df, _ = _read(csv)
    assert df.iloc[0]["ticker"] == "AAPL"


def test_effective_at_replaces_transaction_date_in_new_export():
    csv = StringIO(
        "effective_at,settlement_date,account_id,account_type,activity_type,"
        "activity_sub_type,description,direction,symbol,name,currency,quantity,"
        "unit_price,commission,net_cash_amount\n"
        "2026-08-24T13:45:00-04:00,,A1,TFSA,Trade,BUY,,LONG,DOL,Dollarama,CAD,2,200,0,-400\n"
    )
    df, warnings = read_wealthsimple_csv(csv)
    assert warnings == []
    assert df.iloc[0]["date"] == pd.Timestamp("2026-08-24")
    assert df.iloc[0]["ticker"] == "DOL.TO"


def test_effective_date_replaces_effective_at_in_2026_09_export():
    csv = StringIO(
        "effective_date,effective_time,settlement_date,account_id,account_type,"
        "activity_type,activity_sub_type,description,direction,symbol,name,"
        "currency,quantity,unit_price,commission,net_cash_amount\n"
        "2026-08-24,13:45:00,,A1,TFSA,Trade,BUY,,LONG,DOL,Dollarama,CAD,2,200,0,-400\n"
    )
    df, warnings = read_wealthsimple_csv(csv)
    assert warnings == []
    assert df.iloc[0]["date"] == pd.Timestamp("2026-08-24")
    assert df.iloc[0]["ticker"] == "DOL.TO"
