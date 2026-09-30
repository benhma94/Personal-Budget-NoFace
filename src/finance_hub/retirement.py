"""Probabilistic retirement planning plus deterministic stress calculations.

Forecasts work in today's dollars. Scheduled spending and income therefore
remain real while nominal investment returns are converted using the caller's
inflation assumption.
"""
from __future__ import annotations

import calendar
import math
from datetime import date
from typing import Any

import numpy as np


DEFAULT_NOMINAL_RETURN = 0.05
DEFAULT_INFLATION_RATE = 0.02
DEFAULT_EFFECTIVE_TAX_RATE = 0.0
DEFAULT_VOLATILITY = 0.12
DEFAULT_PLAN_THROUGH_AGE = 95
SCENARIO_SPREAD = 0.02
FORECAST_MONTHS = 60 * 12
MONTE_CARLO_SIMULATIONS = 10_000
MONTE_CARLO_SEED = 20260831
_DAYS_PER_YEAR = 365.2425
SAVINGS_RATE_WINDOW_MONTHS = 12

# Service Canada published rates (canada.ca/en/services/benefits/publicpensions).
# CPP average is the "average amount for new beneficiaries" at age 65 (April 2026);
# OAS max is the age 65-74 rate for the July-September 2026 quarter. Both are
# indexed periodically and will drift out of date; they are a starting estimate
# the user is expected to correct for their own entitlement.
CPP_AVERAGE_MONTHLY_AT_65 = 877.01
OAS_MAX_MONTHLY_65_TO_74 = 751.97
CPP_MIN_START_AGE = 60
CPP_STANDARD_AGE = 65
CPP_MAX_START_AGE = 70
CPP_EARLY_REDUCTION_PER_MONTH = 0.006
CPP_DEFERRAL_INCREASE_PER_MONTH = 0.007
OAS_STANDARD_AGE = 65
OAS_MAX_START_AGE = 70
OAS_DEFERRAL_INCREASE_PER_MONTH = 0.006
DEFAULT_RETIREMENT_INCOME = (CPP_AVERAGE_MONTHLY_AT_65 + OAS_MAX_MONTHLY_65_TO_74) * 12


