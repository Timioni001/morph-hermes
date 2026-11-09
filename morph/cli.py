"""``hermes morph …`` and the human-readable text the slash command reuses.

The approval subcommand is the only write path outside the dashboard, and it is
deliberately awkward for anything that is not a person: it refuses without a
terminal, it will not read the confirmation token from an argument alone without an
interactive confirmation, and it never prints a token an agent could reuse.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Optional

from morph import actions, engine, launch, registry, store, workspace
from morph.actions import Principal
from morph.config import settings
from morph.errors import MorphError, PermissionDenied
from morph.util import clean_text


def _operator(args: Any = None) -> Principal:
    identity = getattr(args, "as_user", "") or ""
    return Principal.operator(identity, identity or "terminal operator")


def _out(payload: Any, as_json: bool) -> int:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


# ───────────────────────────────── text views ──────────────────────────────────

def status_text() -> str:
    current = settings()
    lines = [f"MORPH · configuration from {current.source}"]
    for probe in registry.health():
        mark = "ok" if probe.get("ok") else "unreachable"
        detail = f" — {probe['error']}" if probe.get("error") else ""
        entities = ", ".join(probe.get("entities") or [])
        lines.append(f"  [{mark:>11}] {probe.get('label') or probe['id']} ({probe['id']})"
                     f"{': ' + entities if entities else ''}{detail}")
    saved = workspace.current(include_schema=True)
    if saved is None:
        lines.append("  no workspace yet — set a goal with: hermes morph goal \"...\"")
    else:
        schema = saved.get("schema") or {}
        lines.append(f"  workspace: {saved['title']!r} (revision {saved['revision']}, "
                     f"{saved['mode']})")
        lines.append(f"  goal: {saved['goal']}")
        for section in schema.get("sections") or []:
            lines.append(f"    · {section['title']}: "
                         + ", ".join(b["title"] for b in section["blocks"]))
    pending = actions.list_proposals()
    lines.append(f"  pending approvals: {len(pending)}")
    chain = store.verify_audit_chain()
    lines.append(f"  audit log: {chain['entries']} entries, chain "
                 f"{'intact' if chain['ok'] else 'BROKEN at ' + str(chain.get('broken_at'))}")
    return "\n".join(lines)


def proposals_text() -> str:
    pending = actions.list_proposals()
    if not pending:
        return "MORPH: nothing is awaiting approval."
    lines = ["Awaiting human approval in the MORPH dashboard:"]
    for proposal in pending:
        lines.append(f"  {proposal['id'][:12]}  {proposal['operation_ref']} on "
                     f"{proposal['record_id']}  [{proposal['risk']} risk]  "
                     f"asked by {proposal['requested_by']}")
        if proposal["predicted"]:
            lines.append("      would set: " + ", ".join(
                f"{k} = {v!r}" for k, v in proposal["predicted"].items()))
        if proposal["reason"]:
            lines.append(f"      reason: {proposal['reason']}")
    return "\n".join(lines)


# ──────────────────────────────── subcommands ──────────────────────────────────

def setup_parser(parser: Any) -> None:
    """Build the ``hermes morph`` argparse tree."""
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument("--as-user", default="", metavar="IDENTITY",
                        help="act as this configured identity (role lookup)")
    subparsers = parser.add_subparsers(dest="morph_command")

    subparsers.add_parser("status", help="connected applications, workspace and approvals")
    subparsers.add_parser("discover", help="full capability graph")
    subparsers.add_parser("state", help="workspace with live values resolved")
    subparsers.add_parser("refresh", help="re-read every source and report what changed")
    subparsers.add_parser("proposals", help="actions awaiting approval")
    subparsers.add_parser("audit", help="recent audit entries and chain verification")
    subparsers.add_parser("doctor", help="check configuration, reachability and storage")
    subparsers.add_parser("reload", help="re-read configuration and rebuild connectors")

    up = subparsers.add_parser(
        "up", help="start the applications MORPH ships with and report what is live")
    up.add_argument("--host", default="127.0.0.1",
                    help="bind address for the bundled applications (loopback by default)")
    up.add_argument("--detach", action="store_true",
                    help="return once they are answering instead of holding the terminal")
    up.add_argument("--no-env", action="store_true",
                    help="do not write bearer tokens to the Hermes profile environment")
    up.add_argument("--timeout", type=float, default=25.0,
                    help="seconds to wait for each application to answer")

    goal = subparsers.add_parser("goal", help="compose or transform the workspace")
    goal.add_argument("text", nargs="+", help="what you want to accomplish")
    goal.add_argument("--no-preserve", action="store_true",
                      help="do not carry the current selection and pinned filters over")

    propose = subparsers.add_parser("propose", help="propose one action (still needs approval)")
    propose.add_argument("ref", help="<application>.<entity>.<operation>")
    propose.add_argument("record_id")
    propose.add_argument("--param", action="append", default=[], metavar="NAME=VALUE")
    propose.add_argument("--reason", default="")

    approve = subparsers.add_parser("approve", help="approve a pending action (interactive)")
    approve.add_argument("proposal_id")

    reject = subparsers.add_parser("reject", help="reject a pending action")
    reject.add_argument("proposal_id")
    reject.add_argument("--reason", default="")

    verify = subparsers.add_parser("verify", help="re-read the source and verify an applied action")
    verify.add_argument("proposal_id")


def run(args: Any) -> int:
    command = getattr(args, "morph_command", None) or "status"
    as_json = bool(getattr(args, "json", False))
    try:
        return _dispatch(command, args, as_json)
    except MorphError as exc:
        if as_json:
            print(json.dumps({"ok": False, **exc.payload()}, ensure_ascii=False, indent=2))
        else:
            print(f"MORPH: {exc.message}", file=sys.stderr)
        return 1


def _dispatch(command: str, args: Any, as_json: bool) -> int:  # noqa: C901 - a flat command table
    principal = _operator(args)

    if command == "status":
        if as_json:
            return _out(engine.discover(principal), True)
        print(status_text())
        return 0

    if command == "discover":
        found = engine.discover(principal, refresh=True)
        if as_json:
            return _out(found, True)
        summary = found["summary"]
        for application in summary["applications"]:
            print(f"{application['label']} ({application['id']})")
            for name in application["entities"]:
                print(f"    entity {application['id']}.{name}")
        print("operations:")
        for entry in found["permissions"]:
            mark = "allowed" if entry["allowed"] else "denied "
            print(f"  [{mark}] {entry['ref']}  ({entry['risk']} risk)"
                  + (f" — {entry['reason']}" if entry["reason"] else ""))
        for missing in summary["unavailable"]:
            print(f"  unreachable: {missing['id']} — {missing['error']}")
        return 0

    if command == "state":
        state = engine.state(principal, refresh=True)
        if as_json:
            return _out(state, True)
        saved = state.get("workspace")
        if not saved:
            print(state.get("reason") or "no workspace")
            return 0
        print(f"{saved['title']}  (revision {saved['revision']}, {saved['mode']})")
        blocks = (state.get("data") or {}).get("blocks") or {}
        for section in (saved.get("schema") or {}).get("sections") or []:
            print(f"\n  {section['title']}")
            for block in section["blocks"]:
                resolved = blocks.get(block["id"], {})
                if "error" in resolved:
                    print(f"    ! {block['title']}: {resolved['error']}")
                elif block["kind"] == "metric":
                    print(f"    {block['title']}: {resolved.get('value')}")
                elif block["kind"] in ("table", "relation"):
                    print(f"    {block['title']}: {len(resolved.get('rows') or [])} row(s)")
                elif block["kind"] == "chart":
                    points = ", ".join(f"{p['label']}={p['value']}"
                                       for p in resolved.get("points") or [])
                    print(f"    {block['title']}: {points}")
                else:
                    print(f"    {block['title']} ({block['kind']})")
        if (state.get("data") or {}).get("failures"):
            print("\n  degraded:", (state["data"]["failures"]))
        return 0

    if command == "refresh":
        result = engine.refresh_data(principal)
        if as_json:
            return _out(result, True)
        if not result["changes"]:
            print("No changes since the last read.")
        for change in result["changes"]:
            counts = change["counts"]
            print(f"{change['entity']}: +{counts['added']} -{counts['removed']} "
                  f"~{counts['updated']}"
                  + (f"  updated: {', '.join(change['updated'][:5])}" if change["updated"] else ""))
        return 0

    if command == "goal":
        text = " ".join(getattr(args, "text", []))
        result = engine.plan_workspace(principal, text,
                                        preserve_context=not getattr(args, "no_preserve", False))
        if as_json:
            return _out(result, True)
        saved = result["workspace"]
        print(f"Composed {saved['title']!r} (revision {saved['revision']}, "
              f"{result['plan']['mode']})")
        for note in result["plan"]["notes"]:
            print(f"  note: {note}")
        for section in (saved.get("schema") or {}).get("sections") or []:
            print(f"  · {section['title']}: " + ", ".join(b["title"] for b in section["blocks"]))
        if result["context_carried"]:
            print("  carried over: " + "; ".join(result["context_carried"]))
        if result["context_dropped"]:
            print("  dropped: " + "; ".join(result["context_dropped"]))
        return 0

    if command == "proposals":
        if as_json:
            return _out(actions.list_proposals(), True)
        print(proposals_text())
        return 0

    if command == "propose":
        parts = str(getattr(args, "ref", "")).split(".")
        if len(parts) != 3:
            raise MorphError("ref must be <application>.<entity>.<operation>")
        params: dict[str, Any] = {}
        for pair in getattr(args, "param", []) or []:
            name, _, value = str(pair).partition("=")
            if not name or not _:
                raise MorphError(f"--param must be NAME=VALUE, got {pair!r}")
            params[name.strip()] = value
        proposal = engine.propose(principal, source=parts[0], entity=parts[1],
                                   record_id=str(getattr(args, "record_id", "")),
                                   operation=parts[2], params=params,
                                   reason=str(getattr(args, "reason", "")))
        if as_json:
            return _out({k: v for k, v in proposal.items() if k != "confirm_token"}, True)
        print(f"Proposed {proposal['operation_ref']} on {proposal['record_id']}")
        print(f"  proposal id: {proposal['id']}")
        print("  would set: " + ", ".join(f"{k} = {v!r}" for k, v in proposal["predicted"].items()))
        print("  nothing has changed yet — approve it in the dashboard, or with: "
              f"hermes morph approve {proposal['id']}")
        return 0

    if command == "approve":
        return _approve(principal, args, as_json)

    if command == "reject":
        result = engine.reject(principal, str(getattr(args, "proposal_id", "")),
                                str(getattr(args, "reason", "")))
        if as_json:
            return _out(result, True)
        print(f"Rejected {result['id']} — nothing was changed.")
        return 0

    if command == "verify":
        report = engine.verify(str(getattr(args, "proposal_id", "")))
        if as_json:
            return _out(report, True)
        print(f"{report.get('operation_ref') or report.get('proposal')}: status "
              f"{report['status']}, verified={report['verified']}")
        for mismatch in report.get("mismatches") or []:
            print(f"  mismatch {mismatch['field']}: expected {mismatch['expected']!r}, "
                  f"found {mismatch['actual']!r}")
        return 0

    if command == "audit":
        entries = store.audit_tail(30)
        chain = store.verify_audit_chain()
        if as_json:
            return _out({"chain": chain, "entries": entries}, True)
        print(f"audit chain: {'intact' if chain['ok'] else 'BROKEN'} "
              f"({chain['entries']} entries)")
        for entry in entries:
            print(f"  #{entry['seq']:>4} {entry['kind']:<22} {entry['subject']:<44} "
                  f"by {entry['actor']} ({entry['authority']})")
        return 0

    if command == "reload":
        return _out(engine.reload(), True) if as_json else _print_reload()

    if command == "doctor":
        report = doctor()
        if as_json:
            return _out(report, True)
        for line in report["lines"]:
            print(line)
        return 0 if report["ok"] else 1

    if command == "up":
        return _up(args, as_json)

    raise MorphError(f"unknown subcommand {command!r}")


def _up(args: Any, as_json: bool) -> int:  # noqa: C901 - one linear procedure
    """``hermes morph up`` — start what is ours, report what is not."""
    # Before the survey: an unauthenticated probe cannot tell a running
    # application from a stopped one, and the survey decides what to start.
    exports = launch.prime_environment()
    plans = launch.survey()
    startable = [plan for plan in plans if plan.actionable]

    written: list[str] = []
    if not getattr(args, "no_env", False):
        written = launch.ensure_profile_env(exports)

    children, late = launch.start(plans, host=getattr(args, "host", "127.0.0.1"),
                                  timeout=float(getattr(args, "timeout", 25.0)))
    after = launch.survey() if startable else plans
    live = [plan for plan in after if plan.state == launch.RUNNING]

    if as_json:
        payload = {
            "started": [plan.connector_id for plan in startable],
            "running": [plan.connector_id for plan in live],
            "not_ours": [{"id": plan.connector_id, "reason": plan.detail}
                         for plan in after if plan.state == launch.EXTERNAL],
            "disabled": [plan.connector_id for plan in after
                         if plan.state == launch.DISABLED],
            "did_not_answer": late,
            "env_written": written,
            "detached": bool(getattr(args, "detach", False)),
        }
        _out(payload, True)
    else:
        for line in _up_text(after, startable, late, written):
            print(line)

    if late:
        launch.stop(children)
        return 1
    if not children or getattr(args, "detach", False):
        return 0

    print("\nCtrl-C to stop them.")
    try:
        for child in children:
            child.wait()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        launch.stop(children)
    return 0


def _up_text(plans: list[Any], startable: list[Any], late: list[str],
             written: list[str]) -> list[str]:
    lines = []
    for plan in plans:
        if plan.state == launch.RUNNING:
            was = "started" if any(s.connector_id == plan.connector_id for s in startable) \
                else "already up"
            lines.append(f"  up       {plan.label:<12} {plan.base_url}  ({was})")
        elif plan.state == launch.DISABLED:
            lines.append(f"  off      {plan.label:<12} {plan.detail}")
        else:
            lines.append(f"  not mine {plan.label:<12} {plan.base_url}  — {plan.detail}")
    for connector_id in late:
        lines.append(f"  failed   {connector_id} did not answer in time")
    if written:
        lines.append(f"  wrote {', '.join(written)} to {launch.hermes_env_path()}")
    if not any(plan.state == launch.RUNNING for plan in plans):
        lines.append("Nothing is reachable. `hermes morph doctor` explains why.")
    else:
        lines.append("Open the MORPH tab: `hermes dashboard`.")
    return lines


def _print_reload() -> int:
    result = engine.reload()
    print(f"Configuration reloaded from {result['config_source']}; connectors: "
          + ", ".join(result["connectors"]))
    return 0


def _approve(principal: Principal, args: Any, as_json: bool) -> int:
    """Approve from a terminal — a person, interactively, or not at all.

    An agent with shell access must not be able to turn the CLI into the approval
    tool it does not have.  So: no terminal, no approval; and the confirmation is
    typed, not passed as an argument.
    """
    proposal_id = str(getattr(args, "proposal_id", ""))
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise PermissionDenied(
            "approving from the CLI needs an interactive terminal. Approve in the MORPH dashboard "
            "instead; a non-interactive caller is never treated as the human in the loop.")
    proposal = actions.get_proposal(proposal_id, include_token=True)
    if proposal["status"] != "pending":
        raise MorphError(f"proposal {proposal_id} is {proposal['status']}, not pending")
    print(f"Approve {proposal['operation_ref']} on {proposal['record_id']}?")
    print("  current: " + ", ".join(f"{k} = {v!r}" for k, v in proposal["before"].items()))
    print("  becomes: " + ", ".join(f"{k} = {v!r}" for k, v in proposal["predicted"].items()))
    print(f"  risk: {proposal['risk']}   asked by: {proposal['requested_by']} "
          f"({proposal['authority']})")
    if proposal["reason"]:
        print(f"  reason: {proposal['reason']}")
    answer = input("Type the word 'approve' to apply this, anything else to abort: ").strip()
    if answer.lower() != "approve":
        print("Aborted; nothing was changed.")
        return 1
    result = engine.approve(principal, proposal_id, confirm_token=proposal["confirm_token"])
    if as_json:
        return _out(result, True)
    verification = (result.get("result") or {}).get("verification") or {}
    print(f"Applied. Status {result['status']}; verified against {result['source']}: "
          f"{verification.get('verified')}")
    for field_name, value in ((result.get("result") or {}).get("changed") or {}).items():
        print(f"  {field_name} = {value!r}")
    return 0


def doctor() -> dict[str, Any]:
    """Self-check: configuration, storage, reachability, permissions."""
    lines: list[str] = []
    ok = True
    try:
        current = settings()
        lines.append(f"configuration: {current.source}")
        lines.append(f"  connectors: {', '.join(c.id for c in current.connectors) or 'none'}")
        lines.append(f"  links: {', '.join(l.name for l in current.links) or 'none'}")
        lines.append(f"  roles: {', '.join(sorted(current.roles))}")
        lines.append(f"  loopback allowed: {current.allow_loopback}")
    except MorphError as exc:
        return {"ok": False, "lines": [f"configuration is invalid: {exc.message}"]}

    try:
        path = store.db_path()
        with store.read() as connection:
            connection.execute("SELECT COUNT(*) FROM audit").fetchone()
        lines.append(f"storage: {path} (writable)")
    except Exception as exc:  # noqa: BLE001
        ok = False
        lines.append(f"storage: FAILED — {type(exc).__name__}: {exc}")

    missing_credentials = [c.token_env for c in settings().connectors
                           if c.token_env and not _secret_present(c.token_env)]
    if missing_credentials:
        lines.append("credentials: missing " + ", ".join(sorted(set(missing_credentials))))
    else:
        lines.append("credentials: every configured token environment variable is present")

    reachable = 0
    for probe in registry.health():
        if probe.get("ok"):
            reachable += 1
            lines.append(f"  reachable: {probe['id']} ({probe.get('latency_ms')} ms)")
        else:
            lines.append(f"  unreachable: {probe['id']} — {probe.get('error')}")
    if not reachable:
        ok = False
        lines.append("no application answered; MORPH cannot compose a workspace")

    from morph import hostllm
    lines.append("planner: host model available" if hostllm.available()
                 else "planner: no host model bound — deterministic composition")
    chain = store.verify_audit_chain()
    lines.append(f"audit chain: {'intact' if chain['ok'] else 'BROKEN'} ({chain['entries']} entries)")
    ok = ok and chain["ok"]
    lines.append("MORPH is healthy" if ok else "MORPH has problems (see above)")
    return {"ok": ok, "lines": lines}


def _secret_present(name: str) -> bool:
    from morph.config import get_secret
    try:
        return bool(get_secret(name))
    except MorphError:
        return False
