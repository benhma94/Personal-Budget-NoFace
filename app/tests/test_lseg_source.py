from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from portfolio_tracker.data.base import FundSnapshot
from portfolio_tracker.data.lseg import IdentifierMap, LSEGDataSource, ProviderChain


class _Session:
    def open(self):
        return None

    def close(self):
        return None


class _Definition:
    def __init__(self, app_key=None):
        self.app_key = app_key

    def get_session(self):
        return _Session()


class FakeLD:
    def __init__(self):
        self.history_calls = []
        self.data_calls = []
        self.session = SimpleNamespace(
            desktop=SimpleNamespace(Definition=_Definition),
            set_default=lambda _session: None,
        )

    def get_history(self, **kwargs):
        self.history_calls.append(kwargs)
        index = pd.date_range("2026-01-02", periods=2, freq="D")
        field = kwargs["fields"][0]
        return pd.DataFrame({field: [10.0, 11.0]}, index=index)

    def get_data(self, universe, fields, parameters=None):
        self.data_calls.append((universe, fields, parameters))
        if "TR.CommonName" in fields:
            return pd.DataFrame([{
                "Instrument": universe,
                "Company Common Name": "Mapped Fund",
                "Asset Category Description": "Equity ETF",
                "Country of Headquarters": "Canada",
                "TRBC Industry Group Name": "Financials",
                "Currency": "USD",
                "Fund Type": "Equity",
            }])
        if "TR.FundCountryAllocation" in fields:
            return pd.DataFrame({
                "Instrument": [universe, universe],
                "Country Allocation % of TNA": [60.0, 20.0],
                "Allocation Name": ["CANADA", "UNITED STATES"],
            })
        if "TR.FundIndustrySectorAllocation" in fields:
            return pd.DataFrame({
                "Instrument": [universe],
                "Industry Allocation % of TNA": [100.0],
                "Allocation Name": ["TECHNOLOGY"],
            })
        if "TR.FundAssetAllocation" in fields:
            return pd.DataFrame({
                "Instrument": [universe, universe],
                "Asset Allocation % of TNA": [100.01, -0.01],
                "Allocation Name": ["EQUITY", "CASH"],
            })
        return pd.DataFrame()


@pytest.fixture
def lseg(monkeypatch):
    monkeypatch.setenv("LSEG_APP_KEY", "test-key")
    fake_ld = FakeLD()
    source = LSEGDataSource(
        identifiers=IdentifierMap({"GLDM": {"lseg_ric": "GLDM.P", "yahoo_ticker": "GLDM"}}),
        ld_module=fake_ld,
    )
    return source, fake_ld


def test_identifier_map_defaults_and_overrides():
    identifiers = IdentifierMap({"GLDM": {"lseg_ric": "GLDM.P", "yahoo_ticker": "GLDM"}})
    assert identifiers.lseg("GLDM") == "GLDM.P"
    assert identifiers.lseg("VBAL.TO") == "VBAL.TO"


def test_price_request_uses_ric_and_non_dividend_adjustments(lseg):
    source, fake_ld = lseg
    df = source.get_prices(["GLDM"], date(2026, 1, 1), date(2026, 1, 5))
    assert list(df.columns) == ["GLDM"]
    call = fake_ld.history_calls[0]
    assert call["universe"] == "GLDM.P"
    assert call["fields"] == ["TRDPRC_1"]
    assert call["adjustments"] == [
        "exchangeCorrection", "manualCorrection", "CCH", "CRE", "RPO", "RTS",
    ]


def test_lipper_snapshot_fetches_allocations_without_constituents(lseg):
    source, fake_ld = lseg
    snapshot = source.get_fund_snapshot("GLDM")
    assert snapshot.geography == {
        "Canada": pytest.approx(0.6),
        "United States": pytest.approx(0.2),
        "Unknown": pytest.approx(0.2),
    }
    assert snapshot.asset_class == {"Equity": pytest.approx(1.0)}
    assert snapshot.sources["geography"] == "lseg"
    assert not any("TR.FundHoldingName" in fields for _, fields, _ in fake_ld.data_calls)


class _Primary:
    def get_prices(self, *_args):
        raise PermissionError("not entitled")

    def get_classification(self, _ticker):
        raise KeyError("missing")

    def get_fund_snapshot(self, ticker):
        return FundSnapshot(ticker=ticker, geography={"Canada": 1.0}, sources={"geography": "lseg"})

    def close(self):
        return None


class _Fallback:
    def get_prices(self, tickers, start, end):
        return pd.DataFrame({tickers[0]: [5.0]}, index=[pd.Timestamp(start)])

    def get_classification(self, _ticker):
        return {"quote_type": "ETF", "currency": "USD"}

    def get_fund_snapshot(self, ticker):
        return FundSnapshot(
            ticker=ticker,
            geography={"United States": 1.0},
            sector={"Technology": 1.0},
            asset_class={"Equity": 1.0},
            fetched_at=datetime(2026, 1, 1),
        )


def test_provider_chain_falls_back_per_operation_and_dimension():
    chain = ProviderChain(_Primary(), _Fallback(), IdentifierMap())
    prices = chain.get_prices(["ABC"], date(2026, 1, 1), date(2026, 1, 1))
    assert prices.iloc[0, 0] == 5.0
    assert chain.provenance["price:ABC"] == "yahoo"

    snapshot = chain.get_fund_snapshot("ABC")
    assert snapshot.geography == {"Canada": 1.0}
    assert snapshot.sector == {"Technology": 1.0}
    assert snapshot.sources["geography"] == "lseg"
    assert snapshot.sources["sector"] == "yahoo"
    assert chain.provenance["fund_geography:ABC"] == "lseg"
    assert chain.provenance["fund_sector:ABC"] == "yahoo"
