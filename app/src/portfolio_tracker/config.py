"""Parse the workbook's Config sheet into a typed dataclass."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from openpyxl import load_workbook


_LSEG_BENCHMARK_REPLACEMENTS = {
    "XIC.TO": ".TRGSPTSE",
    ".GSPTSE": ".TRGSPTSE",
    "^GSPC": ".SPXTR",
    "XAW.TO": ".FTDLMSXNA",
    "XBB.TO": ".FT26045CADT",
}


@dataclass
class Config:
    base_currency: str = "CAD"
    risk_free_rate: float = 0.0319
    inception_date: date | None = None
    cache_ttl_classifications_days: int = 7
    benchmark_blend: list[tuple[str, float]] = field(default_factory=list)


def read_config(workbook_path) -> Config:
    """Read the Config sheet from `workbook_path`.

    Layout: column A = key, column B = value, one key per row.
    After a row with key == 'benchmark_blend', subsequent rows are
    (ticker, weight) pairs until a blank row.
    """
    wb = load_workbook(workbook_path, data_only=True, read_only=True)
    if "Config" not in wb.sheetnames:
        raise ValueError(f"Workbook {workbook_path} has no 'Config' sheet")

    ws = wb["Config"]
    cfg = Config()
    in_blend = False

    for row in ws.iter_rows(min_row=1, values_only=True):
        if not row or row[0] is None:
            in_blend = False
            continue

        key = str(row[0]).strip().lower()
        value = row[1] if len(row) > 1 else None

        if in_blend:
            if value is None:
                in_blend = False
                continue
            ticker = str(row[0]).strip()
            cfg.benchmark_blend.append((_lseg_benchmark_ticker(ticker), float(value)))
            continue

        if key == "base_currency":
            cfg.base_currency = str(value).strip().upper()
        elif key == "risk_free_rate":
            cfg.risk_free_rate = float(value)
        elif key == "inception_date":
            cfg.inception_date = _coerce_date(value)
        elif key == "cache_ttl_classifications_days":
            cfg.cache_ttl_classifications_days = int(value)
        elif key == "benchmark_blend":
            in_blend = True

    _validate(cfg)
    return cfg


def _lseg_benchmark_ticker(ticker: str) -> str:
    """Replace legacy ETF/Yahoo equity proxies with direct LSEG index RICs."""
    return _LSEG_BENCHMARK_REPLACEMENTS.get(ticker.upper(), ticker)


def _coerce_date(value) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip())


def _validate(cfg: Config) -> None:
    if cfg.inception_date is None:
        raise ValueError("Config: 'inception_date' is required")
    if not cfg.benchmark_blend:
        raise ValueError("Config: 'benchmark_blend' must have at least one component")
    total = sum(w for _, w in cfg.benchmark_blend)
    if abs(total - 1.0) > 0.01:
        raise ValueError(
            f"Config: benchmark_blend weights sum to {total:.4f}, expected ~1.0"
        )
