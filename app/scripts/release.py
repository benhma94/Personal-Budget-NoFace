"""Version and release-notes steps for `.github/workflows/publish-public.yml`.

Adapted from CALCIFER's publish script. Subcommands:

* ``plan`` picks the version to publish from the ``bump`` choice and the latest
  ``v*`` tag in the public repository, and writes a suggestion to the run summary.
* ``bump`` sets that version in ``pyproject.toml`` and ``uv.lock``, and drafts
  ``docs/releases/<version>.md`` from commit messages when it does not exist.
* ``notes`` checks the version is above the latest public one and writes the
  release notes, combining every ``docs/releases/*.md`` since that version.
* ``changelog`` prepends an entry to the public repository's ``CHANGELOG.md``.

Paths are relative to the app folder (``--source``). Standard library only, so the
workflow needs no project dependencies.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path

VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")
TAG = re.compile(r"v(\d+\.\d+\.\d+)")
BUMPS = ("auto", "patch", "minor", "major")
NOTES_DIR = Path("docs") / "releases"
VERSION_FILES = {
    "pyproject.toml": re.compile(r'(?m)^(version = ")(\d+\.\d+\.\d+)(")'),
    "uv.lock": re.compile(r'(?m)^(name = "portfolio-tracker"\r?\nversion = ")(\d+\.\d+\.\d+)(")'),
}

Version = tuple[int, int, int]


def parse_version(text: str) -> Version:
    match = VERSION.fullmatch(text.strip())
    if match is None:
        raise ValueError(f"Not a stable three-part version: {text!r}")
    return (int(match[1]), int(match[2]), int(match[3]))


def format_version(version: Version) -> str:
    return ".".join(str(part) for part in version)


def latest_published(tags: list[str]) -> Version | None:
    versions = [parse_version(m[1]) for tag in tags if (m := TAG.fullmatch(tag.strip()))]
    return max(versions, default=None)


def public_tags(public: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(public), "tag", "--list", "v*"],
        check=True, capture_output=True, text=True,
    )
    return result.stdout.splitlines()


def project_version(source: Path) -> Version:
    data = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))
    return parse_version(str(data["project"]["version"]))


def check_increase(version: Version, previous: Version | None) -> None:
    if previous is not None and version <= previous:
        raise ValueError(
            f"Version {format_version(version)} must be higher than the latest public "
            f"version {format_version(previous)}"
        )


def bumped(base: Version, bump: str) -> Version:
    major, minor, patch = base
    if bump == "patch":
        return (major, minor, patch + 1)
    if bump == "minor":
        return (major, minor + 1, 0)
    if bump == "major":
        return (major + 1, 0, 0)
    raise ValueError(f"Unknown bump {bump!r}; choose one of {', '.join(BUMPS)}")


def plan_version(project: Version, previous: Version | None, bump: str) -> Version:
    """The version to publish: never lower than the code's, always above the public one."""
    if previous is None:
        return project if bump == "auto" else bumped(project, bump)
    if bump == "auto":
        return project if project > previous else bumped(previous, "patch")
    return max(bumped(previous, bump), project)


def version_advice(project: Version, previous: Version | None) -> str:
    """Markdown telling the person which version each bump choice would publish."""
    shown = format_version(previous) if previous else "none yet"
    lines = [
        "### Version suggestion",
        "",
        f"- Latest public version: **{shown}**",
        f"- Version in the code (`app/pyproject.toml`): **{format_version(project)}**",
        "",
        "| Bump | Publishes | Use for |",
        "| --- | --- | --- |",
    ]
    uses = {
        "auto": "the code's version if unpublished, otherwise the next patch",
        "patch": "fixes only",
        "minor": "new features",
        "major": "major or breaking changes",
    }
    for bump in BUMPS:
        version = format_version(plan_version(project, previous, bump))
        lines.append(f"| `{bump}` | {version} | {uses[bump]} |")
    return "\n".join(lines) + "\n"


def set_version(source: Path, version: Version) -> list[str]:
    """Write ``version`` into every version file; return the files that changed."""
    changed: list[str] = []
    for name, pattern in VERSION_FILES.items():
        path = source / name
        text = path.read_text(encoding="utf-8")
        if len(pattern.findall(text)) != 1:
            raise ValueError(f"Expected exactly one project version in {name}")
        updated = pattern.sub(lambda m: m[1] + format_version(version) + m[3], text)
        if updated != text:
            path.write_bytes(updated.encode("utf-8"))
            changed.append(name)
    return changed


def git_lines(source: Path, *args: str) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(source), *args], check=True, capture_output=True, text=True
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def draft_notes(source: Path, version: Version) -> Path | None:
    """Draft ``docs/releases/<version>.md`` from commits since the last notes change."""
    path = source / NOTES_DIR / f"{format_version(version)}.md"
    if path.exists():
        return None
    last_notes = git_lines(source, "log", "-1", "--format=%H", "--", NOTES_DIR.as_posix())
    revisions = f"{last_notes[0]}..HEAD" if last_notes else "HEAD"
    subjects = [
        subject
        for subject in git_lines(source, "log", "--no-merges", "--format=%s", revisions)
        if not subject.startswith("Release ")
    ]
    if not subjects:
        raise ValueError(f"No commits since the last release notes; write {path}")
    items = "\n".join(f"- {subject}" for subject in subjects)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"# NOFACE {format_version(version)}\n\n{items}\n\n"
        "Drafted from commit messages by the publish workflow.\n",
        encoding="utf-8",
    )
    return path


