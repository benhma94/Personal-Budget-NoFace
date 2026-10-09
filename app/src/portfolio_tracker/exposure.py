"""Geographic, sector, and asset-class exposure with fund lookthrough."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from portfolio_tracker.data.base import ExposureSource


@dataclass
class ExposureBreakdown:
    geography: dict[str, float] = field(default_factory=dict)
    sector: dict[str, float] = field(default_factory=dict)
    asset_class: dict[str, float] = field(default_factory=dict)


_ETF_QUOTE_TYPES = {"ETF", "MUTUALFUND"}

_BENCHMARK_ROLES = {
    "XIC": "canada_equity",
    ".GSPTSE": "canada_equity",
    ".TRGSPTSE": "canada_equity",
    "^GSPC": "us_equity",
    ".SPXTR": "us_equity",
    "XAW": "international_equity",
    ".FTDLMSXNA": "international_equity",
    "XBB": "debt",
    ".FT26045CADT": "debt",
}

_BENCHMARK_ROLE_LABELS = {
    "canada_equity": "Canada",
    "us_equity": "US",
    "international_equity": "International",
    "debt": "Bonds",
}

_US_COUNTRIES = {"UNITED STATES", "UNITED STATES OF AMERICA", "USA", "US"}


def benchmark_role_label(ticker: str) -> str:
    """Human-friendly label for a benchmark component, e.g. "Canada" for XIC.TO.

    Falls back to the raw ticker for components (or custom blends) that don't
    match one of the recognized Canada/US/international/debt proxy roles.
    """
    role = _BENCHMARK_ROLES.get(ticker.upper().removesuffix(".TO"))
    return _BENCHMARK_ROLE_LABELS.get(role, ticker)

# Total-return-swap ("Corporate Class") ETFs hold swap collateral (cash/T-bills)
# on their balance sheet while their economic exposure is the swapped index. Lipper's
# fund allocation data reports the collateral, not the look-through exposure, which
# misclassifies most of the fund's value as Cash/Other and its geography as the
# issuer's own Canadian listing country. Skip fund lookthrough for these known
# tickers, use the plain ETF classification fallback for asset class (100% Equity),
# and use the mapped region below instead of the listing country for geography.
_SWAP_STRUCTURE_GEOGRAPHY = {
    # Tracks the Global X (formerly Horizons) EAFE Futures Roll Index: MSCI EAFE
    # futures, i.e. developed markets ex-US/Canada.
    "HXDM.TO": "International Developed (EAFE)",
}


def compute_exposure(
    positions: dict[str, float],
    cash_cad: float,
    source: ExposureSource,
) -> ExposureBreakdown:
    """Aggregate exposure across positions using fund-level allocations.

    `positions` maps ticker → CAD market value. Cash is always classified as Cash
    asset class and unallocated geographically/by sector.
    """
    geo: dict[str, float] = defaultdict(float)
    sec: dict[str, float] = defaultdict(float)
    ac: dict[str, float] = defaultdict(float)

    total = sum(positions.values()) + cash_cad
    if total <= 0:
        return ExposureBreakdown()

    for ticker, value_cad in positions.items():
        if value_cad == 0:
            continue
        classification = source.get_classification(ticker)
        weight = value_cad / total

        is_etf = classification.get("quote_type", "").upper() in _ETF_QUOTE_TYPES
        geography_override = _SWAP_STRUCTURE_GEOGRAPHY.get(ticker.upper())
        snapshot = source.get_fund_snapshot(ticker) if is_etf and geography_override is None else None

        country = geography_override or classification.get("country") or "Unknown"
        sector = classification.get("sector") or "Unknown"
        qt = classification.get("quote_type", "").upper()
        asset = _quote_type_to_asset_class(qt)

        if snapshot is not None:
            _add_distribution(geo, snapshot.geography, weight, country)
            _add_distribution(sec, snapshot.sector, weight, sector)
            _add_distribution(ac, snapshot.asset_class, weight, asset)
        else:
            geo[country] += weight
            sec[sector] += weight
            ac[asset] += weight

    if cash_cad > 0:
        ac["Cash"] += cash_cad / total

    return ExposureBreakdown(geography=dict(geo), sector=dict(sec), asset_class=dict(ac))


def allocation_matched_benchmark_blend(
    configured_blend: list[tuple[str, float]],
    positions: dict[str, float],
    source: ExposureSource,
) -> list[tuple[str, float]]:
    """Weight benchmark components to match the identifiable portfolio allocation.

    The configured tickers determine the components; their configured weights are
    only a fallback when the expected Canada, US, international, and debt proxies
    cannot all be identified. Cash, other/unknown asset classes, unknown equity
    geography, and positions absent from ``positions`` are excluded before the
    resulting component weights are normalized.
    """
    role_tickers = {
        _BENCHMARK_ROLES.get(ticker.upper().removesuffix(".TO")): ticker
        for ticker, _ in configured_blend
    }
    expected_roles = set(_BENCHMARK_ROLES.values())
    if set(role_tickers) != expected_roles:
        return configured_blend

    values = {role: 0.0 for role in expected_roles}
    for ticker, value_cad in positions.items():
        if value_cad <= 0:
            continue
        try:
            classification = source.get_classification(ticker)
        except (KeyError, ValueError):
            # A priced override may still have no usable identity/classification.
            continue
        quote_type = (classification.get("quote_type") or "").upper()
        geography_override = _SWAP_STRUCTURE_GEOGRAPHY.get(ticker.upper())
        snapshot = (
            source.get_fund_snapshot(ticker)
            if quote_type in _ETF_QUOTE_TYPES and geography_override is None
            else None
        )

        asset_allocation = (
            snapshot.asset_class
            if snapshot is not None and snapshot.asset_class
            else {_quote_type_to_asset_class(quote_type): 1.0}
        )
        debt_weight = _distribution_weight(asset_allocation, "Debt")
        equity_weight = _distribution_weight(asset_allocation, "Equity")
        values["debt"] += value_cad * debt_weight

        if equity_weight <= 0:
            continue
        geography = (
            snapshot.geography
            if snapshot is not None and snapshot.geography
            else {geography_override or classification.get("country") or "Unknown": 1.0}
        )
        for country, country_weight in geography.items():
            country_name = str(country or "").strip().upper()
            if country_name in {"", "UNKNOWN", "OTHER", "NOT CLASSIFIED"}:
                continue
            if country_name == "CANADA":
                role = "canada_equity"
            elif country_name in _US_COUNTRIES:
                role = "us_equity"
            else:
                role = "international_equity"
            values[role] += value_cad * equity_weight * float(country_weight)

    identifiable_value = sum(values.values())
    if identifiable_value <= 0:
        return configured_blend
    weights_by_ticker = {
        role_tickers[role]: value / identifiable_value
        for role, value in values.items()
    }
    return [(ticker, weights_by_ticker[ticker]) for ticker, _ in configured_blend]


def _distribution_weight(distribution: dict[str, float], bucket: str) -> float:
    return sum(
        float(weight)
        for name, weight in distribution.items()
        if str(name).strip().casefold() == bucket.casefold()
    )


def _add_distribution(target, distribution: dict[str, float], position_weight: float, fallback: str) -> None:
    if not distribution:
        target[fallback] += position_weight
        return
    for bucket, fund_weight in distribution.items():
        target[bucket or "Unknown"] += position_weight * float(fund_weight)


def _quote_type_to_asset_class(quote_type: str) -> str:
    if quote_type in {"BOND", "FIXEDINCOME"}:
        return "Debt"
    if quote_type in {"CURRENCY", "CASH"}:
        return "Cash"
    return "Equity"
