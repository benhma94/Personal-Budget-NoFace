"""Risk suite: StDev, VaR, Sharpe, Beta, Correlation, IR, Treynor, Jensen's Alpha."""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import pandas as pd
from scipy.stats import norm


_Z_95 = 1.6448536269514722  # norm.ppf(0.95)
_Z_99 = 2.3263478740408408  # norm.ppf(0.99)


@dataclass
class RiskMetrics:
    stdev: float
    var_95_pct: float
    var_95_dollar: float
    var_99_pct: float
    var_99_dollar: float
    prob_loss: float
    sharpe: float
    beta: float
    correlation: float
    information_ratio: float
    treynor: float
    jensens_alpha: float


def compute_risk(
    portfolio_daily_returns: pd.Series,
    benchmark_daily_returns: pd.Series,
    risk_free_rate: float,
    portfolio_value_cad: float,
    trading_days_per_year: int = 252,
) -> RiskMetrics:
    """Annualized risk metrics. All inputs are daily TWR series."""
    p = portfolio_daily_returns.dropna()
    b = benchmark_daily_returns.reindex(p.index).dropna()
    p = p.reindex(b.index).dropna()

    n = trading_days_per_year
    sqrt_n = math.sqrt(n)

    mu_p_annual = p.mean() * n
    mu_b_annual = b.mean() * n
    sigma_p_annual = p.std(ddof=1) * sqrt_n
    sigma_b_annual = b.std(ddof=1) * sqrt_n

    stdev = float(sigma_p_annual)

    var_95_pct = float(mu_p_annual - _Z_95 * sigma_p_annual)
    var_99_pct = float(mu_p_annual - _Z_99 * sigma_p_annual)

    prob_loss = float(norm.cdf(-mu_p_annual / sigma_p_annual)) if sigma_p_annual > 0 else 0.0

    sharpe = float((mu_p_annual - risk_free_rate) / sigma_p_annual) if sigma_p_annual > 0 else 0.0

    cov = float(np.cov(p, b, ddof=1)[0, 1])
    var_b_daily = float(b.var(ddof=1))
    beta = cov / var_b_daily if var_b_daily > 0 else 0.0
    correlation = float(p.corr(b)) if sigma_p_annual > 0 and sigma_b_annual > 0 else 0.0

    active = p - b
    tracking_error_annual = float(active.std(ddof=1) * sqrt_n)
    if tracking_error_annual > 0:
        information_ratio = float((mu_p_annual - mu_b_annual) / tracking_error_annual)
    else:
        information_ratio = 0.0

    treynor = float((mu_p_annual - risk_free_rate) / beta) if beta != 0 else 0.0
    jensens_alpha = float(mu_p_annual - (risk_free_rate + beta * (mu_b_annual - risk_free_rate)))

    return RiskMetrics(
        stdev=stdev,
        var_95_pct=var_95_pct,
        var_95_dollar=var_95_pct * portfolio_value_cad,
        var_99_pct=var_99_pct,
        var_99_dollar=var_99_pct * portfolio_value_cad,
        prob_loss=prob_loss,
        sharpe=sharpe,
        beta=beta,
        correlation=correlation,
        information_ratio=information_ratio,
        treynor=treynor,
        jensens_alpha=jensens_alpha,
    )
