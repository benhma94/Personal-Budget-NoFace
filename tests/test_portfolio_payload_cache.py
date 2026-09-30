"""Tests for the portfolio payload cache: the JSON file portfolio_tracker.cli
writes after a pipeline run (see test_write_payload_cache_wraps_payload_with_a_
generation_timestamp in test_cli.py), and the background refresh job
finance_hub uses to rerun that (slow, LSEG-dependent) pipeline without
blocking the app.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

import pytest

from finance_hub.portfolio_api import PortfolioRefreshJob, read_cache, stale_days


def test_read_cache_returns_none_when_file_is_absent(tmp_path):
    assert read_cache(tmp_path / "missing.json") is None


def test_read_cache_returns_none_for_corrupt_json(tmp_path):
    path = tmp_path / "portfolio_payload.json"
    path.write_text("{not valid json", encoding="utf-8")

    assert read_cache(path) is None


def test_read_cache_round_trips_a_valid_payload(tmp_path):
    path = tmp_path / "portfolio_payload.json"
    document = {"generated_at": "2026-08-01T12:00:00-04:00", "payload": {"total_value_cad": 500.0}}
    path.write_text(json.dumps(document), encoding="utf-8")

    cached = read_cache(path)
    assert cached["payload"]["total_value_cad"] == 500.0


def test_stale_days_is_none_without_a_timestamp():
    assert stale_days(None) is None


def test_stale_days_is_roughly_zero_for_a_fresh_timestamp():
    now_iso = datetime.now(timezone.utc).isoformat()
    assert stale_days(now_iso) < 0.01


def test_refresh_job_starts_idle(tmp_path):
    job = PortfolioRefreshJob(tmp_path / "portfolio.xlsx", tmp_path / "portfolio_payload.json")

    status = job.status()

    assert status["state"] == "idle"
    assert status["log"] == []
    assert status["generated_at"] is None


def test_refresh_job_rejects_a_concurrent_start(tmp_path, monkeypatch):
    job = PortfolioRefreshJob(tmp_path / "portfolio.xlsx", tmp_path / "portfolio_payload.json")
    release = threading.Event()

    def fake_main(argv):
        release.wait(timeout=2)
        return 0

    monkeypatch.setattr("portfolio_tracker.cli.main", fake_main)
    job.start()
    try:
        with pytest.raises(RuntimeError):
            job.start()
    finally:
        release.set()


def test_refresh_job_reports_error_state_on_nonzero_exit(tmp_path, monkeypatch):
    job = PortfolioRefreshJob(tmp_path / "portfolio.xlsx", tmp_path / "portfolio_payload.json")
    monkeypatch.setattr("portfolio_tracker.cli.main", lambda argv: 1)

    job.start()
    for _ in range(50):
        if job.status()["state"] != "running":
            break
        threading.Event().wait(0.05)

    status = job.status()
    assert status["state"] == "error"
    assert "1" in status["error"]
