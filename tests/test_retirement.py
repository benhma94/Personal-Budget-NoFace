from __future__ import annotations

from datetime import date

import pytest

from finance_hub.retirement import (
    CPP_AVERAGE_MONTHLY_AT_65,
    OAS_MAX_MONTHLY_65_TO_74,
    build_retirement_defaults,
    forecast_retirement,
)


def _budget(
    as_of: str,
    spending: float,
    *,
    income: float = 0.0,
    monthly: dict | None = None,
    periods: list[str] | None = None,
) -> dict:
    period = as_of[:7]
    if periods is None:
        periods = [period]
    if monthly is None:
        monthly = {period: {"income": income, "savings_rate": None}}
    return {
        "latest_period": period,
        "latest_transaction_date": as_of,
        "ytd": {period: {"spending": spending, "income": income}},
        "monthly": monthly,
        "periods": periods,
    }


def _inputs(**overrides) -> dict:
    values = {
        "starting_portfolio": 1200.0,
        "annual_spending": 1200.0,
        "annual_retirement_income": 0.0,
        "nominal_return": 0.0,
        "inflation_rate": 0.0,
        "effective_tax_rate": 0.0,
    }
    values.update(overrides)
    return values


def test_defaults_annualize_partial_non_leap_year_by_elapsed_days():
    portfolio = {
        "generated_at": "2026-02-28T12:00:00-05:00",
        "payload": {
            "as_of_date": "2026-02-28",
            "total_value_cad": 500_000,
            "performance": [{
                "Horizon": "Since-inception",
                "Return Basis": "Annualized",
                "Portfolio Return": 0.1383,
            }],
            "risk": {"Annualized Volatility": 0.184},
            "account_breakdown": [
                {"account_type": "TFSA", "value_cad": 180_000},
                {"account_type": "RRSP", "value_cad": 200_000},
            ],
            "account_positions": [
                {"account_type": "Non-registered", "ticker": "AAA", "total_acb": 70_000},
                {"account_type": "Non-registered", "ticker": "CASH", "total_acb": 5_000},
                {"account_type": "TFSA", "ticker": "BBB", "total_acb": 100_000},
            ],
        },
    }

    result = build_retirement_defaults(
        _budget("2026-02-28", 590), portfolio, portfolio_stale_days=1.25
    )

    assert result["defaults"]["annual_spending"] == pytest.approx(3650)
    assert result["defaults"]["starting_portfolio"] == 500_000
    assert result["defaults"]["nominal_return"] == pytest.approx(0.1383)
    assert result["defaults"]["annual_volatility"] == pytest.approx(0.184)
    assert result["defaults"]["plan_through_age"] == 95
    assert result["defaults"]["income_streams"] == []
    assert result["defaults"]["retirement_start_date"] == "2026-02-28"
    assert result["sources"]["elapsed_days"] == 59
    assert result["sources"]["days_in_year"] == 365
    assert result["sources"]["portfolio_stale_days"] == 1.25
    assert "money-weighted" in result["sources"]["nominal_return_source"]
    assert result["sources"]["volatility_source"] == "portfolio annualized volatility"
    assert result["tax_exempt_portfolio"] == {
        "tfsa_value": 180_000,
        "tfsa_share_of_portfolio": pytest.approx(0.36),
        "non_registered_cost_base": 75_000,
        "non_registered_share_of_portfolio": pytest.approx(0.15),
        "total_value": 255_000,
        "share_of_portfolio": pytest.approx(0.51),
    }


def test_defaults_annualize_leap_year_and_allow_missing_portfolio():
    result = build_retirement_defaults(_budget("2024-02-29", 600), None)

    assert result["defaults"]["annual_spending"] == pytest.approx(3660)
    assert result["defaults"]["starting_portfolio"] is None
    assert result["defaults"]["nominal_return"] == 0.05
    assert result["defaults"]["annual_volatility"] == 0.12
    assert result["sources"]["elapsed_days"] == 60
    assert result["sources"]["days_in_year"] == 366
    assert result["sources"]["portfolio_as_of"] is None
    assert result["sources"]["nominal_return_source"] == "illustrative fallback"
    assert result["tax_exempt_portfolio"] == {
        "tfsa_value": None,
        "tfsa_share_of_portfolio": None,
        "non_registered_cost_base": None,
        "non_registered_share_of_portfolio": None,
        "total_value": None,
        "share_of_portfolio": None,
    }