def build_retirement_defaults(
    budget_payload: dict[str, Any],
    portfolio_document: dict[str, Any] | None,
    *,
    portfolio_stale_days: float | None = None,
) -> dict[str, Any]:
    """Combine live budget data and the cached portfolio into UI defaults."""
    latest_period = str(budget_payload["latest_period"])
    latest_transaction_date = date.fromisoformat(
        str(budget_payload["latest_transaction_date"])
    )
    ytd_spending = float(budget_payload["ytd"][latest_period]["spending"])
    ytd_income = float(budget_payload["ytd"][latest_period]["income"])
    elapsed_days = (latest_transaction_date - date(latest_transaction_date.year, 1, 1)).days + 1
    days_in_year = 366 if calendar.isleap(latest_transaction_date.year) else 365
    annualized_spending = ytd_spending * days_in_year / elapsed_days
    annualized_income = ytd_income * days_in_year / elapsed_days

    average_savings_rate, savings_rate_window_months = _average_savings_rate(
        budget_payload, latest_period
    )
    annual_contribution = max(
        0.0,
        (average_savings_rate or 0.0) * annualized_income,
    )

    portfolio_payload = portfolio_document.get("payload") if portfolio_document else None
    starting_portfolio = None
    portfolio_as_of = None
    nominal_return = DEFAULT_NOMINAL_RETURN
    nominal_return_source = "illustrative fallback"
    annual_volatility = DEFAULT_VOLATILITY
    volatility_source = "illustrative fallback"
    tax_exempt_portfolio = {
        "tfsa_value": None,
        "tfsa_share_of_portfolio": None,
        "non_registered_cost_base": None,
        "non_registered_share_of_portfolio": None,
        "total_value": None,
        "share_of_portfolio": None,
    }
    if portfolio_payload:
        raw_value = portfolio_payload.get("total_value_cad")
        starting_portfolio = float(raw_value) if raw_value is not None else None
        portfolio_as_of = portfolio_payload.get("as_of_date")
        for row in portfolio_payload.get("performance", []):
            if str(row.get("Horizon", "")).casefold() != "since-inception":
                continue
            historical_return = row.get("Portfolio Return")
            if (
                isinstance(historical_return, (int, float))
                and not isinstance(historical_return, bool)
                and math.isfinite(historical_return)
                and historical_return - SCENARIO_SPREAD > -1
            ):
                nominal_return = float(historical_return)
                nominal_return_source = "portfolio since-inception annualized money-weighted return"
            break
        risk_volatility = portfolio_payload.get("risk", {}).get("Annualized Volatility")
        if (
            isinstance(risk_volatility, (int, float))
            and not isinstance(risk_volatility, bool)
            and math.isfinite(risk_volatility)
            and risk_volatility >= 0
        ):
            annual_volatility = float(risk_volatility)
            volatility_source = "portfolio annualized volatility"

        tfsa_value = sum(
            float(row.get("value_cad", 0.0) or 0.0)
            for row in portfolio_payload.get("account_breakdown", [])
            if _account_key(row.get("account_type")) == "tfsa"
        )
        non_registered_cost_base = sum(
            float(row.get("total_acb", 0.0) or 0.0)
            for row in portfolio_payload.get("account_positions", [])
            if _account_key(row.get("account_type")) == "nonregistered"
        )
        tax_exempt_total = tfsa_value + non_registered_cost_base
        tax_exempt_portfolio = {
            "tfsa_value": tfsa_value,
            "tfsa_share_of_portfolio": (
                tfsa_value / starting_portfolio
                if starting_portfolio is not None and starting_portfolio > 0 else None
            ),
            "non_registered_cost_base": non_registered_cost_base,
            "non_registered_share_of_portfolio": (
                non_registered_cost_base / starting_portfolio
                if starting_portfolio is not None and starting_portfolio > 0 else None
            ),
            "total_value": tax_exempt_total,
            "share_of_portfolio": (
                tax_exempt_total / starting_portfolio
                if starting_portfolio is not None and starting_portfolio > 0 else None
            ),
        }

    return {
        "defaults": {
            "starting_portfolio": starting_portfolio,
            "annual_spending": annualized_spending,
            "annual_retirement_income": DEFAULT_RETIREMENT_INCOME,
            "nominal_return": nominal_return,
            "inflation_rate": DEFAULT_INFLATION_RATE,
            "effective_tax_rate": DEFAULT_EFFECTIVE_TAX_RATE,
            "retirement_start_date": portfolio_as_of or date.today().isoformat(),
            "annual_contribution": annual_contribution,
            "annual_volatility": annual_volatility,
            "date_of_birth": None,
            "plan_through_age": DEFAULT_PLAN_THROUGH_AGE,
            "income_streams": [],
            "spending_phases": [],
            "one_time_events": [],
        },
        "sources": {
            "budget_as_of": latest_transaction_date.isoformat(),
            "latest_period": latest_period,
            "ytd_spending": ytd_spending,
            "elapsed_days": elapsed_days,
            "days_in_year": days_in_year,
            "portfolio_as_of": portfolio_as_of,
            "portfolio_generated_at": (
                portfolio_document.get("generated_at") if portfolio_document else None
            ),
            "portfolio_stale_days": portfolio_stale_days,
            "nominal_return_source": nominal_return_source,
            "volatility_source": volatility_source,
            "annualized_income": annualized_income,
            "average_savings_rate": average_savings_rate,
            "savings_rate_window_months": savings_rate_window_months,
            "retirement_income_basis": {
                "cpp_average_monthly_at_65": CPP_AVERAGE_MONTHLY_AT_65,
                "oas_max_monthly_65_to_74": OAS_MAX_MONTHLY_65_TO_74,
                "cpp_min_start_age": CPP_MIN_START_AGE,
                "cpp_standard_age": CPP_STANDARD_AGE,
                "cpp_max_start_age": CPP_MAX_START_AGE,
                "cpp_early_reduction_per_month": CPP_EARLY_REDUCTION_PER_MONTH,
                "cpp_deferral_increase_per_month": CPP_DEFERRAL_INCREASE_PER_MONTH,
                "oas_standard_age": OAS_STANDARD_AGE,
                "oas_max_start_age": OAS_MAX_START_AGE,
                "oas_deferral_increase_per_month": OAS_DEFERRAL_INCREASE_PER_MONTH,
                "min_start_age": CPP_MIN_START_AGE,
                "max_start_age": CPP_MAX_START_AGE,
            },
        },
        "forecast_horizon_years": FORECAST_MONTHS // 12,
        "scenario_spread": SCENARIO_SPREAD,
        "tax_exempt_portfolio": tax_exempt_portfolio,
    }


