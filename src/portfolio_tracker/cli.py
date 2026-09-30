"""Entry point: orchestrates the full pipeline against a workbook."""
from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from portfolio_tracker.config import read_config
from portfolio_tracker.data.cache import Cache
from portfolio_tracker.data.lseg import IdentifierMap, LSEGDataSource, ProviderChain
from portfolio_tracker.data.yahoo import YahooDataSource
from portfolio_tracker.exposure import (
    allocation_matched_benchmark_blend,
    benchmark_role_label,
    compute_exposure,
)
from portfolio_tracker.fx import convert_prices_to_cad, fx_series_to_cad
from portfolio_tracker.io_csv import read_wealthsimple_csv
from portfolio_tracker.io_excel import (
    ReportTables,
    read_instrument_map,
    read_price_overrides,
    write_reports,
)
from portfolio_tracker.portfolio import (
    daily_cash_balance_cad,
    daily_external_cash_flows_cad,
    interpolated_prices_from_transactions,
    reconstruct_daily_units,
)
from portfolio_tracker.returns import (
    annualize_return,
    blended_benchmark_daily_returns,
    cash_flow_matched_values,
    daily_portfolio_value_cad,
    daily_twr,
    money_weighted_return,
    period_twr,
)
from portfolio_tracker.risk import compute_risk
from portfolio_tracker.acb import compute_acb


_HORIZONS = [
    ("YTD", "ytd"),
    ("1Y", 1),
    ("3Y", 3),
    ("5Y", 5),
    ("Since-inception", "inception"),
]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:]) if argv is None else list(argv)
    return _run_main(argv)


