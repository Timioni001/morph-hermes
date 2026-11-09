"""Prove the GitHub connector against the real API, read-only.

    export MORPH_GITHUB_TOKEN=ghp_...          # or: gh auth token
    python scripts/check_github_connector.py --repo owner/name

It introspects the repository, reads issues, derives versions, and runs the change
feed twice. It performs **no writes** — the whole point of MORPH is that a write
waits for a person, and a check script is not a person.

Add --propose to build (but not apply) a proposal against the newest open issue, so
you can see the before/after MORPH would ask you to approve.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--propose", action="store_true",
                        help="also build a proposal (still applies nothing)")
    args = parser.parse_args(argv)

    os.environ.setdefault("MORPH_DATA_DIR", str(ROOT / ".morph-check"))
    os.environ["MORPH_CONFIG"] = json.dumps({
        "connectors": [{"id": "github", "kind": "github", "label": "GitHub",
                        "token_env": "MORPH_GITHUB_TOKEN",
                        "options": {"repo": args.repo}}],
        "default_role": "operator",
    })

    from morph import engine, registry, sync
    from morph.actions import Principal

    operator = Principal.operator("check@local", "Connector check")
    probe = registry.health()[0]
    print(f"health      : ok={probe['ok']} acting_as={probe.get('acting_as')} "
          f"{probe.get('latency_ms')} ms")
    if not probe["ok"]:
        print(f"              {probe.get('error')}")
        return 1

    graph = engine.graph(refresh=True)
    entity = graph.entity("github.issues")
    print(f"schema      : {len(entity.fields)} fields, "
          f"{len(entity.operations)} operations "
          f"({', '.join(o.id for o in entity.operations)})")

    from morph.connectors.base import Predicate
    result = registry.get("github").fetch("issues", filters=[Predicate("state", "eq", "open")],
                                          limit=5)
    print(f"read        : {result.total} open issue(s)")
    for record in result.records[:5]:
        print(f"              #{record['number']:<5} v{record['_version']} "
              f"{record['state']:<6} {record['title'][:58]}")

    first = sync.poll("github-check")
    second = sync.poll("github-check")
    print(f"change feed : {first['mechanism']}, {first['backfilled']} backfilled, "
          f"{len(second['changes'])} new on the second poll (expected 0)")

    planned = engine.plan_workspace(operator, "triage the open issues in this repository")
    print(f"workspace   : {planned['workspace']['revision']} · "
          f"{planned['plan']['mode']} · {', '.join(planned['entities_used'])}")
    for section in planned["workspace"]["schema"]["sections"]:
        print("              " + section["title"] + ": "
              + ", ".join(b["title"] for b in section["blocks"]))

    if args.propose:
        target = next((r for r in result.records if not r["is_pull_request"]), None)
        if target is None:
            print("propose     : no non-PR issue to target")
            return 0
        proposal = engine.propose(operator, source="github", entity="issues",
                                  record_id=target["number"], operation="close_issue",
                                  reason="Connector check — not applied")
        print(f"propose     : {proposal['operation_ref']} on #{proposal['record_id']}")
        print(f"              now    {proposal['before']}")
        print(f"              would  {proposal['predicted']}")
        print(f"              status {proposal['status']} — nothing was written to GitHub")
        engine.reject(operator, proposal["id"], "connector check")
        print("              rejected, so the queue is left clean")
    print("\nNo write was performed. Approving one is a person's job, in the dashboard.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