def _average_savings_rate(
    budget_payload: dict[str, Any], latest_period: str
) -> tuple[float | None, int]:
    """Mean monthly savings_rate over the trailing window, skipping no-income months."""
    monthly = budget_payload["monthly"]
    window = [
        period for period in budget_payload["periods"] if period <= latest_period
    ][-SAVINGS_RATE_WINDOW_MONTHS:]
    rates = [
        monthly[period]["savings_rate"] for period in window
        if monthly[period]["savings_rate"] is not None
    ]
    average = sum(rates) / len(rates) if rates else None
    return average, len(rates)


def forecast_retirement(
    inputs: dict[str, Any],
    *,
    start_date: date | None = None,
) -> dict[str, Any]:
    """Project fixed-return scenarios plus a reproducible Monte Carlo range."""
    starting_portfolio = _money(inputs, "starting_portfolio")
    annual_spending = _money(inputs, "annual_spending")
    annual_income = _money(inputs, "annual_retirement_income")
    nominal_return = _number(inputs, "nominal_return")
    inflation_rate = _number(inputs, "inflation_rate")
    effective_tax_rate = _number(inputs, "effective_tax_rate")
    annual_contribution = _optional_money(inputs, "annual_contribution", 0.0)
    annual_volatility = _optional_number(inputs, "annual_volatility", DEFAULT_VOLATILITY)

    if inflation_rate < 0:
        raise ValueError("inflation_rate must be non-negative")
    if not 0 <= effective_tax_rate < 1:
        raise ValueError("effective_tax_rate must be at least 0% and less than 100%")
    if nominal_return - SCENARIO_SPREAD <= -1:
        raise ValueError("the conservative nominal return must be greater than -100%")
    if annual_volatility < 0:
        raise ValueError("annual_volatility must be non-negative")

    forecast_start = start_date or date.today()
    retirement_start = _retirement_date(inputs, forecast_start)
    delay_days = (retirement_start - forecast_start).days
    delay_years = delay_days / _DAYS_PER_YEAR
    if delay_years > FORECAST_MONTHS / 12:
        raise ValueError("retirement_start_date cannot be more than 60 years after the forecast start")

    birth_date = _optional_date(inputs.get("date_of_birth"), "date_of_birth")
    plan_through_age = _optional_age(inputs.get("plan_through_age"), birth_date)
    plan_end = _plan_end_date(
        birth_date, plan_through_age, retirement_start, forecast_start
    )
    income_streams = _income_streams(
        inputs.get("income_streams"), annual_income, retirement_start
    )
    spending_phases = _spending_phases(
        inputs.get("spending_phases"), annual_spending, retirement_start
    )
    one_time_events = _one_time_events(
        inputs.get("one_time_events"), forecast_start, plan_end
    )

    retirement_spending = _active_spending(spending_phases, retirement_start)
    retirement_income = _active_income(income_streams, retirement_start)
    net_spending_gap = max(retirement_spending - retirement_income, 0.0)
    gross_annual_draw = net_spending_gap / (1 - effective_tax_rate)
    scenarios = _deterministic_scenarios(
        starting_portfolio=starting_portfolio,
        annual_contribution=annual_contribution,
        nominal_return=nominal_return,
        inflation_rate=inflation_rate,
        effective_tax_rate=effective_tax_rate,
        forecast_start=forecast_start,
        retirement_start=retirement_start,
        plan_end=plan_end,
        income_streams=income_streams,
        spending_phases=spending_phases,
        one_time_events=one_time_events,
    )
    base_scenario = next(row for row in scenarios if row["key"] == "base")
    base_retirement_balance = base_scenario["retirement_start_balance"]
    initial_withdrawal_rate = (
        gross_annual_draw / base_retirement_balance if base_retirement_balance > 0 else None
    )
    probabilistic = _monte_carlo(
        starting_portfolio=starting_portfolio,
        annual_contribution=annual_contribution,
        nominal_return=nominal_return,
        annual_volatility=annual_volatility,
        inflation_rate=inflation_rate,
        effective_tax_rate=effective_tax_rate,
        forecast_start=forecast_start,
        retirement_start=retirement_start,
        plan_end=plan_end,
        birth_date=birth_date,
        income_streams=income_streams,
        spending_phases=spending_phases,
        one_time_events=one_time_events,
    )

    return {
        "start_date": forecast_start.isoformat(),
        "retirement_start_date": retirement_start.isoformat(),
        "plan_end_date": plan_end.isoformat(),
        "plan_through_age": plan_through_age,
        "delay_years": delay_years,
        "horizon_years": (plan_end - forecast_start).days / _DAYS_PER_YEAR,
        "summary": {
            "annual_spending_at_retirement": retirement_spending,
            "annual_income_at_retirement": retirement_income,
            "net_spending_gap": net_spending_gap,
            "gross_annual_draw": gross_annual_draw,
            "initial_withdrawal_rate": initial_withdrawal_rate,
            "base_retirement_balance": base_retirement_balance,
        },
        "scenarios": scenarios,
        "probabilistic": probabilistic,
    }


