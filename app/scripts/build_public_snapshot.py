"""Copy reviewed source files into a fresh directory for a new public repo.

This exports the working tree without its Git history. It never publishes or
modifies the original repository.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
from datetime import datetime
from pathlib import Path


# Paths below are relative to the repo root; the project itself lives in app/.
APP = Path(__file__).resolve().parents[1]
ROOT = APP.parent
PUBLIC_FILES = {
    ".gitignore", "LICENSE", "README.md",
    "app/.python-version", "app/launch.pyw", "app/pyproject.toml", "app/setup.ps1",
    "app/setup_lseg_app_key.bat", "app/setup_lseg_app_key.ps1", "app/uv.lock",
    "app/data/.gitkeep",
    "app/examples/Personal Budget Template.xlsx",
    "app/examples/Portfolio Template.xlsx",
}
PUBLIC_DIRS = {".github", "app/scripts", "app/src", "app/tests"}
# Needs the private repo's PUBLIC_REPO_TOKEN secret, so it would only fail publicly.
EXCLUDED = {".github/workflows/publish-public.yml"}


def public_path(path: Path) -> bool:
    posix = path.as_posix()
    if posix in EXCLUDED:
        return False
    return posix in PUBLIC_FILES or any(posix.startswith(f"{d}/") for d in PUBLIC_DIRS)


def build_snapshot(destination: Path) -> int:
    """Create a clean copy from tracked and eligible new working-tree files."""
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(f"Snapshot destination already exists: {destination}")
    entries = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
    ).decode("utf-8").split("\0")
    selected: list[tuple[Path, Path]] = []
    for entry in entries:
        if not entry:
            continue
        relative = Path(entry)
        if relative.is_absolute() or ".." in relative.parts or not public_path(relative):
            continue
        candidate = ROOT / relative
        source = candidate.resolve()
        if candidate.is_symlink() or not source.is_relative_to(ROOT) or not source.is_file():
            continue
        selected.append((source, relative))

    destination.mkdir(parents=True)
    for source, relative in selected:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return len(selected)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path,
        default=APP / "public-preview" / datetime.now().strftime("%Y%m%dT%H%M%S"),
        help="new directory to create (default: timestamped public-preview folder)",
    )
    args = parser.parse_args()
    count = build_snapshot(args.output)
    print(f"Copied {count} reviewed files to {args.output.resolve()}")


if __name__ == "__main__":
    main()
