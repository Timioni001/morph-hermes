"""Stamp catalog-entry.yaml with a real commit SHA and pinned asset URLs.

The catalog rejects branches and short SHAs, and asks that `image` and
`screenshots` URLs be re-pinned whenever the SHA moves. Doing that by hand at
submission time is exactly when it gets done wrong, so:

    python scripts/prepare_catalog_entry.py            # uses HEAD, prints the entry
    python scripts/prepare_catalog_entry.py --write    # also writes plugin-catalog-morph.yaml
                                                       # and plugin-catalog-pr-body.md

Copy the output into hermes-agent as plugin-catalog/morph.yaml in your PR.
It refuses to stamp a commit that is not pushed, because the catalog CI clones
your repository at that SHA and would fail on one that only exists locally.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRY = ROOT / "catalog-entry.yaml"
ASSETS = ["workspace.png", "approvals.png", "connections.png", "context.png"]


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                          text=True, check=True).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sha", help="commit to pin (default: HEAD)")
    parser.add_argument("--write", action="store_true",
                        help="write plugin-catalog-morph.yaml next to the repository root")
    parser.add_argument("--allow-unpushed", action="store_true",
                        help="stamp a commit that is not on the remote (for a dry run)")
    args = parser.parse_args(argv)

    sha = args.sha or git("rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        print(f"not a full 40-character SHA: {sha}", file=sys.stderr)
        return 1

    if not args.allow_unpushed:
        remote_branches = git("branch", "-r", "--contains", sha)
        if not remote_branches.strip():
            print(f"commit {sha[:12]} is not on any remote branch — push it first, "
                  f"or pass --allow-unpushed for a dry run", file=sys.stderr)
            return 1

    dirty = git("status", "--porcelain")
    if dirty and not args.sha:
        print("warning: the working tree has uncommitted changes, so HEAD is not what "
              "a reviewer will clone", file=sys.stderr)

    text = ENTRY.read_text(encoding="utf-8")
    repo_line = re.search(r"^repo:\s*(\S+)", text, re.M)
    if repo_line is None:
        print("catalog-entry.yaml has no repo: line", file=sys.stderr)
        return 1
    slug = repo_line.group(1).rstrip("/").removeprefix("https://github.com/")
    raw = f"https://raw.githubusercontent.com/{slug}/{sha}/docs/assets"

    text = re.sub(r'^sha:\s*"?[0-9a-f]*"?.*$', f'sha: "{sha}"', text, count=1, flags=re.M)
    text = re.sub(r"^# Re-pin these to the submitted SHA before opening the PR\.\n"
                  r"(?:^#.*\n)+", "", text, flags=re.M)
    missing = [name for name in ASSETS if not (ROOT / "docs" / "assets" / name).is_file()]
    if missing:
        print(f"warning: missing assets, leaving them out: {', '.join(missing)}",
              file=sys.stderr)
    present = [name for name in ASSETS if name not in missing]
    card = ROOT / "docs" / "assets" / "catalog-card.png"
    block = ""
    if card.is_file():
        block += f"image: {raw}/catalog-card.png\n"
    if present:
        block += "screenshots:\n" + "".join(f"  - {raw}/{name}\n" for name in present)
    text = text.replace("capabilities:\n", block + "capabilities:\n", 1)

    print(text)
    if args.write:
        out = ROOT / "plugin-catalog-morph.yaml"
        out.write_text(text, encoding="utf-8")
        print(f"\n→ wrote {out}", file=sys.stderr)
        template = ROOT / "docs" / "CATALOG_PR.md"
        if template.is_file():
            body = ROOT / "plugin-catalog-pr-body.md"
            body.write_text(template.read_text(encoding="utf-8").replace("<sha>", sha),
                            encoding="utf-8")
            print(f"→ wrote {body} (paste it as the PR description)", file=sys.stderr)
    print(f"\n→ pinned {sha}", file=sys.stderr)
    print(f"→ open the PR against NousResearch/hermes-agent adding "
          f"plugin-catalog/morph.yaml", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
