"""Tests for exposure aggregation with ETF lookthrough."""
from __future__ import annotations

import pytest

from portfolio_tracker.data.base import FundSnapshot
from portfolio_tracker.exposure import (
    ExposureBreakdown,
    allocation_matched_benchmark_blend,
    compute_exposure,
)


def test_single_us_stock_exposure(fake_source):
    fake_source.classifications["AAPL"] = {
        "quote_type": "EQUITY",
        "country": "United States",
        "sector": "Technology",
        "currency": "USD",
    }
    positions = {"AAPL": 10_000.0}  # CAD value

    exp = compute_exposure(positions, cash_cad=0.0, source=fake_source)
    assert isinstance(exp, ExposureBreakdown)
    assert exp.geography["United States"] == pytest.approx(1.0)
    assert exp.sector["Technology"] == pytest.approx(1.0)
    assert exp.asset_class["Equity"] == pytest.approx(1.0)


def test_cash_appears_in_asset_class(fake_source):
    fake_source.classifications["AAPL"] = {
        "quote_type": "EQUITY",
        "country": "United States",
        "sector": "Technology",
        "currency": "USD",
    }
    positions = {"AAPL": 8_000.0}

    exp = compute_exposure(positions, cash_cad=2_000.0, source=fake_source)
    assert exp.asset_class["Equity"] == pytest.approx(0.8)
    assert exp.asset_class["Cash"] == pytest.approx(0.2)


def test_etf_lookthrough_uses_fund_country_allocations(fake_source):
    fake_source.classifications["XAW.TO"] = {
        "quote_type": "ETF",
        "country": "Canada",
        "sector": "",
        "currency": "CAD",
    }
    fake_source.fund_snapshots["XAW.TO"] = FundSnapshot(
        ticker="XAW.TO",
        geography={"United States": 0.7, "Japan": 0.3},
        sector={"Technology": 0.7, "Industrials": 0.3},
        asset_class={"Equity": 1.0},
    )
    positions = {"XAW.TO": 10_000.0}

    exp = compute_exposure(positions, cash_cad=0.0, source=fake_source)
    assert exp.geography["United States"] == pytest.approx(0.7)
    assert exp.geography["Japan"] == pytest.approx(0.3)


def test_bond_etf_classified_as_debt(fake_source):
    fake_source.classifications["XBB.TO"] = {
        "quote_type": "ETF",
        "country": "Canada",
        "sector": "",
        "currency": "CAD",
    }
    fake_source.fund_snapshots["XBB.TO"] = FundSnapshot(
        ticker="XBB.TO",
        geography={"Canada": 1.0},
        sector={"Fixed Income": 1.0},
        asset_class={"Debt": 1.0},
    )
    positions = {"XBB.TO": 10_000.0}

    exp = compute_exposure(positions, cash_cad=0.0, source=fake_source)
    assert exp.asset_class["Debt"] == pytest.approx(1.0)


def test_swap_structure_etf_ignores_fund_snapshot_and_falls_back(fake_source):
    """HXDM.TO-style total-return-swap ETFs report swap collateral as Cash/Other

    in Lipper's fund allocation data, not real economic exposure, and their
    geography defaults to the issuer's Canadian listing country rather than the
    swapped index's actual region. These known tickers should skip fund
    lookthrough entirely, use the plain ETF classification fallback for asset
    class (100% Equity), and use the mapped region instead of listing country.
    """
    fake_source.classifications["HXDM.TO"] = {
        "quote_type": "ETF",
        "country": "Canada",
        "sector": "",
        "currency": "CAD",
    }
    fake_source.fund_snapshots["HXDM.TO"] = FundSnapshot(
        ticker="HXDM.TO",
        geography={"Japan": 0.4, "Unknown": 0.6},
        asset_class={"Cash": 0.62, "Other": 0.38},
    )
    positions = {"HXDM.TO": 10_000.0}

    exp = compute_exposure(positions, cash_cad=0.0, source=fake_source)
    assert exp.asset_class == {"Equity": pytest.approx(1.0)}
    assert exp.geography == {"International Developed (EAFE)": pytest.approx(1.0)}


def test_swap_structure_etf_counted_as_international_in_benchmark_blend(fake_source):
    """The benchmark-blend lookthrough must use the same region override, or a

    swap-structure ETF's equity gets misattributed to the Canada-equity role
    via its issuer's listing country.
    """
    blend = [("XIC.TO", 0.15), ("^GSPC", 0.30), ("XAW.TO", 0.35), ("XBB.TO", 0.20)]
    fake_source.classifications.update({
        "CA": {"quote_type": "EQUITY", "country": "Canada"},
        "HXDM.TO": {"quote_type": "ETF", "country": "Canada"},
    })
    fake_source.fund_snapshots["HXDM.TO"] = FundSnapshot(
        ticker="HXDM.TO",
        geography={"Japan": 0.4, "Unknown": 0.6},
        asset_class={"Cash": 0.62, "Other": 0.38},
    )

    result = dict(allocation_matched_benchmark_blend(
        blend, {"CA": 100, "HXDM.TO": 300}, fake_source
    ))

    assert result["XIC.TO"] == pytest.approx(100 / 400)
    assert result["XAW.TO"] == pytest.approx(300 / 400)
    assert result["^GSPC"] == pytest.approx(0.0)
    assert result["XBB.TO"] == pytest.approx(0.0)


