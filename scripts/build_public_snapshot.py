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


ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {
    ".gitignore", ".python-version", "LICENSE", "README.md", "msgbox.vbs",
    "pyproject.toml", "run.bat", "run_hidden.vbs", "setup_lseg_app_key.bat",
    "setup_lseg_app_key.ps1", "uv.lock",
}
PUBLIC_DIRS = {".github", "scripts", "src", "tests"}
PUBLIC_EXAMPLES = {"examples/Personal Budget Template.xlsx"}
# Needs the private repo's PUBLIC_REPO_TOKEN secret, so it would only fail publicly.
EXCLUDED = {".github/workflows/publish-public.yml"}


def public_path(path: Path) -> bool:
    parts = path.parts
    if path.as_posix() in EXCLUDED:
        return False
    return (
        path.as_posix() == "data/.gitkeep"
        or path.as_posix() in PUBLIC_EXAMPLES
        or len(parts) == 1 and parts[0] in ROOT_FILES
        or len(parts) > 1 and parts[0] in PUBLIC_DIRS
    )


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
        default=ROOT / "public-preview" / datetime.now().strftime("%Y%m%dT%H%M%S"),
        help="new directory to create (default: timestamped public-preview folder)",
    )
    args = parser.parse_args()
    count = build_snapshot(args.output)
    print(f"Copied {count} reviewed files to {args.output.resolve()}")


if __name__ == "__main__":
    main()