def _run_main(argv: list[str]) -> int:
    args = _parse_args(argv)
    workbook_path = Path(args.workbook)
    if not workbook_path.exists():
        print(f"Workbook not found: {workbook_path}", file=sys.stderr)
        return 1

    cfg = read_config(workbook_path)
    price_overrides = read_price_overrides(workbook_path)
    identifiers = IdentifierMap(read_instrument_map(workbook_path))

    transactions, txn_warnings, txn_source = _load_transactions(args, workbook_path)
    print(f"Loaded {len(transactions)} transactions from {txn_source}")
    if txn_warnings:
        print(f"  ({len(txn_warnings)} rows skipped — see Report_Meta)")

    cache = None if args.no_cache else Cache(Path(args.cache_path))
    yahoo = YahooDataSource(cache=cache, classification_ttl_days=cfg.cache_ttl_classifications_days)
    lseg = LSEGDataSource(
        identifiers=identifiers,
        cache=cache,
        classification_ttl_days=cfg.cache_ttl_classifications_days,
        refresh_funds=args.refresh_lseg,
    )
    source = ProviderChain(primary=lseg, fallback=yahoo, identifiers=identifiers)

    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    first_transaction_date = _first_transaction_date(transactions)
    start = _effective_inception_date(cfg.inception_date, first_transaction_date)
    if start != cfg.inception_date:
        print(
            f"Configured inception {cfg.inception_date} precedes the first transaction; "
            f"using {start}."
        )
    print(f"Computing portfolio from {start} to {as_of}...")

    fx_callables = _build_fx_callables(transactions, source, start, as_of)

    units = reconstruct_daily_units(transactions, start, as_of)
    ext_cf = daily_external_cash_flows_cad(transactions, start, as_of, fx_callables)
    cash_bal = daily_cash_balance_cad(transactions, start, as_of, fx_callables)

    tickers_held = list(units.columns)
    print(f"Tickers held: {len(tickers_held)}")

    priceable_tickers, unpriceable, ticker_currencies = _classify_tickers(
        tickers_held, source, price_overrides
    )

    prices_native = (
        source.get_prices(priceable_tickers, start, as_of) if priceable_tickers else pd.DataFrame()
    )
    if not prices_native.empty:
        prices_native = prices_native.reindex(units.index).ffill()
        prices_cad = convert_prices_to_cad(prices_native, ticker_currencies, source)
    else:
        prices_cad = pd.DataFrame(index=units.index)

    prices_cad = _apply_price_overrides(prices_cad, units.index, price_overrides)

    interpolated_prices = interpolated_prices_from_transactions(
        transactions, unpriceable, start, as_of, fx_callables
    )
    for ticker in interpolated_prices.columns:
        prices_cad[ticker] = interpolated_prices[ticker]
    if not interpolated_prices.empty:
        print(f"  Interpolated price from own transaction history for: {sorted(interpolated_prices.columns)}")
    still_unpriceable = sorted(t for t in unpriceable if t not in interpolated_prices.columns)
    if still_unpriceable:
        print(f"  No provider price, override, or transaction history for: {still_unpriceable}")

    common = [c for c in prices_cad.columns if c in units.columns]
    pv = daily_portfolio_value_cad(units[common], prices_cad[common], cash_bal)
    twr_daily = daily_twr(pv, ext_cf)

    current_positions = _current_position_values(units, prices_cad)
    benchmark_blend = allocation_matched_benchmark_blend(
        cfg.benchmark_blend, current_positions, source
    )
    print(
        "Allocation-matched benchmark: "
        + ", ".join(f"{ticker} {weight:.2%}" for ticker, weight in benchmark_blend)
    )
    blend_prices = _fetch_blend_prices(benchmark_blend, source, start, as_of)
    bench_daily = blended_benchmark_daily_returns(benchmark_blend, blend_prices)
    benchmark_values = cash_flow_matched_values(bench_daily, ext_cf)

    perf = _build_performance_table(twr_daily, bench_daily, start, as_of, pv, ext_cf)
    current_value = float(pv.iloc[-1])
    risk = compute_risk(twr_daily, bench_daily, cfg.risk_free_rate, current_value)

    positions_table = _build_positions_table(units, prices_native, prices_cad, ticker_currencies, current_value)
    geo, sec, ac = _build_exposure_tables(units, prices_cad, float(cash_bal.iloc[-1]), source)

    tables = ReportTables(
        positions=positions_table,
        geography=geo,
        sector=sec,
        asset_class=ac,
        performance=perf,
        risk=_risk_to_df(risk),
        meta=_build_meta(
            as_of, cfg, benchmark_blend, start, first_transaction_date, prices_native,
            txn_source, txn_warnings, still_unpriceable, sorted(interpolated_prices.columns), source,
        ),
    )
    write_reports(workbook_path, tables)
    print(f"Wrote reports to {workbook_path}")

    acb_data = compute_acb(transactions, fx_callables)
    account_positions = _build_account_positions_table(
        transactions, positions_table, start, as_of, current_value
    )
    account_acb_data = _compute_account_acb(transactions, fx_callables)
    account_cash = _build_account_cash_balances(
        transactions, fx_callables, start, as_of
    )
    account_breakdown = _build_account_breakdown(account_positions, account_cash)
    payload = _build_dashboard_payload(
        positions_table, acb_data, pv, ext_cf, benchmark_values, twr_daily,
        bench_daily, perf, risk,
        (geo, sec, ac), account_breakdown, account_positions, account_acb_data,
        benchmark_blend, cfg.risk_free_rate, source, as_of,
        total_cash=float(cash_bal.iloc[-1]), account_cash=account_cash,
    )
    payload_path = Path(args.payload_out)
    _write_payload_cache(payload, payload_path)
    print(f"Dashboard payload cached -> {payload_path}")

    source.close()
    if cache:
        cache.close()
    return 0


def _parse_args(argv):
    parser = argparse.ArgumentParser(prog="portfolio-tracker")
    parser.add_argument("--workbook", default="portfolio.xlsx", help="Path to the portfolio workbook")
    parser.add_argument("--transactions-csv", help="Wealthsimple activities CSV (overrides auto-detect)")
    parser.add_argument("--cache-path", default="data/cache.sqlite", help="SQLite cache path")
    parser.add_argument("--no-cache", action="store_true", help="Bypass and overwrite the cache")
    parser.add_argument("--as-of", help="ISO date to use as the end-of-period (default: today)")
    parser.add_argument("--refresh-lseg", action="store_true",
                        help="Bypass today's cached Lipper allocation snapshots")
    parser.add_argument("--payload-out", default="data/portfolio_payload.json",
                        help="Where to write the cached dashboard payload JSON (read by finance-hub)")
    return parser.parse_args(argv)


