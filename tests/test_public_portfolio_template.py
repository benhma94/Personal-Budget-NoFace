"""The distributable portfolio template must match the tracker's readers."""
from pathlib import Path

from portfolio_tracker.config import read_config
from portfolio_tracker.io_excel import read_instrument_map, read_price_overrides
from scripts.build_public_snapshot import public_path


TEMPLATE = Path(__file__).resolve().parents[1] / "examples/Portfolio Template.xlsx"


def test_public_portfolio_template_is_usable():
    config = read_config(TEMPLATE)

    assert config.base_currency == "CAD"
    assert config.inception_date is not None
    assert len(config.benchmark_blend) == 4
    assert sum(weight for _, weight in config.benchmark_blend) == 1
    assert read_price_overrides(TEMPLATE) == {}
    assert read_instrument_map(TEMPLATE) == {}
    assert public_path(Path("examples/Portfolio Template.xlsx"))
    assert public_path(Path("docs/guide.md"))
