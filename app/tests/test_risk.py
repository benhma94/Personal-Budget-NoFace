"""Tests for the risk suite (StDev, VaR, Sharpe, Beta, IR, Treynor, Alpha)."""
from __future__ import annotations

import math
import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

from portfolio_tracker.risk import compute_risk, RiskMetrics


# Deterministic synthetic returns: 1 year of daily returns
def _synth(seed, mean, std, n=252):
    rng = np.random.default_rng(seed)
    return pd.Series(
        rng.normal(mean, std, n),
        index=pd.date_range("2024-01-01", periods=n, freq="D"),
    )


def test_returns_a_riskmetrics_dataclass():
    p = _synth(1, 0.0005, 0.01)
    b = _synth(2, 0.0003, 0.008)
    r = compute_risk(p, b, risk_free_rate=0.03, portfolio_value_cad=100_000)
    assert isinstance(r, RiskMetrics)


def test_stdev_matches_daily_std_annualized():
    p = _synth(1, 0.0005, 0.01)
    b = _synth(2, 0.0003, 0.008)
    r = compute_risk(p, b, risk_free_rate=0.03, portfolio_value_cad=100_000)

    expected_std = p.std(ddof=1) * math.sqrt(252)
    assert r.stdev == pytest.approx(expected_std)


def test_var_95_is_negative_under_normal_dist():
    """Parametric VaR at 95% on positive-mean returns is the 5th percentile loss."""
    p = _synth(1, 0.0005, 0.01)
    b = _synth(2, 0.0003, 0.008)
    r = compute_risk(p, b, risk_free_rate=0.03, portfolio_value_cad=100_000)

    mu_annual = p.mean() * 252
    sigma_annual = p.std(ddof=1) * math.sqrt(252)
    expected_var_pct = mu_annual - 1.645 * sigma_annual

    assert r.var_95_pct == pytest.approx(expected_var_pct, rel=1e-3)
    assert r.var_95_dollar == pytest.approx(expected_var_pct * 100_000, rel=1e-3)


def test_var_99_more_negative_than_var_95():
    p = _synth(1, 0.0005, 0.01)
    b = _synth(2, 0.0003, 0.008)
    r = compute_risk(p, b, risk_free_rate=0.03, portfolio_value_cad=100_000)

    assert r.var_99_pct < r.var_95_pct


def test_sharpe_with_known_inputs():
    """If portfolio annualized return = 10%, vol = 15%, rf = 3% → Sharpe ≈ 0.4667."""
    # Construct a series with exact annualized mean and std
    n = 252
    mu_daily = 0.10 / 252
    sigma_daily = 0.15 / math.sqrt(252)
    p = pd.Series([mu_daily] * n, index=pd.date_range("2024-01-01", periods=n, freq="D"))
    # Add noise around the mean keeping the std exact
    rng = np.random.default_rng(0)
    raw = rng.normal(0, 1, n)
    raw = (raw - raw.mean()) / raw.std(ddof=1)
    p = pd.Series(mu_daily + sigma_daily * raw, index=p.index)

    b = _synth(2, 0.0003, 0.008)
    r = compute_risk(p, b, risk_free_rate=0.03, portfolio_value_cad=100_000)

    assert r.sharpe == pytest.approx((0.10 - 0.03) / 0.15, rel=1e-2)


def test_beta_correlation_when_portfolio_equals_benchmark():
    p = _synth(1, 0.0005, 0.01)
    r = compute_risk(p, p, risk_free_rate=0.03, portfolio_value_cad=100_000)
    assert r.beta == pytest.approx(1.0)
    assert r.correlation == pytest.approx(1.0)


def test_beta_zero_when_uncorrelated():
    """Independent series should have beta ~ 0 and correlation ~ 0."""
    p = _synth(1, 0.0005, 0.01, n=1000)
    b = _synth(99, 0.0003, 0.008, n=1000)
    r = compute_risk(p, b, risk_free_rate=0.03, portfolio_value_cad=100_000)
    assert abs(r.correlation) < 0.1
    assert abs(r.beta) < 0.2


def test_information_ratio_zero_when_portfolio_equals_benchmark():
    p = _synth(1, 0.0005, 0.01)
    r = compute_risk(p, p, risk_free_rate=0.03, portfolio_value_cad=100_000)
    # mean active return is 0, tracking error is 0 → define IR as 0 (or nan handled)
    assert r.information_ratio == 0.0 or math.isnan(r.information_ratio)


def test_jensens_alpha_zero_when_portfolio_equals_benchmark():
    """If r_p == r_b, then α = r_p − [rf + β(r_b − rf)] with β=1 → α = 0."""
    p = _synth(1, 0.0005, 0.01)
    r = compute_risk(p, p, risk_free_rate=0.03, portfolio_value_cad=100_000)
    assert r.jensens_alpha == pytest.approx(0.0, abs=1e-9)


def test_prob_loss_between_zero_and_one():
    p = _synth(1, 0.0005, 0.01)
    b = _synth(2, 0.0003, 0.008)
    r = compute_risk(p, b, risk_free_rate=0.03, portfolio_value_cad=100_000)
    assert 0.0 <= r.prob_loss <= 1.0


def test_treynor_with_unit_beta():
    """With β=1 (p==b), Treynor = (μ_p − rf) / 1 = excess annualized return."""
    p = _synth(1, 0.0005, 0.01)
    r = compute_risk(p, p, risk_free_rate=0.03, portfolio_value_cad=100_000)
    expected = (p.mean() * 252) - 0.03
    assert r.treynor == pytest.approx(expected)
