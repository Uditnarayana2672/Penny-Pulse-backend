"""Fail if `app/services/` has grown a framework import or a commit.

The ban is the load-bearing rule of the whole backend (delta D7, `CLAUDE.md` "Layering"):
`services/` imports neither `fastapi` nor `sqlalchemy`, and nothing outside the session
dependency commits. Both erode one convenience import at a time, which is exactly the kind
of drift a human reviewer stops noticing by the fourth file.

Usage:
    python scripts/check_service_layering.py                 # scan app/services and app/lib
    python scripts/check_service_layering.py path/to/file.py # scan named files only

Exit code is the number of violations, so a hook can branch on non-zero.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Windows hands a subprocess a cp1252 stderr, which cannot encode the dash this file uses
# to separate a finding from its source line.
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent

# Directories the ban applies to. `app/lib/` is stricter still — it may not import from
# `app/` at all — but that rule needs a different test, so this file checks the shared part.
GUARDED_DIRECTORIES = ("app/services", "app/lib")

# Word-boundary anchored so `app.services.errors` and a docstring mentioning "sqlalchemy"
# in prose do not trip it. Only real import statements and real calls.
FORBIDDEN = (
    (re.compile(r"^\s*import\s+fastapi\b"), "imports fastapi"),
    (re.compile(r"^\s*from\s+fastapi\b"), "imports from fastapi"),
    (re.compile(r"^\s*import\s+sqlalchemy\b"), "imports sqlalchemy"),
    (re.compile(r"^\s*from\s+sqlalchemy\b"), "imports from sqlalchemy"),
    (re.compile(r"\.commit\s*\("), "calls .commit()"),
    (re.compile(r"\.rollback\s*\("), "calls .rollback()"),
)


def is_guarded(path: Path) -> bool:
    try:
        relative = path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        # Outside this repo entirely — a frontend file, or another checkout.
        return False
    return any(relative.startswith(directory) for directory in GUARDED_DIRECTORIES)


def violations_in(path: Path) -> list[str]:
    found: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        # A line that is only a comment is prose about the rule, not a breach of it.
        if line.lstrip().startswith("#"):
            continue
        for pattern, description in FORBIDDEN:
            if pattern.search(line):
                found.append(f"{path}:{number}: {description} — {line.strip()}")
    return found


def files_to_check(arguments: list[str]) -> list[Path]:
    if arguments:
        return [Path(argument) for argument in arguments]
    return sorted(
        path
        for directory in GUARDED_DIRECTORIES
        for path in (REPO_ROOT / directory).rglob("*.py")
    )


def main(arguments: list[str]) -> int:
    found: list[str] = []
    for path in files_to_check(arguments):
        if not path.exists() or path.suffix != ".py" or not is_guarded(path):
            continue
        found.extend(violations_in(path))

    for violation in found:
        print(violation, file=sys.stderr)

    if found:
        print(
            f"\n{len(found)} layering violation(s). services/ must import neither fastapi "
            "nor sqlalchemy, and only the session dependency commits.",
            file=sys.stderr,
        )
    return len(found)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
