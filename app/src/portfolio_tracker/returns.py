"""Portfolio return computations and blended benchmark returns."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd


def daily_portfolio_value_cad(
    units: pd.DataFrame,
    prices_cad: pd.DataFrame,
    cash_cad: pd.Series,
) -> pd.Series:
    """Total portfolio value in CAD on each day.

    V_t = Σ_i units[t, i] · price_cad[t, i] + cash[t]
    """
    if units.empty or len(units.columns) == 0:
        return cash_cad.copy()

    aligned_prices = prices_cad.reindex(index=units.index, columns=units.columns)
    holdings_value = (units * aligned_prices).sum(axis=1)
    return holdings_value.add(cash_cad, fill_value=0.0)


def daily_twr(values: pd.Series, external_flows: pd.Series) -> pd.Series:
    """Daily time-weighted return.

    factor_t = (V_t − CF_t) / V_{t−1}, return_t = factor_t − 1.
    Assumes flow occurs at start of day (Modified Dietz simplification).
    First day is NaN. Days where V_{t−1} == 0 yield NaN to avoid division by zero.
    """
    aligned_flows = external_flows.reindex(values.index, fill_value=0.0)
    prev = values.shift(1)
    with np.errstate(divide="ignore", invalid="ignore"):
        factor = (values - aligned_flows) / prev
    factor = factor.where(prev != 0, np.nan)
    return factor - 1.0


def period_twr(daily_twr_series: pd.Series, start: date, end: date) -> float:
    """Compound daily TWRs over [start, end] inclusive."""
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)
    mask = (daily_twr_series.index >= s) & (daily_twr_series.index <= e)
    window = daily_twr_series.loc[mask].dropna()
    if window.empty:
        return 0.0
    return float((1.0 + window).prod() - 1.0)


def annualize_return(period_return: float, days: int) -> float:
    """Annualize a period return over `days` calendar days using 365-day convention."""
    if days <= 0:
        return 0.0
    return float((1.0 + period_return) ** (365.0 / days) - 1.0)


def xirr(cash_flows: pd.Series) -> float:
    """Annualized money-weighted return for irregular dated cash flows.

    ``cash_flows`` uses the investor perspective: investments into the
    portfolio are negative and withdrawals/ending value are positive. The
    solver works in log-growth space, which keeps the valid rate domain above
    -100% and is more stable than Newton iteration for long histories.

    Returns NaN when the cash flows do not contain both signs or no root can
    be bracketed in the supported range.
    """
    flows = pd.to_numeric(cash_flows, errors="coerce").dropna()
    if flows.empty:
        return float("nan")
    flows.index = pd.to_datetime(flows.index).normalize()
    flows = flows.groupby(level=0).sum().sort_index()
    flows = flows[flows.abs() > 1e-12]
    if flows.empty or not (flows.lt(0).any() and flows.gt(0).any()):
        return float("nan")

    years = (flows.index - flows.index[0]).days.to_numpy(dtype=float) / 365.0
    amounts = flows.to_numpy(dtype=float)

    def npv(log_growth: float) -> float:
        return float(np.sum(amounts * np.exp(-log_growth * years)))

    # log(1 + rate) in [-12, 12] covers approximately -99.9994% to 16,275,380%.
    grid = np.linspace(-12.0, 12.0, 481)
    brackets: list[tuple[float, float]] = []
    previous_x = float(grid[0])
    previous_value = npv(previous_x)
    if abs(previous_value) < 1e-9:
        return float(np.expm1(previous_x))

    for candidate in grid[1:]:
        current_x = float(candidate)
        current_value = npv(current_x)
        if abs(current_value) < 1e-9:
            return float(np.expm1(current_x))
        if np.isfinite(previous_value) and np.isfinite(current_value):
            if np.signbit(previous_value) != np.signbit(current_value):
                brackets.append((previous_x, current_x))
        previous_x = current_x
        previous_value = current_value

    if not brackets:
        return float("nan")

    # Multiple IRRs are possible for non-conventional cash flows. Match the
    # usual XIRR convention by selecting the root nearest a 10% initial guess.
    guess = float(np.log1p(0.10))
    low, high = min(brackets, key=lambda pair: abs((pair[0] + pair[1]) / 2.0 - guess))
    low_value = npv(low)
    for _ in range(100):
        mid = (low + high) / 2.0
        mid_value = npv(mid)
        if abs(mid_value) < 1e-10 or high - low < 1e-12:
            return float(np.expm1(mid))
        if np.signbit(low_value) != np.signbit(mid_value):
            high = mid
        else:
            low = mid
            low_value = mid_value
    return float(np.expm1((low + high) / 2.0))


def money_weighted_return(
    values: pd.Series,
    external_flows: pd.Series,
    start: date,
    end: date,
) -> tuple[float, float]:
    """Return ``(period MWR, annualized XIRR)`` over a reporting window.

    The prior day's closing value is the opening investment for a window that
    begins after inception. Contributions are positive in ``external_flows``
    and are converted to negative investor cash flows; withdrawals are the
    reverse. The last available portfolio value is the terminal cash flow.
    """
    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()
    if start_ts > end_ts:
        return float("nan"), float("nan")

    clean_values = pd.to_numeric(values, errors="coerce").dropna().sort_index()
    clean_values.index = pd.to_datetime(clean_values.index).normalize()
    ending_values = clean_values.loc[clean_values.index <= end_ts]
    if ending_values.empty:
        return float("nan"), float("nan")

    aligned_flows = pd.to_numeric(external_flows, errors="coerce").fillna(0.0)
    aligned_flows.index = pd.to_datetime(aligned_flows.index).normalize()
    aligned_flows = aligned_flows.groupby(level=0).sum().sort_index()
    window_flows = aligned_flows.loc[
        (aligned_flows.index >= start_ts) & (aligned_flows.index <= end_ts)
    ]

    investor_flows: dict[pd.Timestamp, float] = {}

    def add_flow(flow_date: pd.Timestamp, amount: float) -> None:
        investor_flows[flow_date] = investor_flows.get(flow_date, 0.0) + float(amount)

    prior_values = clean_values.loc[clean_values.index < start_ts]
    if not prior_values.empty:
        add_flow(start_ts, -float(prior_values.iloc[-1]))

    for flow_date, amount in window_flows.items():
        if abs(float(amount)) > 1e-12:
            add_flow(pd.Timestamp(flow_date), -float(amount))

    # For legacy/incomplete histories with no recognized initial contribution,
    # fall back to the first in-window valuation as the opening investment.
    if not any(amount < 0 for amount in investor_flows.values()):
        first_values = clean_values.loc[
            (clean_values.index >= start_ts) & (clean_values.index <= end_ts)
        ]
        if first_values.empty:
            return float("nan"), float("nan")
        add_flow(pd.Timestamp(first_values.index[0]), -float(first_values.iloc[0]))

    add_flow(end_ts, float(ending_values.iloc[-1]))
    annualized = xirr(pd.Series(investor_flows, dtype=float))
    if not np.isfinite(annualized):
        return float("nan"), float("nan")

    days = (end_ts - start_ts).days
    if days <= 0:
        period = 0.0
    else:
        period = float(np.expm1(np.log1p(annualized) * days / 365.0))
    return period, annualized


def cash_flow_matched_values(
    daily_returns: pd.Series,
    external_flows: pd.Series,
) -> pd.Series:
    """Synthetic account value after applying dated flows to daily returns.

    This creates a benchmark portfolio that receives the same contributions
    and withdrawals as the real portfolio. Flows are invested at the start of
    the day, matching :func:`daily_twr`'s convention.
    """
    idx = external_flows.index.union(daily_returns.index).sort_values()
    returns = pd.to_numeric(daily_returns, errors="coerce").reindex(idx).fillna(0.0)
    flows = pd.to_numeric(external_flows, errors="coerce").reindex(idx).fillna(0.0)
    value = 0.0
    out: list[float] = []
    for ts in idx:
        value = (value + float(flows.loc[ts])) * (1.0 + float(returns.loc[ts]))
        out.append(value)
    return pd.Series(out, index=idx, dtype=float)


def blended_benchmark_daily_returns(
    blend: list[tuple[str, float]],
    prices_cad_by_ticker: dict[str, pd.Series],
) -> pd.Series:
    """Daily-rebalanced blended benchmark daily return.

    For each component, daily return = price_t / price_{t-1} - 1.
    Blend r_blend,t = Σ_i w_i · r_i,t.

    All component price series must share an index (caller's responsibility).
    """
    component_returns = []
    for ticker, weight in blend:
        prices = prices_cad_by_ticker[ticker]
        ret = prices.pct_change()
        component_returns.append(ret * weight)

    return pd.concat(component_returns, axis=1).sum(axis=1, min_count=1)