def test_defaults_fall_back_when_since_inception_return_is_not_finite():
    portfolio = {
        "payload": {
            "total_value_cad": 100,
            "performance": [{
                "Horizon": "Since-inception",
                "Portfolio Return": float("nan"),
            }],
        }
    }

    result = build_retirement_defaults(_budget("2026-02-28", 590), portfolio)

    assert result["defaults"]["nominal_return"] == 0.05


def test_contribution_defaults_to_trailing_twelve_month_average_savings_rate_times_income():
    periods = [f"2025-{m:02d}" for m in range(1, 13)] + ["2026-01"]
    monthly = {p: {"income": 1000.0, "savings_rate": 0.10} for p in periods[:-1]}
    monthly[periods[-1]] = {"income": 1000.0, "savings_rate": 0.30}
    budget = _budget("2026-01-31", 0, income=2000.0, monthly=monthly, periods=periods)

    result = build_retirement_defaults(budget, None)

    expected_rate = (11 * 0.10 + 0.30) / 12
    expected_annualized_income = 2000.0 * 365 / 31
    assert result["sources"]["savings_rate_window_months"] == 12
    assert result["sources"]["annualized_income"] == pytest.approx(expected_annualized_income)
    assert result["sources"]["average_savings_rate"] == pytest.approx(expected_rate)
    assert result["defaults"]["annual_contribution"] == pytest.approx(
        expected_rate * expected_annualized_income
    )


def test_contribution_default_uses_fewer_than_twelve_months_when_history_is_short():
    periods = ["2025-11", "2025-12", "2026-01"]
    monthly = {
        "2025-11": {"income": 1000.0, "savings_rate": 0.20},
        "2025-12": {"income": 1000.0, "savings_rate": 0.40},
        "2026-01": {"income": 1000.0, "savings_rate": 0.60},
    }
    budget = _budget("2026-01-31", 0, income=1000.0, monthly=monthly, periods=periods)

    result = build_retirement_defaults(budget, None)

    assert result["sources"]["savings_rate_window_months"] == 3
    assert result["sources"]["average_savings_rate"] == pytest.approx(0.40)


def test_contribution_default_skips_months_with_no_income():
    periods = ["2025-12", "2026-01"]
    monthly = {
        "2025-12": {"income": 0.0, "savings_rate": None},
        "2026-01": {"income": 1000.0, "savings_rate": 0.50},
    }
    budget = _budget("2026-01-31", 0, income=1000.0, monthly=monthly, periods=periods)

    result = build_retirement_defaults(budget, None)

    assert result["sources"]["savings_rate_window_months"] == 1
    assert result["sources"]["average_savings_rate"] == pytest.approx(0.50)


def test_contribution_default_clamps_negative_average_savings_rate_to_zero():
    periods = ["2026-01"]
    monthly = {"2026-01": {"income": 1000.0, "savings_rate": -0.20}}
    budget = _budget("2026-01-31", 0, income=1000.0, monthly=monthly, periods=periods)

    result = build_retirement_defaults(budget, None)

    assert result["sources"]["average_savings_rate"] == pytest.approx(-0.20)
    assert result["defaults"]["annual_contribution"] == 0.0


def test_contribution_default_is_zero_when_no_period_has_income():
    periods = ["2026-01"]
    monthly = {"2026-01": {"income": 0.0, "savings_rate": None}}
    budget = _budget("2026-01-31", 0, income=0.0, monthly=monthly, periods=periods)

    result = build_retirement_defaults(budget, None)

    assert result["sources"]["average_savings_rate"] is None
    assert result["defaults"]["annual_contribution"] == 0.0


def test_retirement_income_defaults_to_cpp_and_oas_base_estimate():
    result = build_retirement_defaults(_budget("2026-01-31", 0), None)

    assert result["defaults"]["annual_retirement_income"] == pytest.approx(
        (CPP_AVERAGE_MONTHLY_AT_65 + OAS_MAX_MONTHLY_65_TO_74) * 12
    )
    basis = result["sources"]["retirement_income_basis"]
    assert basis["cpp_average_monthly_at_65"] == CPP_AVERAGE_MONTHLY_AT_65
    assert basis["oas_max_monthly_65_to_74"] == OAS_MAX_MONTHLY_65_TO_74
    assert basis["cpp_standard_age"] == 65
    assert basis["min_start_age"] == 60
    assert basis["max_start_age"] == 70


