from __future__ import annotations

from pathlib import Path

from finance_hub.cli import _parser, main


def test_main_returns_1_when_workbook_missing(tmp_path, capsys):
    missing = tmp_path / "Nope.xlsx"
    assert main(["--workbook", str(missing)]) == 1
    assert "not found" in capsys.readouterr().out


def test_defaults_match_the_single_launcher_convention():
    args = _parser().parse_args([])
    assert args.workbook.name == "Personal Budget.xlsx"
    assert args.portfolio_workbook == Path("data/portfolio.xlsx")
    assert args.portfolio_payload.name == "portfolio_payload.json"
    assert args.port == 8765


def test_hidden_launcher_marker_is_not_forwarded_to_finance_hub():
    launcher = (Path(__file__).parents[1] / "run.bat").read_text(encoding="utf-8")
    assert "FINANCE_HUB_LAUNCH_HIDDEN" in launcher
    assert "--hidden" not in launcher