def note_body(path: Path) -> str:
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    body = "\n".join(lines).strip()
    if not body:
        raise ValueError(f"Release notes are empty: {path}")
    return body


def release_notes(notes_dir: Path, version: Version, previous: Version | None) -> str:
    """Combine the notes for every version after ``previous`` up to ``version``."""
    if not (notes_dir / f"{format_version(version)}.md").is_file():
        raise ValueError(f"Missing release notes: {notes_dir / format_version(version)}.md")
    included: list[Version] = []
    for path in notes_dir.glob("*.md"):
        try:
            candidate = parse_version(path.stem)
        except ValueError:
            continue
        if candidate <= version and (previous is None or candidate > previous):
            included.append(candidate)
    if previous is None:
        included = [version]
    included.sort(reverse=True)

    header = (
        f"First release with a Windows installer: {format_version(version)}."
        if previous is None
        else f"Version {format_version(previous)} → {format_version(version)}."
    )
    if len(included) == 1:
        return f"{header}\n\n{note_body(notes_dir / f'{format_version(version)}.md')}\n"
    sections = [
        f"### Changes in {format_version(v)}\n\n{note_body(notes_dir / f'{format_version(v)}.md')}"
        for v in included
    ]
    return header + "\n\n" + "\n\n".join(sections) + "\n"


def prepend_changelog(path: Path, entry: str) -> None:
    title = "# Changelog\n\n"
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    previous_entries = old.removeprefix(title).strip()
    changelog = title + entry.strip() + "\n"
    if previous_entries:
        changelog += "\n" + previous_entries + "\n"
    path.write_text(changelog, encoding="utf-8")


def command_plan(args: argparse.Namespace) -> None:
    project = project_version(args.source)
    previous = latest_published(public_tags(args.public))
    version = plan_version(project, previous, args.bump)
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as summary:
            summary.write(version_advice(project, previous))
            summary.write(f"\n**This run publishes {format_version(version)}** ")
            summary.write(f"(bump: `{args.bump}`).\n\n")
    print(f"version={format_version(version)}")
    print(f"previous={format_version(previous) if previous else 'none'}")


def command_bump(args: argparse.Namespace) -> None:
    version = parse_version(args.version)
    project = project_version(args.source)
    if version < project:
        raise ValueError(f"Refusing to lower the version from {format_version(project)}")
    for name in set_version(args.source, version):
        print(name)
    notes = draft_notes(args.source, version)
    if notes is not None:
        print(notes.relative_to(args.source).as_posix())


def command_notes(args: argparse.Namespace) -> None:
    project = project_version(args.source)
    version = parse_version(args.version) if args.version.strip() else project
    if version != project:
        raise ValueError(
            f"Requested version {format_version(version)} does not match pyproject.toml "
            f"({format_version(project)})"
        )
    previous = latest_published(public_tags(args.public))
    check_increase(version, previous)
    notes = release_notes(args.source / NOTES_DIR, version, previous)
    args.output.write_text(notes, encoding="utf-8")
    print(f"previous={format_version(previous) if previous else 'none'}")


def command_changelog(args: argparse.Namespace) -> None:
    notes = args.notes.read_text(encoding="utf-8").strip()
    prepend_changelog(args.public / "CHANGELOG.md", f"## {args.version} — {args.date}\n\n{notes}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="choose the version to publish")
    plan.add_argument("--source", type=Path, default=Path("."))
    plan.add_argument("--public", type=Path, required=True)
    plan.add_argument("--bump", choices=BUMPS, default="auto")
    plan.add_argument("--summary", type=Path, help="append the version suggestion here")
    plan.set_defaults(handler=command_plan)

    bump = sub.add_parser("bump", help="set the version and draft missing release notes")
    bump.add_argument("--source", type=Path, default=Path("."))
    bump.add_argument("--version", required=True)
    bump.set_defaults(handler=command_bump)

    notes = sub.add_parser("notes", help="check the version increase and write release notes")
    notes.add_argument("--source", type=Path, default=Path("."))
    notes.add_argument("--public", type=Path, required=True)
    notes.add_argument("--version", default="", help="defaults to the pyproject.toml version")
    notes.add_argument("--output", type=Path, required=True)
    notes.set_defaults(handler=command_notes)

    changelog = sub.add_parser("changelog", help="prepend an entry to the public CHANGELOG.md")
    changelog.add_argument("--public", type=Path, required=True)
    changelog.add_argument("--version", required=True)
    changelog.add_argument("--date", required=True)
    changelog.add_argument("--notes", type=Path, required=True)
    changelog.set_defaults(handler=command_changelog)

    args = parser.parse_args(argv)
    try:
        args.handler(args)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