def test_etf_lookthrough_failure_falls_back_to_listed_country(fake_source):
    """Empty fund allocations use the ETF's own classification."""
    fake_source.classifications["XAW.TO"] = {
        "quote_type": "ETF",
        "country": "Canada",
        "sector": "Diversified",
        "currency": "CAD",
    }
    positions = {"XAW.TO": 10_000.0}
    exp = compute_exposure(positions, cash_cad=0.0, source=fake_source)
    assert exp.geography["Canada"] == pytest.approx(1.0)


def test_geo_weights_sum_to_one(fake_source):
    fake_source.classifications["AAPL"] = {"quote_type": "EQUITY", "country": "United States", "sector": "Technology", "currency": "USD"}
    fake_source.classifications["TD.TO"] = {"quote_type": "EQUITY", "country": "Canada", "sector": "Financial Services", "currency": "CAD"}
    positions = {"AAPL": 6_000.0, "TD.TO": 4_000.0}

    exp = compute_exposure(positions, cash_cad=0.0, source=fake_source)
    assert sum(exp.geography.values()) == pytest.approx(1.0)
    assert sum(exp.sector.values()) == pytest.approx(1.0)
    assert sum(exp.asset_class.values()) == pytest.approx(1.0)


def test_benchmark_blend_matches_identifiable_lookthrough_allocation(fake_source):
    blend = [("XIC.TO", 0.15), ("^GSPC", 0.30), ("XAW.TO", 0.35), ("XBB.TO", 0.20)]
    fake_source.classifications.update({
        "CA": {"quote_type": "EQUITY", "country": "Canada"},
        "US": {"quote_type": "EQUITY", "country": "United States"},
        "INTL": {"quote_type": "ETF", "country": "Canada"},
        "BAL": {"quote_type": "ETF", "country": "Canada"},
        "BOND": {"quote_type": "BOND", "country": "Canada"},
        "CASHLIKE": {"quote_type": "CASH", "country": "Unknown"},
    })
    fake_source.fund_snapshots["INTL"] = FundSnapshot(
        ticker="INTL",
        geography={"Japan": 0.75, "Unknown": 0.25},
        asset_class={"Equity": 0.8, "Other": 0.2},
    )
    fake_source.fund_snapshots["BAL"] = FundSnapshot(
        ticker="BAL",
        geography={"Canada": 0.5, "United States of America": 0.5},
        asset_class={"Equity": 0.6, "Debt": 0.4},
    )

    result = dict(allocation_matched_benchmark_blend(
        blend,
        {"CA": 200, "US": 300, "INTL": 400, "BAL": 100, "BOND": 50, "CASHLIKE": 100},
        fake_source,
    ))

    # Identifiable contributions: CA=230, US=330, international=240, debt=90.
    assert result["XIC.TO"] == pytest.approx(230 / 890)
    assert result["^GSPC"] == pytest.approx(330 / 890)
    assert result["XAW.TO"] == pytest.approx(240 / 890)
    assert result["XBB.TO"] == pytest.approx(90 / 890)
    assert sum(result.values()) == pytest.approx(1.0)


def test_benchmark_blend_excludes_unidentifiable_position(fake_source):
    blend = [("XIC.TO", 0.15), ("^GSPC", 0.30), ("XAW.TO", 0.35), ("XBB.TO", 0.20)]
    fake_source.classifications["CA"] = {"quote_type": "EQUITY", "country": "Canada"}

    result = dict(allocation_matched_benchmark_blend(
        blend, {"CA": 100, "MYSTERY": 900}, fake_source
    ))

    assert result == {"XIC.TO": 1.0, "^GSPC": 0.0, "XAW.TO": 0.0, "XBB.TO": 0.0}


def test_benchmark_blend_recognizes_direct_lseg_index_rics(fake_source):
    blend = [
        (".TRGSPTSE", 0.15),
        (".SPXTR", 0.30),
        (".FTDLMSXNA", 0.35),
        (".FT26045CADT", 0.20),
    ]
    fake_source.classifications.update({
        "CA": {"quote_type": "EQUITY", "country": "Canada"},
        "US": {"quote_type": "EQUITY", "country": "United States"},
        "JP": {"quote_type": "EQUITY", "country": "Japan"},
        "BOND": {"quote_type": "BOND", "country": "Canada"},
    })

    result = dict(allocation_matched_benchmark_blend(
        blend, {"CA": 10, "US": 20, "JP": 30, "BOND": 40}, fake_source
    ))

    assert result == {
        ".TRGSPTSE": pytest.approx(0.10),
        ".SPXTR": pytest.approx(0.20),
        ".FTDLMSXNA": pytest.approx(0.30),
        ".FT26045CADT": pytest.approx(0.40),
    }
