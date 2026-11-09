"""Tell someone what to run, instead of showing them an import traceback.

Every entry point a person types by hand — the demo, the browser suite, the test
session — passes through here first. A missing dependency is the most common way a
first run fails, and a stack trace ending in ModuleNotFoundError makes it look like
the project is broken rather than not yet installed.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

#: Import name → what it is for, in the order they matter.
REQUIRED = (
    ("fastapi", "the demo applications and the dashboard API"),
    ("uvicorn", "running the demo applications"),
    ("httpx", "the HTTP connector"),
)

INSTALL = 'pip install -e ".[dev]"'


def missing() -> list[tuple[str, str]]:
    out = []
    for name, purpose in REQUIRED:
        try:
            importlib.import_module(name)
        except ImportError:
            out.append((name, purpose))
    return out


def message(absent: list[tuple[str, str]]) -> str:
    lines = ["MORPH is not installed in this environment yet.", ""]
    for name, purpose in absent:
        lines.append(f"  missing: {name:<10} ({purpose})")
    lines += [
        "",
        f"  Fix:  {INSTALL}",
        "",
        f"  Python in use: {sys.executable}",
    ]
    venv = Path(sys.prefix) / "pyvenv.cfg"
    if not venv.is_file():
        lines.append("  No virtual environment is active. Activate one first:")
        lines.append("      python3 -m venv .venv && . .venv/bin/activate")
    return "\n".join(lines)


def require(exit_code: int = 1) -> None:
    """Exit with an explanation if anything needed is not installed."""
    absent = missing()
    if absent:
        print(message(absent), file=sys.stderr)
        raise SystemExit(exit_code)
