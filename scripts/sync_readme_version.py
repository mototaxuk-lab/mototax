#!/usr/bin/env python3
"""Sync the version badge + history table in README.md from CHANGELOG.md.

CHANGELOG.md is the single source of truth. This regenerates the block between
the <!-- VERSION:START --> and <!-- VERSION:END --> markers in README.md.

Exit codes:
  0  README already up to date (no change written)
  2  README was out of date and has been updated (caller should commit it)
  1  error (markers missing, no versions parsed, etc.)

Run manually any time, or let the pre-push hook run it. Stdlib only.
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
README = ROOT / "README.md"

START = "<!-- VERSION:START — auto-generated from CHANGELOG.md by .githooks/pre-push; do not edit by hand -->"
END = "<!-- VERSION:END -->"

# Matches headings like:  ## [0.4.0] — 2026-06-09
_HEADING = re.compile(r"^##\s+\[(\d+\.\d+\.\d+)\]\s+[—-]\s+(\d{4}-\d{2}-\d{2})\s*$")


def parse_versions(changelog_text: str) -> list[tuple[str, str, str]]:
    """Return [(version, date, summary), ...], newest first."""
    lines = changelog_text.splitlines()
    out: list[tuple[str, str, str]] = []
    for i, line in enumerate(lines):
        m = _HEADING.match(line)
        if not m:
            continue
        version, date = m.group(1), m.group(2)
        # Summary = the first paragraph after the heading (joined across soft
        # wraps), stopping at a blank line, a sub-section, or a link reference.
        summary_lines: list[str] = []
        for nxt in lines[i + 1:]:
            s = nxt.strip()
            if not s:
                if summary_lines:
                    break
                continue
            if s.startswith("#") or s.startswith("["):
                break
            summary_lines.append(s)
        # Escape pipes so a summary can't break the Markdown table row.
        summary = " ".join(summary_lines).replace("|", "\\|")
        out.append((version, date, summary))
    return out


def render_block(versions: list[tuple[str, str, str]]) -> str:
    latest = versions[0][0]
    badge = (
        f"![Version](https://img.shields.io/badge/version-{latest}-blue) "
        f"![Changelog](https://img.shields.io/badge/changelog-CHANGELOG.md-informational)"
    )
    rows = "\n".join(
        f"| **{v}** | {d} | {s} |" for v, d, s in versions
    )
    table = (
        "### Version history\n\n"
        "| Version | Date | Summary |\n"
        "|---------|------|---------|\n"
        f"{rows}\n\n"
        "Full details in [CHANGELOG.md](CHANGELOG.md)."
    )
    return f"{badge}\n\n{table}"


def main() -> int:
    if not CHANGELOG.exists():
        print("sync_readme_version: CHANGELOG.md not found", file=sys.stderr)
        return 1
    if not README.exists():
        print("sync_readme_version: README.md not found", file=sys.stderr)
        return 1

    versions = parse_versions(CHANGELOG.read_text(encoding="utf-8"))
    if not versions:
        print("sync_readme_version: no version headings parsed from CHANGELOG.md",
              file=sys.stderr)
        return 1

    readme = README.read_text(encoding="utf-8")
    if START not in readme or END not in readme:
        print("sync_readme_version: VERSION markers not found in README.md",
              file=sys.stderr)
        return 1

    block = render_block(versions)
    new_section = f"{START}\n{block}\n{END}"
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.DOTALL)
    updated = pattern.sub(lambda _: new_section, readme, count=1)

    if updated == readme:
        return 0  # already in sync

    README.write_text(updated, encoding="utf-8")
    print(f"sync_readme_version: README updated to v{versions[0][0]}")
    return 2  # changed


if __name__ == "__main__":
    raise SystemExit(main())
