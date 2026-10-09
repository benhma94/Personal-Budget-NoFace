"""Command-line entry point for the budget dashboard payload.

The interactive HTML shell that used to be generated here (budget_dashboard/
html.py) now lives in finance_hub/assets and is served live by the hub. This
CLI is kept for scripting/debugging: it writes the same payload the hub's
/api/budget/payload route serves, as JSON.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .workbook import build_dashboard_payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the budget dashboard payload from Personal Budget.xlsx")
    parser.add_argument("--workbook", type=Path, default=Path("data/Personal Budget.xlsx"), help="source workbook (read only)")
    parser.add_argument("--output", type=Path, default=Path("budget_dashboard.json"), help="payload JSON destination")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    payload = build_dashboard_payload(args.workbook)
    args.output.write_text(json.dumps(payload, default=str, ensure_ascii=False), encoding="utf-8")
    print(f"Budget dashboard payload written to {args.output.resolve()}")
    print(f"Workbook was read only: {args.workbook.resolve()}")
    return 0