def _deterministic_scenarios(
    *,
    starting_portfolio: float,
    annual_contribution: float,
    nominal_return: float,
    inflation_rate: float,
    effective_tax_rate: float,
    forecast_start: date,
    retirement_start: date,
    plan_end: date,
    income_streams: list[dict[str, Any]],
    spending_phases: list[dict[str, Any]],
    one_time_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    scenarios = []
    for key, label, offset in (
        ("conservative", "Conservative", -SCENARIO_SPREAD),
        ("base", "Base", 0.0),
        ("optimistic", "Optimistic", SCENARIO_SPREAD),
    ):
        scenario_nominal = nominal_return + offset
        real_annual = (1 + scenario_nominal) / (1 + inflation_rate) - 1
        balance = max(0.0, starting_portfolio + _events_on(one_time_events, forecast_start))
        current_date = forecast_start
        balances = [_balance_point(forecast_start, forecast_start, balance)]

        month = 1
        while True:
            point_date = _add_months(forecast_start, month)
            if point_date >= retirement_start:
                break
            balance = _grow(balance, real_annual, current_date, point_date)
            balance += annual_contribution / 12
            balance += _events_between(one_time_events, current_date, point_date)
            balance = max(0.0, balance)
            balances.append(_balance_point(forecast_start, point_date, balance))
            current_date = point_date
            month += 1

        if current_date < retirement_start:
            balance = _grow(balance, real_annual, current_date, retirement_start)
            contribution_fraction = (
                1.0 if retirement_start == _add_months(forecast_start, month)
                else (retirement_start - current_date).days / (_DAYS_PER_YEAR / 12)
            )
            balance += annual_contribution / 12 * contribution_fraction
            balance += _events_between(one_time_events, current_date, retirement_start)
            balance = max(0.0, balance)
            balances.append(_balance_point(forecast_start, retirement_start, balance))
            current_date = retirement_start
        retirement_start_balance = balance
        depletion_months: int | None = 0 if balance <= 0 else None

        month = 1
        while current_date < plan_end and balance > 0:
            scheduled = _add_months(retirement_start, month)
            point_date = min(scheduled, plan_end)
            balance = _grow(balance, real_annual, current_date, point_date)
            fraction = 1.0 if point_date == scheduled else (
                (point_date - current_date).days / (_DAYS_PER_YEAR / 12)
            )
            gross_draw = _gross_draw_for_period(
                current_date, point_date, fraction,
                income_streams, spending_phases, effective_tax_rate,
            )
            balance = max(
                0.0,
                balance - gross_draw + _events_between(
                    one_time_events, current_date, point_date
                ),
            )
            balances.append(_balance_point(forecast_start, point_date, balance))
            current_date = point_date
            if balance <= 1e-8:
                balance = 0.0
                balances[-1]["value_cad"] = 0.0
                depletion_months = month
                break
            month += 1

        scenarios.append({
            "key": key,
            "label": label,
            "nominal_return": scenario_nominal,
            "real_return": real_annual,
            "retirement_start_balance": retirement_start_balance,
            "depletion_months": depletion_months,
            "depletion_date": (
                _add_months(retirement_start, depletion_months).isoformat()
                if depletion_months is not None else None
            ),
            "ending_balance": balance,
            "balances": balances,
        })
    return scenarios


def _monte_carlo(
    *,
    starting_portfolio: float,
    annual_contribution: float,
    nominal_return: float,
    annual_volatility: float,
    inflation_rate: float,
    effective_tax_rate: float,
    forecast_start: date,
    retirement_start: date,
    plan_end: date,
    birth_date: date | None,
    income_streams: list[dict[str, Any]],
    spending_phases: list[dict[str, Any]],
    one_time_events: list[dict[str, Any]],
) -> dict[str, Any]:
    rng = np.random.default_rng(MONTE_CARLO_SEED)
    balances = np.full(MONTE_CARLO_SIMULATIONS, starting_portfolio, dtype=float)
    balances += _events_on(one_time_events, forecast_start)
    balances = np.maximum(balances, 0.0)
    alive = np.ones(MONTE_CARLO_SIMULATIONS, dtype=bool)
    depletion_month = np.full(MONTE_CARLO_SIMULATIONS, -1, dtype=int)
    if forecast_start == retirement_start:
        alive = balances > 1e-8
        depletion_month[~alive] = 0
    percentile_paths = [_percentile_point(forecast_start, birth_date, balances)]

    log_mean = (math.log1p(nominal_return) - 0.5 * annual_volatility ** 2) / 12
    monthly_sigma = annual_volatility / math.sqrt(12)
    monthly_inflation = (1 + inflation_rate) ** (1 / 12)
    previous_date = forecast_start
    month = 1
    while previous_date < plan_end:
        scheduled = _add_months(forecast_start, month)
        point_date = min(scheduled, plan_end)
        period_fraction = 1.0 if point_date == scheduled else (
            (point_date - previous_date).days / (_DAYS_PER_YEAR / 12)
        )
        period_days = (point_date - previous_date).days
        pre_retirement_days = max(
            0, (min(point_date, retirement_start) - previous_date).days
        ) if previous_date < retirement_start else 0
        retirement_days = max(
            0, (point_date - max(previous_date, retirement_start)).days
        ) if point_date > retirement_start else 0
        pre_retirement_fraction = (
            period_fraction * pre_retirement_days / period_days if period_days else 0.0
        )
        retirement_fraction = (
            period_fraction * retirement_days / period_days if period_days else 0.0
        )
        shocks = rng.normal(
            log_mean * period_fraction,
            monthly_sigma * math.sqrt(max(period_fraction, 0.0)),
            MONTE_CARLO_SIMULATIONS,
        )
        real_factor = np.exp(shocks) / monthly_inflation ** period_fraction
        balances[alive] *= real_factor[alive]
        if pre_retirement_fraction:
            balances[alive] += annual_contribution / 12 * pre_retirement_fraction
        if retirement_fraction:
            balances[alive] -= _gross_draw_for_period(
                max(previous_date, retirement_start), point_date,
                retirement_fraction, income_streams, spending_phases,
                effective_tax_rate,
            )
        balances[alive] += _events_between(
            one_time_events, previous_date, point_date
        )

        if point_date >= retirement_start:
            newly_depleted = alive & (balances <= 1e-8)
            depletion_month[newly_depleted] = month
            alive[newly_depleted] = False
        balances[~alive] = 0.0
        balances = np.maximum(balances, 0.0)
        if month % 12 == 0 or point_date == plan_end:
            percentile_paths.append(_percentile_point(point_date, birth_date, balances))
        previous_date = point_date
        month += 1

    failed_months = depletion_month[depletion_month >= 0]
    if birth_date is not None and failed_months.size:
        failed_ages = np.array([
            _age_on(birth_date, _add_months(forecast_start, int(value)))
            for value in failed_months
        ])
        depletion_percentiles = _percentiles(failed_ages)
    else:
        depletion_percentiles = None
    return {
        "simulations": MONTE_CARLO_SIMULATIONS,
        "seed": MONTE_CARLO_SEED,
        "success_probability": float(np.mean(alive)),
        "ending_balance_percentiles": _percentiles(balances),
        "depletion_age_percentiles": depletion_percentiles,
        "balance_percentiles": percentile_paths,
    }


def _balance_point(start: date, point_date: date, balance: float) -> dict[str, Any]:
    return {
        "elapsed_years": (point_date - start).days / _DAYS_PER_YEAR,
        "date": point_date.isoformat(),
        "value_cad": balance,
    }


def _percentile_point(
    point_date: date, birth_date: date | None, balances: np.ndarray
) -> dict[str, Any]:
    return {
        "date": point_date.isoformat(),
        "age": _age_on(birth_date, point_date) if birth_date is not None else None,
        **_percentiles(balances),
    }


def _percentiles(values: np.ndarray) -> dict[str, float]:
    p10, p50, p90 = np.percentile(values, [10, 50, 90])
    return {"p10": float(p10), "p50": float(p50), "p90": float(p90)}


def _grow(balance: float, real_return: float, start: date, end: date) -> float:
    years = (end - start).days / _DAYS_PER_YEAR
    return balance * (1 + real_return) ** years


def _gross_draw_for_period(
    start: date,
    end: date,
    period_fraction: float,
    income_streams: list[dict[str, Any]],
    spending_phases: list[dict[str, Any]],
    effective_tax_rate: float,
) -> float:
    if end <= start or period_fraction <= 0:
        return 0.0
    boundaries = {start, end}
    for row in (*income_streams, *spending_phases):
        for boundary in (row["start_date"], row["end_date"]):
            if boundary is not None and start < boundary < end:
                boundaries.add(boundary)
    ordered = sorted(boundaries)
    period_days = (end - start).days
    total = 0.0
    for left, right in zip(ordered, ordered[1:]):
        fraction = period_fraction * (right - left).days / period_days
        gap = max(
            _active_spending(spending_phases, left)
            - _active_income(income_streams, left),
            0.0,
        )
        total += gap / (1 - effective_tax_rate) / 12 * fraction
    return total


def _active_spending(phases: list[dict[str, Any]], point_date: date) -> float:
    return sum(
        row["annual_amount"] for row in phases
        if row["start_date"] <= point_date
        and (row["end_date"] is None or point_date < row["end_date"])
    )


def _active_income(streams: list[dict[str, Any]], point_date: date) -> float:
    return sum(
        row["annual_amount"] for row in streams
        if row["start_date"] <= point_date
        and (row["end_date"] is None or point_date < row["end_date"])
    )


def _events_on(events: list[dict[str, Any]], point_date: date) -> float:
    return sum(row["amount"] for row in events if row["date"] == point_date)


def _events_between(events: list[dict[str, Any]], start: date, end: date) -> float:
    return sum(row["amount"] for row in events if start < row["date"] <= end)


def _income_streams(
    raw: Any, legacy_income: float, retirement_start: date
) -> list[dict[str, Any]]:
    base = [{
        "name": "Retirement income",
        "annual_amount": legacy_income,
        "start_date": retirement_start,
        "end_date": None,
    }] if legacy_income else []
    if raw is None or raw == []:
        return base
    return base + _dated_amount_rows(raw, "income_streams", allow_overlap=True)


def _spending_phases(
    raw: Any, legacy_spending: float, retirement_start: date
) -> list[dict[str, Any]]:
    if raw is None or raw == []:
        return [{
            "name": "Retirement spending",
            "annual_amount": legacy_spending,
            "start_date": retirement_start,
            "end_date": None,
        }]
    rows = _dated_amount_rows(raw, "spending_phases", allow_overlap=False)
    return rows


def _dated_amount_rows(
    raw: Any, field: str, *, allow_overlap: bool
) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ValueError(f"{field} must be a list")
    rows = []
    for index, value in enumerate(raw):
        if not isinstance(value, dict):
            raise ValueError(f"{field}[{index}] must be an object")
        amount = _dict_money(value, "annual_amount", f"{field}[{index}].annual_amount")
        start = _required_date(value.get("start_date"), f"{field}[{index}].start_date")
        end = _optional_date(value.get("end_date"), f"{field}[{index}].end_date")
        if end is not None and end <= start:
            raise ValueError(f"{field}[{index}].end_date must be after start_date")
        rows.append({
            "name": str(value.get("name") or f"{field} {index + 1}"),
            "annual_amount": amount,
            "start_date": start,
            "end_date": end,
        })
    rows.sort(key=lambda row: row["start_date"])
    if not allow_overlap:
        for left, right in zip(rows, rows[1:]):
            if left["end_date"] is None or left["end_date"] > right["start_date"]:
                raise ValueError("spending_phases cannot overlap")
    return rows


def _one_time_events(raw: Any, start: date, end: date) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("one_time_events must be a list")
    rows = []
    for index, value in enumerate(raw):
        if not isinstance(value, dict):
            raise ValueError(f"one_time_events[{index}] must be an object")
        event_date = _required_date(value.get("date"), f"one_time_events[{index}].date")
        if event_date < start or event_date > end:
            raise ValueError(f"one_time_events[{index}].date must be within the forecast")
        amount = _dict_number(value, "amount", f"one_time_events[{index}].amount")
        rows.append({
            "name": str(value.get("name") or f"Event {index + 1}"),
            "date": event_date,
            "amount": amount,
        })
    return rows


def _plan_end_date(
    birth_date: date | None,
    plan_through_age: int | None,
    retirement_start: date,
    forecast_start: date,
) -> date:
    if birth_date is None:
        return _add_months(retirement_start, FORECAST_MONTHS)
    assert plan_through_age is not None
    if birth_date >= forecast_start:
        raise ValueError("date_of_birth must be before the forecast start")
    result = _add_years(birth_date, plan_through_age)
    if result <= retirement_start:
        raise ValueError("plan_through_age must end after retirement starts")
    return result


def _optional_age(raw: Any, birth_date: date | None) -> int | None:
    if birth_date is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or int(raw) != raw:
        raise ValueError("plan_through_age must be a whole number")
    value = int(raw)
    if not 1 <= value <= 120:
        raise ValueError("plan_through_age must be between 1 and 120")
    return value


def _age_on(birth_date: date, point_date: date) -> float:
    return (point_date - birth_date).days / _DAYS_PER_YEAR


def _add_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, day=28)


