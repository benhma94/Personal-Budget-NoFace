"""Tests for the SQLite-backed price/FX/classification cache."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import pandas as pd
import pytest

from portfolio_tracker.data.cache import Cache


@pytest.fixture
def cache(tmp_path):
    return Cache(tmp_path / "test_cache.sqlite")


def test_round_trip_prices(cache):
    s = pd.Series(
        [10.0, 11.0, 12.0],
        index=pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
    )
    cache.put_prices("AAPL", s, currency="USD")

    got = cache.get_prices("AAPL", date(2024, 1, 2), date(2024, 1, 4))
    assert got is not None
    assert len(got) == 3
    assert got.iloc[0] == 10.0


def test_get_prices_returns_none_when_missing(cache):
    assert cache.get_prices("MISSING", date(2024, 1, 1), date(2024, 1, 5)) is None


def test_get_prices_returns_none_when_range_not_covered(cache):
    s = pd.Series([10.0, 11.0], index=pd.to_datetime(["2024-01-02", "2024-01-03"]))
    cache.put_prices("AAPL", s, currency="USD")

    # Cache has Jan 2-3, but we ask for Jan 4
    got = cache.get_prices("AAPL", date(2024, 1, 4), date(2024, 1, 5))
    assert got is None


def test_round_trip_fx(cache):
    s = pd.Series(
        [1.35, 1.36],
        index=pd.to_datetime(["2024-01-02", "2024-01-03"]),
    )
    cache.put_fx("USDCAD", s)

    got = cache.get_fx("USDCAD", date(2024, 1, 2), date(2024, 1, 3))
    assert got is not None
    assert got.iloc[0] == 1.35


def test_round_trip_classification_within_ttl(cache):
    payload = {"quote_type": "EQUITY", "country": "United States"}
    cache.put_classification("AAPL", payload)

    got = cache.get_classification("AAPL", max_age_days=7)
    assert got == payload


def test_classification_expires_past_ttl(cache):
    payload = {"quote_type": "EQUITY"}
    # Manually backdate by writing through internal path
    cache.put_classification("AAPL", payload, fetched_at=datetime.now() - timedelta(days=30))

    assert cache.get_classification("AAPL", max_age_days=7) is None


def test_provider_caches_are_isolated(cache):
    idx = pd.date_range("2024-01-01", "2024-01-03", freq="D")
    cache.put_prices("ABC", pd.Series(1.0, index=idx), "CAD", provider="lseg")
    cache.put_prices("ABC", pd.Series(2.0, index=idx), "CAD", provider="yahoo")
    lseg = cache.get_prices("ABC", date(2024, 1, 1), date(2024, 1, 3), provider="lseg")
    yahoo = cache.get_prices("ABC", date(2024, 1, 1), date(2024, 1, 3), provider="yahoo")
    assert lseg.iloc[0] == 1.0
    assert yahoo.iloc[0] == 2.0


def test_new_cache_omits_legacy_tables(cache):
    tables = {
        row[0]
        for row in cache._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert tables == {"prices_v2", "fx_v2", "classifications_v2", "fund_snapshots"}
