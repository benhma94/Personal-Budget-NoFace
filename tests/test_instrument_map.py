from openpyxl import Workbook
import pytest

from portfolio_tracker.io_excel import read_instrument_map
from portfolio_tracker.data.lseg import IdentifierMap


def _workbook(path, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "InstrumentMap"
    ws.append(["ticker", "lseg_ric", "yahoo_ticker", "note"])
    for row in rows:
        ws.append(row)
    wb.save(path)


def test_read_instrument_map(tmp_path):
    path = tmp_path / "portfolio.xlsx"
    _workbook(path, [["GLDM", "GLDM.P", "GLDM", "primary RIC"]])
    result = read_instrument_map(path)
    assert result["GLDM"] == {
        "lseg_ric": "GLDM.P",
        "yahoo_ticker": "GLDM",
        "note": "primary RIC",
    }


def test_duplicate_instrument_map_ticker_is_rejected(tmp_path):
    path = tmp_path / "portfolio.xlsx"
    _workbook(path, [["GLDM", "GLDM.P", "GLDM", ""], ["GLDM", "GLDM.N", "GLDM", ""]])
    with pytest.raises(ValueError, match="duplicate ticker"):
        read_instrument_map(path)


def test_universe_bond_index_has_xbb_yahoo_fallback():
    identifiers = IdentifierMap()

    assert identifiers.lseg(".FT26045CADT") == ".FT26045CADT"
    assert identifiers.yahoo(".FT26045CADT") == "XBB.TO"


def test_tsx_total_return_index_has_adjusted_xic_yahoo_fallback():
    identifiers = IdentifierMap()

    assert identifiers.lseg(".TRGSPTSE") == ".TRGSPTSE"
    assert identifiers.yahoo(".TRGSPTSE") == "XIC.TO"
