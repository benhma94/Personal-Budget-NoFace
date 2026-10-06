"""Command-line entry point for the finance hub: one local app serving the
Budget, Portfolio, Retirement, and Journal views."""
from __future__ import annotations

import argparse
from pathlib import Path

from .server import run


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Personal finance hub: budget, portfolio, retirement, and "
        "journal views in one local app"
    )
    parser.add_argument("--workbook", type=Path, default=Path("data/Personal Budget.xlsx"),
                         help="workbook containing the Journal, Budget, and Ledger sheets")
    parser.add_argument("--portfolio-workbook", type=Path, default=Path("portfolio.xlsx"),
                         help="portfolio workbook used by the Portfolio tab's Refresh action")
    parser.add_argument("--portfolio-payload", type=Path, default=Path("data/portfolio_payload.json"),
                         help="cached portfolio dashboard payload written by portfolio-tracker")
    parser.add_argument("--db", type=Path, default=Path("data/journal_entry.sqlite"),
                         help="local SQLite store for imported transactions and rules")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="don't auto-open a browser tab")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.workbook.is_file():
        print(f"Workbook not found: {args.workbook.resolve()}")
        return 1
    run(
        args.workbook,
        args.db,
        portfolio_workbook=args.portfolio_workbook,
        portfolio_payload=args.portfolio_payload,
        host=args.host,
        port=args.port,
        open_browser=not args.no_browser,
    )
    return 0