def test_zero_return_depletes_exactly_after_twelve_months():
    result = forecast_retirement(_inputs(), start_date=date(2026, 8, 30))
    base = next(row for row in result["scenarios"] if row["key"] == "base")

    assert base["depletion_months"] == 12
    assert base["depletion_date"] == "2027-08-30"
    assert base["ending_balance"] == 0
    assert len(base["balances"]) == 13


def test_inflation_converts_nominal_growth_to_real_growth():
    result = forecast_retirement(
        _inputs(nominal_return=0.05, inflation_rate=0.02),
        start_date=date(2026, 1, 1),
    )
    base = next(row for row in result["scenarios"] if row["key"] == "base")

    assert base["real_return"] == pytest.approx((1.05 / 1.02) - 1)


def test_tax_grosses_up_only_the_after_tax_spending_gap():
    result = forecast_retirement(_inputs(
        starting_portfolio=1000,
        annual_spending=180,
        annual_retirement_income=100,
        effective_tax_rate=0.20,
    ))

    assert result["summary"]["net_spending_gap"] == 80
    assert result["summary"]["gross_annual_draw"] == 100
    assert result["summary"]["initial_withdrawal_rate"] == pytest.approx(0.10)


def test_base_retirement_income_applies_alongside_dated_income_streams():
    result = forecast_retirement(
        _inputs(
            starting_portfolio=1000,
            annual_spending=180,
            annual_retirement_income=50,
            effective_tax_rate=0.0,
            retirement_start_date="2026-01-01",
            income_streams=[{
                "name": "CPP estimate", "annual_amount": 30,
                "start_date": "2026-01-01", "end_date": None,
            }],
        ),
        start_date=date(2026, 1, 1),
    )

    assert result["summary"]["net_spending_gap"] == 100
    assert result["summary"]["gross_annual_draw"] == 100


def test_delayed_income_stream_starts_on_its_date_in_both_forecasts():
    result = forecast_retirement(
        _inputs(
            starting_portfolio=1200,
            annual_spending=1200,
            annual_retirement_income=0,
            annual_volatility=0,
            retirement_start_date="2026-01-01",
            date_of_birth="1960-01-01",
            plan_through_age=67,
            income_streams=[{
                "name": "Pension", "annual_amount": 1200,
                "start_date": "2026-07-01", "end_date": None,
            }],
        ),
        start_date=date(2026, 1, 1),
    )
    base = next(row for row in result["scenarios"] if row["key"] == "base")

    assert result["summary"]["net_spending_gap"] == 1200
    assert base["ending_balance"] == pytest.approx(600)
    assert result["probabilistic"]["ending_balance_percentiles"]["p50"] == pytest.approx(600)


@pytest.mark.parametrize(
    "cpp_start,oas_start,expected_gap",
    [
        ("2026-01-01", "2026-01-01", 36000),
        ("2026-01-01", "2026-07-01", 44000),
        ("2026-07-01", "2027-01-01", 56000),
        ("2025-01-01", "2025-01-01", 36000),
    ],
)
def test_starting_withdrawal_includes_only_cpp_oas_already_payable(
    cpp_start, oas_start, expected_gap
):
    result = forecast_retirement(
        _inputs(
            starting_portfolio=1000000,
            annual_spending=60000,
            annual_retirement_income=4000,
            annual_volatility=0,
            effective_tax_rate=0.2,
            retirement_start_date="2026-01-01",
            date_of_birth="1960-01-01",
            plan_through_age=67,
            income_streams=[
                {"name": "CPP estimate", "annual_amount": 12000,
                 "start_date": cpp_start, "end_date": None},
                {"name": "OAS estimate", "annual_amount": 8000,
                 "start_date": oas_start, "end_date": None},
            ],
        ),
        start_date=date(2026, 1, 1),
    )
    assert result["summary"]["annual_spending_at_retirement"] == 60000
    assert result["summary"]["annual_income_at_retirement"] == 60000 - expected_gap
    assert result["summary"]["net_spending_gap"] == expected_gap
    assert result["summary"]["gross_annual_draw"] == pytest.approx(expected_gap / 0.8)
    assert result["summary"]["initial_withdrawal_rate"] == pytest.approx(
        expected_gap / 0.8 / 1000000
    )


