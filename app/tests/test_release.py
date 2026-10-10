from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

_PATH = Path(__file__).parents[1] / "scripts" / "release.py"
_SPEC = importlib.util.spec_from_file_location("noface_release", _PATH)
release = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(release)


def test_latest_published_ignores_non_release_tags():
    assert release.latest_published(["v0.1.0", "v0.10.0", "v0.9.1", "nightly", "v1.0"]) == (0, 10, 0)
    assert release.latest_published([]) is None


@pytest.mark.parametrize(
    ("project", "previous", "bump", "expected"),
    [
        ((0, 1, 0), None, "auto", (0, 1, 0)),
        ((0, 1, 0), None, "minor", (0, 2, 0)),
        ((0, 1, 0), (0, 1, 0), "auto", (0, 1, 1)),
        ((0, 3, 0), (0, 2, 4), "auto", (0, 3, 0)),
        ((0, 1, 0), (0, 2, 4), "minor", (0, 3, 0)),
        ((0, 1, 0), (0, 2, 4), "major", (1, 0, 0)),
        ((2, 0, 0), (0, 2, 4), "patch", (2, 0, 0)),
    ],
)
def test_plan_version(project, previous, bump, expected):
    assert release.plan_version(project, previous, bump) == expected


def test_version_must_increase():
    release.check_increase((0, 1, 1), (0, 1, 0))
    release.check_increase((0, 1, 0), None)
    with pytest.raises(ValueError):
        release.check_increase((0, 1, 0), (0, 1, 0))


def test_notes_combine_every_version_since_previous(tmp_path):
    for version, text in [("0.1.0", "first"), ("0.1.1", "fix"), ("0.2.0", "feature")]:
        (tmp_path / f"{version}.md").write_text(f"# NOFACE {version}\n\n- {text}\n", encoding="utf-8")
    combined = release.release_notes(tmp_path, (0, 2, 0), (0, 1, 0))
    assert combined.startswith("Version 0.1.0 → 0.2.0.")
    assert "### Changes in 0.2.0" in combined and "### Changes in 0.1.1" in combined
    assert "first" not in combined
    first = release.release_notes(tmp_path, (0, 1, 0), None)
    assert "- first" in first and "### Changes" not in first
    with pytest.raises(ValueError):
        release.release_notes(tmp_path, (0, 3, 0), (0, 2, 0))


def test_set_version_updates_pyproject_and_lock(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "portfolio-tracker"\nversion = "0.1.0"\n', encoding="utf-8")
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "numpy"\nversion = "2.0.0"\n\n'
        '[[package]]\nname = "portfolio-tracker"\nversion = "0.1.0"\n', encoding="utf-8")
    assert release.set_version(tmp_path, (0, 2, 0)) == ["pyproject.toml", "uv.lock"]
    assert release.project_version(tmp_path) == (0, 2, 0)
    lock = (tmp_path / "uv.lock").read_text(encoding="utf-8")
    assert 'name = "numpy"\nversion = "2.0.0"' in lock
    assert 'name = "portfolio-tracker"\nversion = "0.2.0"' in lock


def test_bump_drafts_notes_from_commits(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.name", "t")
    git("config", "user.email", "t@example.com")
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n', encoding="utf-8")
    (tmp_path / "uv.lock").write_text('name = "portfolio-tracker"\nversion = "0.1.0"\n', encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "Add retirement tab")
    assert release.main(["bump", "--source", str(tmp_path), "--version", "0.1.1"]) == 0
    notes = (tmp_path / "docs" / "releases" / "0.1.1.md").read_text(encoding="utf-8")
    assert notes.startswith("# NOFACE 0.1.1") and "- Add retirement tab" in notes
    assert release.main(["bump", "--source", str(tmp_path), "--version", "0.1.0"]) == 1


def test_changelog_prepends_entries(tmp_path):
    notes = tmp_path / "notes.md"
    notes.write_text("- first\n", encoding="utf-8")
    args = ["changelog", "--public", str(tmp_path), "--date", "2026-10-10", "--notes", str(notes)]
    assert release.main([*args, "--version", "0.1.0"]) == 0
    notes.write_text("- second\n", encoding="utf-8")
    assert release.main([*args, "--version", "0.1.1"]) == 0
    text = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    assert text.startswith("# Changelog\n\n## 0.1.1 — 2026-10-10\n\n- second\n\n## 0.1.0")