def _write_payload_cache(payload: dict, path: Path) -> None:
    """Write the dashboard payload as JSON, wrapped with a generation timestamp.

    finance_hub.portfolio_api reads this file to serve the Portfolio view
    without re-running the pipeline (slow, and dependent on LSEG Workspace
    being open) on every request.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "payload": payload,
    }
    path.write_text(json.dumps(document, default=str, ensure_ascii=False), encoding="utf-8")


def _load_transactions(args, workbook_path):
    """Auto-detect: --transactions-csv flag > data/activities-export-*.csv."""
    if args.transactions_csv:
        df, w = read_wealthsimple_csv(args.transactions_csv)
        return df, w, args.transactions_csv

    candidates = sorted(glob.glob("data/activities-export-*.csv"))
    if candidates:
        path = candidates[-1]
        df, w = read_wealthsimple_csv(path)
        return df, w, path

    raise FileNotFoundError(
        "No transactions CSV found. Place an activities-export-*.csv in data/ "
        "or pass --transactions-csv PATH."
    )


def _first_transaction_date(transactions: pd.DataFrame) -> date | None:
    if transactions.empty:
        return None
    return pd.to_datetime(transactions["date"]).min().date()


def _effective_inception_date(
    configured_inception: date,
    first_transaction_date: date | None,
) -> date:
    """Prevent the reporting period from starting before activity exists."""
    if first_transaction_date is None:
        return configured_inception
    return max(configured_inception, first_transaction_date)


def _classify_tickers(tickers, source, price_overrides):
    """Return (priceable_via_provider_chain, unpriceable, currency_map).

    Tickers in the override sheet are not fetched (override wins).
    Tickers neither provider can price are excluded with a warning.
    """
    priceable = []
    unpriceable = []
    currencies = {}
    probe_end = date.today()
    probe_start = probe_end - timedelta(days=14)
    for t in tickers:
        if t in price_overrides:
            currencies[t] = price_overrides[t]["currency"]
            continue
        try:
            df = source.get_prices([t], probe_start, probe_end)
            if df.empty or df[t].dropna().empty:
                unpriceable.append(t)
                continue
            cls = source.get_classification(t)
            currencies[t] = cls.get("currency") or "USD"
            priceable.append(t)
        except Exception:
            unpriceable.append(t)
    return priceable, unpriceable, currencies


def _apply_price_overrides(prices_cad, index, price_overrides):
    """Inject override-priced tickers as constant series across the index."""
    out = prices_cad.copy()
    for ticker, info in price_overrides.items():
        # Override price is in 'currency'; for CAD it's already CAD. Non-CAD overrides
        # would require FX conversion — keep v1 simple by requiring overrides in CAD.
        if info["currency"] != "CAD":
            continue
        out[ticker] = pd.Series(info["price"], index=index)
    return out


def _build_fx_callables(transactions, source, start, end):
    needed = set(transactions["currency"].dropna().unique()) | {"CAD"}
    out = {}
    for ccy in needed:
        if ccy == "CAD":
            out[ccy] = lambda _ts: 1.0
        else:
            series = fx_series_to_cad(ccy, start, end, source)
            series = series.reindex(pd.date_range(start, end, freq="D")).ffill().bfill()
            out[ccy] = _make_fx_lookup(series)
    return out


def _make_fx_lookup(series):
    def lookup(ts):
        ts = pd.Timestamp(ts)
        if ts in series.index:
            return float(series.loc[ts])
        idx = series.index.searchsorted(ts)
        idx = min(max(idx, 0), len(series) - 1)
        return float(series.iloc[idx])
    return lookup


def _fetch_blend_prices(blend, source, start, end):
    tickers = [t for t, _ in blend]
    prices = source.get_prices(tickers, start, end)
    ticker_currencies = {t: source.get_classification(t).get("currency") or "USD" for t in tickers}
    prices_cad = convert_prices_to_cad(prices, ticker_currencies, source)
    return {t: prices_cad[t].dropna() for t in tickers}


def _build_performance_table(
    portfolio_daily_twr, benchmark_daily_twr, inception, as_of, pv=None, external_flows=None
):
    """Build comparable TWR horizon rates, plus a money-weighted portfolio figure.

    YTD is conventionally shown as a cumulative return. Horizons of one year
    or longer are annualized using actual calendar-day length. TWR drives the
    benchmark comparison since the blend has no cash-flow timing of its own;
    "Portfolio Return" is money-weighted (XIRR) and reflects the return on the
    investor's actual dollars, including contribution/withdrawal timing. It is
    only computed when ``pv``/``external_flows`` are supplied.
    """
    today = pd.Timestamp(as_of)
    rows = []
    for label, spec in _HORIZONS:
        if spec == "ytd":
            start = pd.Timestamp(date(as_of.year, 1, 1))
        elif spec == "inception":
            start = pd.Timestamp(inception)
        else:
            start = today - pd.DateOffset(years=int(spec))
        start = max(start, pd.Timestamp(inception))
        if start >= today:
            continue
        # A horizon runs from the closing value on ``start`` through the
        # closing value on ``as_of``; the return stamped on ``start`` belongs
        # to the preceding interval and must not be included.
        first_return_date = start.date() + timedelta(days=1)
        p_period = period_twr(portfolio_daily_twr, first_return_date, as_of)
        b_period = period_twr(benchmark_daily_twr, first_return_date, as_of)
        days = int((today - start).days)
        is_annualized = label != "YTD" and days >= 365
        p_ret = annualize_return(p_period, days) if is_annualized else p_period
        b_ret = annualize_return(b_period, days) if is_annualized else b_period

        mwr_ret = float("nan")
        if pv is not None and external_flows is not None:
            mwr_period, mwr_annualized = money_weighted_return(
                pv, external_flows, start.date(), as_of
            )
            mwr_ret = mwr_annualized if is_annualized else mwr_period

        rows.append({
            "Horizon": label,
            "Return Basis": "Annualized" if is_annualized else "Cumulative",
            "Portfolio TWR": p_ret,
            "Benchmark TWR": b_ret,
            "Difference": p_ret - b_ret,
            "Portfolio Return": mwr_ret,
        })
    return pd.DataFrame(rows)


_UNITS_EPSILON = 1e-6  # ignore ghost positions from float drift after closing trades
_CASH_EPSILON = 0.005  # ignore sub-cent cash noise


def _cash_position_record(cash_value: float, total_value: float, account_type: str | None = None) -> dict | None:
    """Represent an un-invested cash balance as a pseudo-position.

    Without this, Holdings (securities only) doesn't sum to the headline total,
    which includes cash. Priced at $1/unit so it has zero accrued gain.
    """
    if abs(cash_value) < _CASH_EPSILON:
        return None
    record = {
        "ticker": "CASH",
        "name": "Cash",
        "units": cash_value,
        "price_cad": 1.0,
        "value_cad": cash_value,
        "weight": cash_value / total_value if total_value else 0.0,
        "acb_per_unit": 1.0,
        "total_acb": cash_value,
        "accrued_gain_cad": 0.0,
        "accrued_gain_pct": 0.0,
    }
    if account_type is not None:
        record["account_type"] = account_type
    return record


def _build_positions_table(units, prices_native, prices_cad, ticker_currencies, total_value):
    if units.empty or len(units.columns) == 0:
        return pd.DataFrame(columns=["ticker", "units", "last_price_native", "currency", "value_cad", "weight"])
    last_units = units.iloc[-1]
    last_native = prices_native.iloc[-1] if not prices_native.empty else pd.Series()
    last_cad = prices_cad.iloc[-1] if not prices_cad.empty else pd.Series()
    rows = []
    for ticker in units.columns:
        u = float(last_units[ticker])
        if abs(u) < _UNITS_EPSILON:
            continue
        pc = float(last_cad.get(ticker, float("nan"))) if not last_cad.empty else float("nan")
        if pd.isna(pc):
            # Unidentifiable: Yahoo can't price and no override — skip entirely.
            # Surfaced in Report_Meta.skipped_unidentifiable instead.
            continue
        pn = float(last_native.get(ticker, float("nan"))) if not last_native.empty else float("nan")
        value = u * pc
        rows.append({
            "ticker": ticker,
            "units": u,
            "last_price_native": pn,
            "currency": ticker_currencies.get(ticker, ""),
            "value_cad": value,
            "weight": value / total_value if total_value > 0 else 0.0,
        })
    return pd.DataFrame(rows).sort_values("value_cad", ascending=False).reset_index(drop=True)


def _build_exposure_tables(units, prices_cad, cash_cad, source):
    positions = _current_position_values(units, prices_cad)
    try:
        exp = compute_exposure(positions, cash_cad=cash_cad, source=source)
    except KeyError:
        # Some held tickers lack classification — fall back to no exposure breakdown
        return pd.DataFrame(columns=["bucket", "weight"]), pd.DataFrame(columns=["bucket", "weight"]), pd.DataFrame(columns=["bucket", "weight"])

    def _to_df(d):
        if not d:
            return pd.DataFrame(columns=["bucket", "weight"])
        return pd.DataFrame(
            sorted(d.items(), key=lambda kv: kv[1], reverse=True),
            columns=["bucket", "weight"],
        )

    return _to_df(exp.geography), _to_df(exp.sector), _to_df(exp.asset_class)


def _current_position_values(units, prices_cad):
    """Return current identifiable security values, excluding closed/unpriced holdings."""
    if units.empty or len(units.columns) == 0:
        return {}
    last_units = units.iloc[-1]
    last_cad = prices_cad.iloc[-1] if not prices_cad.empty else pd.Series()
    return {
        ticker: float(last_units[ticker]) * float(last_cad[ticker])
        for ticker in units.columns
        if ticker in prices_cad.columns
        and pd.notna(last_cad.get(ticker))
        and abs(float(last_units[ticker])) >= _UNITS_EPSILON
    }


def _account_type(account: object) -> str:
    value = str(account or "").split("|", 1)[0].strip()
    return value or "Unknown"


def _build_account_positions_table(
    transactions: pd.DataFrame,
    positions: pd.DataFrame,
    start: date,
    end: date,
    total_value: float,
) -> pd.DataFrame:
    """Current priced security positions split by account type and ticker."""
    columns = ["account_type", "ticker", "units", "price_cad", "value_cad", "weight"]
    if transactions.empty or positions.empty:
        return pd.DataFrame(columns=columns)

    trades = transactions[transactions["type"].isin({"BUY", "SELL"})].copy()
    trades["date"] = pd.to_datetime(trades["date"])
    trades = trades[
        (trades["date"] >= pd.Timestamp(start))
        & (trades["date"] <= pd.Timestamp(end))
    ]
    if trades.empty:
        return pd.DataFrame(columns=columns)

    trades["account_type"] = trades["account"].map(_account_type)
    account_units = (
        trades.groupby(["account_type", "ticker"], as_index=False)["units"].sum()
    )
    priced = positions.set_index("ticker")
    rows = []
    for _, row in account_units.iterrows():
        ticker = str(row["ticker"])
        units = float(row["units"])
        if abs(units) < _UNITS_EPSILON or ticker not in priced.index:
            continue
        aggregate = priced.loc[ticker]
        aggregate_units = float(aggregate["units"])
        if abs(aggregate_units) < _UNITS_EPSILON:
            continue
        price_cad = float(aggregate["value_cad"]) / aggregate_units
        value_cad = units * price_cad
        rows.append({
            "account_type": str(row["account_type"]),
            "ticker": ticker,
            "units": units,
            "price_cad": price_cad,
            "value_cad": value_cad,
            "weight": value_cad / total_value if total_value > 0 else 0.0,
        })
    return pd.DataFrame(rows, columns=columns)


def _compute_account_acb(transactions, fx_callables) -> dict[str, dict]:
    """Compute ticker ACB independently within each account type."""
    if transactions.empty:
        return {}
    txns = transactions.copy()
    txns["account_type"] = txns["account"].map(_account_type)
    return {
        account_type: compute_acb(group, fx_callables)
        for account_type, group in txns.groupby("account_type")
    }


def _build_account_cash_balances(
    transactions: pd.DataFrame,
    fx_callables,
    start: date,
    end: date,
) -> dict[str, float]:
    """Current transaction-derived cash balance in CAD by account type."""
    if transactions.empty:
        return {}
    txns = transactions.copy()
    txns["date"] = pd.to_datetime(txns["date"])
    txns = txns[
        (txns["date"] >= pd.Timestamp(start))
        & (txns["date"] <= pd.Timestamp(end))
    ]
    if txns.empty:
        return {}
    txns["account_type"] = txns["account"].map(_account_type)
    txns["value_cad"] = [
        float(row["cash_native"]) * fx_callables[row["currency"]](row["date"])
        for _, row in txns.iterrows()
    ]
    return txns.groupby("account_type")["value_cad"].sum().to_dict()


def _build_account_breakdown(
    account_positions: pd.DataFrame,
    account_cash: dict[str, float],
) -> list[dict]:
    """Current security and cash value in CAD by account type."""
    totals = dict(account_cash)
    if not account_positions.empty:
        security_totals = account_positions.groupby("account_type")["value_cad"].sum()
        for account_type, value in security_totals.items():
            totals[account_type] = totals.get(account_type, 0.0) + float(value)

    return [{"account_type": k, "value_cad": v} for k, v in sorted(totals.items())]


def _build_dashboard_payload(
    positions_df: pd.DataFrame,
    acb_data: dict,
    pv: pd.Series,
    external_flows: pd.Series,
    benchmark_values: pd.Series,
    twr_daily: pd.Series,
    bench_daily: pd.Series,
    perf: pd.DataFrame,
    risk,
    exposure: tuple,
    account_breakdown: list,
    account_positions_df: pd.DataFrame,
    account_acb_data: dict,
    benchmark_blend: list[tuple[str, float]],
    risk_free_rate: float,
    source,
    as_of,
    total_cash: float,
    account_cash: dict[str, float],
) -> dict:
    """Assemble the payload dict consumed by generate_html."""
    geo_df, sec_df, ac_df = exposure
    total_value_cad = float(pv.iloc[-1])

    names: dict[str, str] = {}

    def position_records(df, acb_by_ticker, include_account=False):
        records = []
        for _, row in df.iterrows():
            ticker = str(row["ticker"])
            acb = acb_by_ticker.get(ticker, {})
            total_acb = float(acb.get("total_acb", 0.0))
            value = float(row["value_cad"])
            gain = value - total_acb
            gain_pct = gain / total_acb if total_acb > 0 else 0.0
            units_held = float(row["units"])
            price_cad = value / units_held if abs(units_held) > 1e-9 else 0.0

            if ticker not in names:
                try:
                    cls = source.get_classification(ticker)
                    names[ticker] = cls.get("long_name") or ticker
                except Exception:
                    names[ticker] = ticker

            record = {
                "ticker": ticker,
                "name": names[ticker],
                "units": units_held,
                "price_cad": price_cad,
                "value_cad": value,
                "weight": float(row["weight"]),
                "acb_per_unit": float(acb.get("acb_per_unit", 0.0)),
                "total_acb": total_acb,
                "accrued_gain_cad": gain,
                "accrued_gain_pct": gain_pct,
            }
            if include_account:
                record["account_type"] = str(row["account_type"])
            records.append(record)
        return records

    positions = position_records(positions_df, acb_data)
    cash_record = _cash_position_record(total_cash, total_value_cad)
    if cash_record:
        positions.append(cash_record)

    account_positions = []
    if not account_positions_df.empty:
        for account_type, group in account_positions_df.groupby("account_type"):
            account_positions.extend(position_records(
                group,
                account_acb_data.get(str(account_type), {}),
                include_account=True,
            ))
    for account_type, cash_value in sorted(account_cash.items()):
        account_cash_record = _cash_position_record(
            cash_value, total_value_cad, account_type=str(account_type)
        )
        if account_cash_record:
            account_positions.append(account_cash_record)

    total_cost_base = sum(p["total_acb"] for p in positions)

    pv_clean = pv.dropna()
    daily_values = [
        {"date": str(ts.date()), "value_cad": float(v)}
        for ts, v in pv_clean.items()
    ]

    benchmark_values_clean = benchmark_values.dropna()
    daily_benchmark_values = [
        {"date": str(ts.date()), "value_cad": float(v)}
        for ts, v in benchmark_values_clean.items()
    ]
    external_flow_rows = [
        {"date": str(ts.date()), "flow_cad": float(v)}
        for ts, v in external_flows.items()
        if pd.notna(v) and abs(float(v)) > 1e-12
    ]

    aligned_returns = pd.concat(
        [twr_daily.rename("portfolio"), bench_daily.rename("benchmark")],
        axis=1,
    ).sort_index()
    daily_returns = [
        {
            "date": str(ts.date()),
            "portfolio": float(row["portfolio"]) if pd.notna(row["portfolio"]) else None,
            "benchmark": float(row["benchmark"]) if pd.notna(row["benchmark"]) else None,
        }
        for ts, row in aligned_returns.iterrows()
    ]

    def _df_to_list(df):
        if df.empty:
            return []
        return [{"bucket": str(r["bucket"]), "weight": float(r["weight"])} for _, r in df.iterrows()]

    return {
        "as_of_date": str(as_of),
        "total_value_cad": total_value_cad,
        "total_cost_base": total_cost_base,
        "positions": positions,
        "account_positions": account_positions,
        "daily_values": daily_values,
        "benchmark_values": daily_benchmark_values,
        "external_flows": external_flow_rows,
        "daily_returns": daily_returns,
        "performance": perf.to_dict("records") if not perf.empty else [],
        "exposure": {
            "geography": _df_to_list(geo_df),
            "sector": _df_to_list(sec_df),
            "asset_class": _df_to_list(ac_df),
        },
        "account_breakdown": account_breakdown,
        "benchmark": {
            "blend": [
                {
                    "ticker": str(ticker),
                    "weight": float(weight),
                    "label": benchmark_role_label(str(ticker)),
                }
                for ticker, weight in benchmark_blend
            ],
            "method": "daily-rebalanced allocation-matched blend compared using time-weighted returns",
            "comparability_notes": [
                "Blend weights reflect the portfolio's current identifiable allocation, not its historical allocation.",
            ],
        },
        "risk": {
            "Annualized Volatility": risk.stdev,
            "VaR 95% ($)": risk.var_95_dollar,
            "VaR 95% (%)": risk.var_95_pct,
            "VaR 99% ($)": risk.var_99_dollar,
            "VaR 99% (%)": risk.var_99_pct,
            "Probability of Loss": risk.prob_loss,
            "Sharpe": risk.sharpe,
            "Beta": risk.beta,
            "Correlation": risk.correlation,
            "Information Ratio": risk.information_ratio,
            "Treynor": risk.treynor,
            "Jensen's Alpha": risk.jensens_alpha,
            "Risk-Free Rate": float(risk_free_rate),
            "Observations Method": "daily TWR; 252 trading-day annualization",
        },
    }


def _risk_to_df(risk):
    return pd.DataFrame([
        ("StDev (annualized)", risk.stdev),
        ("VaR 95% ($)", risk.var_95_dollar),
        ("VaR 95% (%)", risk.var_95_pct),
        ("VaR 99% ($)", risk.var_99_dollar),
        ("VaR 99% (%)", risk.var_99_pct),
        ("Probability of Loss", risk.prob_loss),
        ("Sharpe", risk.sharpe),
        ("Beta", risk.beta),
        ("Correlation", risk.correlation),
        ("Information Ratio", risk.information_ratio),
        ("Treynor", risk.treynor),
        ("Jensen's Alpha", risk.jensens_alpha),
    ], columns=["metric", "value"])


def _build_meta(
    as_of,
    cfg,
    benchmark_blend,
    effective_inception,
    first_transaction_date,
    prices_native,
    txn_source,
    txn_warnings,
    unpriceable,
    interpolated_tickers,
    source,
):
    last_data_date = prices_native.index.max() if not prices_native.empty else None
    provider_warnings = list(source.warnings) + list(getattr(source.primary, "warnings", []))
    meta = {
        "run_timestamp": pd.Timestamp.now().isoformat(timespec="seconds"),
        "as_of_date": str(as_of),
        "data_through_date": str(last_data_date.date()) if last_data_date is not None else "n/a",
        "base_currency": cfg.base_currency,
        "risk_free_rate": cfg.risk_free_rate,
        "inception_date": str(effective_inception),
        "configured_inception_date": str(cfg.inception_date),
        "first_transaction_date": (
            str(first_transaction_date) if first_transaction_date is not None else "n/a"
        ),
        "benchmark_blend": ", ".join(f"{t}:{w:.4f}" for t, w in benchmark_blend),
        "performance_return_method": "time-weighted return; annualized for horizons of one year or longer",
        "summary_card_return_method": "money-weighted return (XIRR); annualized for horizons of one year or longer",
        "benchmark_return_method": "daily time-weighted return; external cash flows excluded",
        "risk_return_method": "daily time-weighted return",
        "transactions_source": txn_source,
        "transactions_warnings_count": len(txn_warnings),
        "skipped_unidentifiable": ", ".join(sorted(unpriceable)) if unpriceable else "(none)",
        "interpolated_from_transactions": ", ".join(interpolated_tickers) if interpolated_tickers else "(none)",
        "market_data_policy": "lseg primary; yahoo per-instrument fallback",
        "provider_provenance": "; ".join(f"{k}={v}" for k, v in sorted(source.provenance.items())),
        "provider_warnings_count": len(provider_warnings),
    }
    # Surface the first N warnings inline
    for i, w in enumerate(txn_warnings[:20], start=1):
        meta[f"warning_{i}"] = w
    offset = min(len(txn_warnings), 20)
    for i, w in enumerate(provider_warnings[:50], start=offset + 1):
        meta[f"warning_{i}"] = w
    return meta


if __name__ == "__main__":
    sys.exit(main())