def test_income_covering_spending_has_no_withdrawal_or_depletion():
    result = forecast_retirement(_inputs(
        starting_portfolio=1000,
        annual_spending=100,
        annual_retirement_income=125,
        nominal_return=0.05,
        inflation_rate=0.02,
    ))

    assert result["summary"]["gross_annual_draw"] == 0
    assert all(row["depletion_months"] is None for row in result["scenarios"])
    assert all(len(row["balances"]) == 721 for row in result["scenarios"])


def test_delayed_retirement_grows_portfolio_before_withdrawals_begin():
    result = forecast_retirement(
        _inputs(
            starting_portfolio=1000,
            annual_spending=120,
            nominal_return=0.12,
            retirement_start_date="2027-01-01",
        ),
        start_date=date(2026, 1, 1),
    )
    base = next(row for row in result["scenarios"] if row["key"] == "base")

    assert result["retirement_start_date"] == "2027-01-01"
    assert result["delay_years"] == pytest.approx(365 / 365.2425)
    assert base["retirement_start_balance"] == pytest.approx(
        1000 * 1.12 ** (365 / 365.2425)
    )
    retirement_point = next(
        row for row in base["balances"] if row["date"] == "2027-01-01"
    )
    assert retirement_point["value_cad"] == pytest.approx(base["retirement_start_balance"])
    assert result["summary"]["initial_withdrawal_rate"] == pytest.approx(
        120 / base["retirement_start_balance"]
    )


@pytest.mark.parametrize("retirement_start_date", ["2025-12-31", "not-a-date", 20270101])
def test_invalid_retirement_start_dates_are_rejected(retirement_start_date):
    with pytest.raises(ValueError, match="retirement_start_date"):
        forecast_retirement(
            _inputs(retirement_start_date=retirement_start_date),
            start_date=date(2026, 1, 1),
        )


def test_retirement_start_date_is_limited_to_sixty_year_delay():
    with pytest.raises(ValueError, match="more than 60 years"):
        forecast_retirement(
            _inputs(retirement_start_date="2087-01-01"),
            start_date=date(2026, 1, 1),
        )


def test_scenarios_are_ordered_and_use_two_point_spread():
    result = forecast_retirement(_inputs(nominal_return=0.05, inflation_rate=0.02))

    assert [row["key"] for row in result["scenarios"]] == [
        "conservative", "base", "optimistic"
    ]
    assert [row["nominal_return"] for row in result["scenarios"]] == pytest.approx([
        0.03, 0.05, 0.07
    ])
    assert result["scenarios"][0]["depletion_months"] <= result["scenarios"][1]["depletion_months"]
    assert result["scenarios"][1]["depletion_months"] <= result["scenarios"][2]["depletion_months"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("starting_portfolio", -1),
        ("annual_spending", -1),
        ("annual_retirement_income", -1),
        ("inflation_rate", -0.01),
        ("effective_tax_rate", 1),
        ("nominal_return", -0.98),
    ],
)
def test_invalid_inputs_are_rejected(field, value):
    with pytest.raises(ValueError):
        forecast_retirement(_inputs(**{field: value}))


def test_missing_and_non_finite_inputs_are_rejected():
    values = _inputs()
    del values["annual_spending"]
    with pytest.raises(ValueError, match="annual_spending is required"):
        forecast_retirement(values)

    with pytest.raises(ValueError, match="must be finite"):
        forecast_retirement(_inputs(annual_spending=float("inf")))


