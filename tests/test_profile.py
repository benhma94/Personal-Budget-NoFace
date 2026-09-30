from __future__ import annotations

import json

import pytest

from budget_dashboard.profile import DEFAULT_PROFILE, load_profile


def test_local_profile_can_replace_account_names(tmp_path):
    profile = json.loads(json.dumps(DEFAULT_PROFILE))
    profile["asset_accounts"][4] = "Custom Brokerage"
    profile["month_close"]["investment"]["account"] = "Custom Brokerage"
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile), encoding="utf-8")

    loaded = load_profile(path)

    assert "Custom Brokerage" in loaded["asset_accounts"]
    assert loaded["month_close"]["investment"]["account"] == "Custom Brokerage"


def test_explicit_profile_path_must_exist(tmp_path):
    with pytest.raises(FileNotFoundError, match="Profile not found"):
        load_profile(tmp_path / "missing.json")


def test_month_close_account_must_be_an_asset(tmp_path):
    profile = json.loads(json.dumps(DEFAULT_PROFILE))
    profile["month_close"]["transit"]["account"] = "Unlisted Account"
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile), encoding="utf-8")

    with pytest.raises(ValueError, match="not an asset account"):
        load_profile(path)