def _number(inputs: dict[str, Any], name: str) -> float:
    if name not in inputs:
        raise ValueError(f"{name} is required")
    value = inputs[name]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _money(inputs: dict[str, Any], name: str) -> float:
    result = _number(inputs, name)
    if result < 0:
        raise ValueError(f"{name} must be non-negative")
    return result


def _optional_number(inputs: dict[str, Any], name: str, default: float) -> float:
    if name not in inputs or inputs[name] is None:
        return default
    return _number(inputs, name)


def _optional_money(inputs: dict[str, Any], name: str, default: float) -> float:
    if name not in inputs or inputs[name] is None:
        return default
    return _money(inputs, name)


def _dict_number(inputs: dict[str, Any], name: str, label: str) -> float:
    value = inputs.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _dict_money(inputs: dict[str, Any], name: str, label: str) -> float:
    result = _dict_number(inputs, name, label)
    if result < 0:
        raise ValueError(f"{label} must be non-negative")
    return result


def _required_date(raw_value: Any, label: str) -> date:
    result = _optional_date(raw_value, label)
    if result is None:
        raise ValueError(f"{label} is required")
    return result


def _optional_date(raw_value: Any, label: str) -> date | None:
    if raw_value in (None, ""):
        return None
    if not isinstance(raw_value, str):
        raise ValueError(f"{label} must be an ISO date")
    try:
        return date.fromisoformat(raw_value)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO date") from exc


def _retirement_date(inputs: dict[str, Any], forecast_start: date) -> date:
    raw_value = inputs.get("retirement_start_date")
    if raw_value is None:
        return forecast_start
    if not isinstance(raw_value, str):
        raise ValueError("retirement_start_date must be an ISO date")
    try:
        result = date.fromisoformat(raw_value)
    except ValueError as exc:
        raise ValueError("retirement_start_date must be an ISO date") from exc
    if result < forecast_start:
        raise ValueError("retirement_start_date cannot be before the forecast start")
    return result


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _account_key(value: Any) -> str:
    return "".join(character for character in str(value or "").casefold() if character.isalnum())