def test_monte_carlo_is_reproducible_and_returns_ordered_percentiles():
    inputs = _inputs(
        starting_portfolio=250_000,
        annual_spending=24_000,
        nominal_return=0.05,
        inflation_rate=0.02,
        annual_volatility=0.12,
        date_of_birth="1960-01-01",
        retirement_start_date="2026-01-01",
        plan_through_age=70,
    )

    first = forecast_retirement(inputs, start_date=date(2026, 1, 1))
    second = forecast_retirement(inputs, start_date=date(2026, 1, 1))

    assert first["probabilistic"] == second["probabilistic"]
    probabilistic = first["probabilistic"]
    assert probabilistic["simulations"] == 10_000
    assert probabilistic["seed"] == 20260831
    assert 0 <= probabilistic["success_probability"] <= 1
    for point in probabilistic["balance_percentiles"]:
        assert point["p10"] <= point["p50"] <= point["p90"]
    ending = probabilistic["ending_balance_percentiles"]
    assert ending["p10"] <= ending["p50"] <= ending["p90"]


def test_contributions_stop_at_retirement():
    result = forecast_retirement(
        _inputs(
            starting_portfolio=0,
            annual_spending=0,
            annual_contribution=1200,
            annual_volatility=0,
            retirement_start_date="2027-01-01",
            date_of_birth="1980-01-01",
            plan_through_age=48,
        ),
        start_date=date(2026, 1, 1),
    )
    base = next(row for row in result["scenarios"] if row["key"] == "base")

    assert base["retirement_start_balance"] == pytest.approx(1200)
    assert base["ending_balance"] == pytest.approx(1200)
    assert result["probabilistic"]["ending_balance_percentiles"]["p50"] == pytest.approx(1200)


def test_scheduled_cash_flows_tax_and_one_time_events_apply_in_their_month():
    result = forecast_retirement(
        _inputs(
            starting_portfolio=1000,
            annual_spending=999,
            annual_retirement_income=0,
            annual_volatility=0,
            effective_tax_rate=0.5,
            retirement_start_date="2026-01-01",
            date_of_birth="1960-01-01",
            plan_through_age=67,
            income_streams=[{
                "name": "Pension", "annual_amount": 60,
                "start_date": "2026-01-01", "end_date": None,
            }],
            spending_phases=[{
                "name": "Go-go", "annual_amount": 120,
                "start_date": "2026-01-01", "end_date": None,
            }],
            one_time_events=[{
                "name": "Vehicle", "date": "2026-02-01", "amount": -100,
            }],
        ),
        start_date=date(2026, 1, 1),
    )
    base = next(row for row in result["scenarios"] if row["key"] == "base")
    february = next(row for row in base["balances"] if row["date"] == "2026-02-01")

    assert result["summary"]["net_spending_gap"] == 60
    assert result["summary"]["gross_annual_draw"] == 120
    assert february["value_cad"] == pytest.approx(890)
    assert result["probabilistic"]["balance_percentiles"][1]["p50"] == pytest.approx(780)


def test_depletion_age_statistics_and_success_probability():
    result = forecast_retirement(
        _inputs(
            starting_portfolio=100,
            annual_spending=1200,
            annual_volatility=0,
            retirement_start_date="2026-01-01",
            date_of_birth="1960-01-01",
            plan_through_age=67,
        ),
        start_date=date(2026, 1, 1),
    )

    probabilistic = result["probabilistic"]
    assert probabilistic["success_probability"] == 0
    assert probabilistic["ending_balance_percentiles"] == {
        "p10": 0, "p50": 0, "p90": 0,
    }
    ages = probabilistic["depletion_age_percentiles"]
    assert ages["p10"] == pytest.approx(ages["p50"])
    assert ages["p50"] == pytest.approx(ages["p90"])
    assert 66 < ages["p50"] < 67


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"date_of_birth": "2030-01-01", "plan_through_age": 95}, "date_of_birth"),
        ({"date_of_birth": "1960-01-01", "plan_through_age": 121}, "plan_through_age"),
        ({
            "spending_phases": [
                {"annual_amount": 10, "start_date": "2026-01-01", "end_date": None},
                {"annual_amount": 20, "start_date": "2027-01-01", "end_date": None},
            ],
        }, "cannot overlap"),
        ({
            "one_time_events": [{"date": "2025-12-01", "amount": -10}],
        }, "within the forecast"),
    ],
)
def test_invalid_probabilistic_and_schedule_inputs_are_rejected(overrides, message):
    with pytest.raises(ValueError, match=message):
        forecast_retirement(_inputs(**overrides), start_date=date(2026, 1, 1))
