"""The MORPH showcase, end to end, in one command.

    python -m demo.run_demo                # run it and exit
    python -m demo.run_demo --serve        # ...then leave everything running so you
                                           #    can open the dashboard harness
    python -m demo.run_demo --keep-data    # reuse the demo databases instead of
                                           #    starting from a clean copy

What it does, in order:

  1. starts three independent applications (own process, own database, own token)
  2. reads one order straight from the Storefront, so you can see where it starts
  3. discovers all three schemas and builds the capability graph
  4. composes a workspace from a goal in plain language and resolves it live
  5. has the AGENT propose an action — and shows it refused when it tries to approve
  6. has the OPERATOR approve it, then verifies it by reading the record back
  7. reads the Storefront's own API and changelog, which is the proof
  8. changes the goal, showing the workspace transform with context carried over
  9. runs a second action against a different application
 10. re-reads every source and reports what moved, then verifies the audit chain

Every number and status printed comes from a real read performed at that moment.
The script exits non-zero if any step does not hold, so it is an acceptance test
as much as a demonstration.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BOLD, DIM, GREEN, RED, BLUE, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[34m", "\033[0m"
if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    BOLD = DIM = GREEN = RED = BLUE = RESET = ""

FAILURES: list[str] = []


def step(number: int, title: str) -> None:
    print(f"\n{BOLD}{BLUE}── {number}. {title}{RESET}")


def say(text: str = "") -> None:
    print(f"   {text}" if text else "")


def expect(label: str, condition: Any, detail: str = "") -> bool:
    ok = bool(condition)
    mark = f"{GREEN}✓{RESET}" if ok else f"{RED}✗{RESET}"
    print(f"   {mark} {label}" + (f"  {DIM}{detail}{RESET}" if detail else ""))
    if not ok:
        FAILURES.append(label)
    return ok


def source_get(base: str, token: str, path: str) -> Any:
    request = urllib.request.Request(base + path, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - loopback
        return json.loads(response.read())


def start_apps(data_dir: Path) -> tuple[list[subprocess.Popen], dict[str, str]]:
    from apps.serve import SERVICES, spec_of, wait_ready
    env = dict(os.environ)
    env["MORPH_DEMO_DATA_DIR"] = str(data_dir)
    env["PYTHONUNBUFFERED"] = "1"
    processes = []
    for name in SERVICES:
        processes.append(subprocess.Popen(
            [sys.executable, "-m", "uvicorn", f"apps.{name}:app", "--host", "127.0.0.1",
             "--port", str(spec_of(name).port), "--log-level", "warning"],
            cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    if not wait_ready(SERVICES):
        for process in processes:
            process.terminate()
        raise SystemExit("the demo applications did not start — is something on 8811-8813?")
    tokens = {}
    for name in SERVICES:
        token_env = spec_of(name).token_env
        token = os.environ.get(token_env, "").strip()
        if not token:
            token = (data_dir / f"{name}.token").read_text(encoding="utf-8").strip()
        tokens[name] = token
        os.environ[token_env] = token
    return processes, tokens


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--serve", action="store_true",
                        help="leave the applications and the dashboard harness running")
    parser.add_argument("--keep-data", action="store_true",
                        help="reuse existing demo data instead of a clean copy")
    args = parser.parse_args(argv)

    workdir = Path(tempfile.mkdtemp(prefix="morph-demo-"))
    app_data = Path(os.environ.get("MORPH_DEMO_DATA_DIR") or (workdir / "apps"))
    morph_data = Path(os.environ.get("MORPH_DATA_DIR") or (workdir / "morph"))
    if not args.keep_data:
        app_data = workdir / "apps"
        morph_data = workdir / "morph"
    app_data.mkdir(parents=True, exist_ok=True)
    morph_data.mkdir(parents=True, exist_ok=True)
    os.environ["MORPH_DEMO_DATA_DIR"] = str(app_data)
    os.environ["MORPH_DATA_DIR"] = str(morph_data)
    os.environ.pop("MORPH_CONFIG", None)

    print(f"{BOLD}MORPH — software that adapts to you{RESET}")
    say(f"{DIM}demo data: {workdir}{RESET}")

    processes: list[subprocess.Popen] = []
    harness: subprocess.Popen | None = None
    try:
        step(1, "Three independent applications start up")
        processes, tokens = start_apps(app_data)
        from apps.serve import SERVICES, spec_of
        for name in SERVICES:
            spec = spec_of(name)
            health = json.loads(urllib.request.urlopen(  # noqa: S310 - loopback
                f"http://127.0.0.1:{spec.port}/health", timeout=5).read())
            say(f"{spec.label:<11} http://127.0.0.1:{spec.port}   "
                f"{DIM}own database · own bearer token · entities: "
                f"{', '.join(health['entities'])}{RESET}")
        expect("all three applications answer", True)

        storefront = "http://127.0.0.1:8811"
        order_id = "ORD-2045"

        step(2, "Where the Storefront starts, read from the Storefront itself")
        before = source_get(storefront, tokens["storefront"], f"/v1/orders/{order_id}")
        say(f"{order_id}: status={before['status']} hours_late={before['hours_late']} "
            f"review_status={before['review_status']!r} version={before['_version']}")
        expect("the order has not been reviewed yet", before["review_status"] == "none")

        from morph import engine, store
        from morph.actions import Principal
        engine.reload()
        agent = Principal.agent()
        operator = Principal.operator("ops@example.com", "Ops lead")

        step(3, "MORPH discovers what it is connected to")
        found = engine.discover(operator, refresh=True)
        summary = found["summary"]
        for application in summary["applications"]:
            say(f"{application['id']:<11} {', '.join(application['entities'])}")
        links = [f"{link['from']} → {link['to']}"
                 for link in summary["cross_application_links"] if "reverse" not in link["name"]]
        say(f"{DIM}cross-application links: {'; '.join(links)}{RESET}")
        say(f"{DIM}operations available: {len(summary['operations'])}{RESET}")
        expect("three applications discovered", len(summary["applications"]) == 3)
        expect("relationships span applications", bool(links))
        denied = [entry["ref"] for entry in found["permissions"] if not entry["allowed"]]
        expect("the role's denials are known up front", bool(denied), ", ".join(denied))

        step(4, "A goal becomes a workspace")
        goal = "Work through delayed orders and the customer complaints about them"
        say(f"goal: {BOLD}{goal}{RESET}")
        planned = engine.plan_workspace(agent, goal)
        say(f"planned {DIM}({planned['plan']['mode']}){RESET}, revision "
            f"{planned['workspace']['revision']}")
        state = engine.state(operator)
        blocks = state["data"]["blocks"]
        for section in state["workspace"]["schema"]["sections"]:
            say(f"{BOLD}{section['title']}{RESET}")
            for block in section["blocks"]:
                resolved = blocks.get(block["id"], {})
                if block["kind"] == "metric":
                    detail = f"= {resolved.get('value')}"
                elif block["kind"] in ("table", "relation"):
                    detail = f"{len(resolved.get('rows') or [])} row(s)"
                elif block["kind"] == "chart":
                    detail = ", ".join(f"{p['label']}={p['value']}"
                                       for p in (resolved.get("points") or [])[:4])
                else:
                    detail = ""
                say(f"  {block['kind']:<9} {block['title']:<34} {DIM}{detail}{RESET}")
        expect("the workspace is not degraded", state["data"]["degraded"] is False)
        expect("it reads from more than one application",
               len({entity.split(".")[0] for entity in
                    state["workspace"]["schema"]["entities_used"]}) >= 2,
               ", ".join(state["workspace"]["schema"]["entities_used"]))
        relation = next((b for s in state["workspace"]["schema"]["sections"]
                         for b in s["blocks"] if b["kind"] == "relation"), None)
        if relation:
            joined = blocks[relation["id"]]
            say(f"{DIM}joined: {joined['link']['from']} → {joined['link']['to']}{RESET}")
            expect("the join pulled related records across applications",
                   any(row["related"] for row in joined["rows"]))

        step(5, "The agent proposes — and cannot approve")
        proposal = engine.propose(
            agent, source="storefront", entity="orders", record_id=order_id,
            operation="flag_for_review",
            params={"reason": f"{before['hours_late']}h late with an open complaint"},
            reason="Chasing the oldest delayed orders")
        say(f"proposal {proposal['id'][:12]} · {proposal['operation_ref']} on {order_id}")
        say(f"would set: " + ", ".join(f"{k} = {v!r}" for k, v in proposal["predicted"].items()))
        expect("the agent was not given a confirmation token",
               "confirm_token" not in proposal)
        unchanged = source_get(storefront, tokens["storefront"], f"/v1/orders/{order_id}")
        expect("nothing changed in the Storefront", unchanged == before,
               f"still version {unchanged['_version']}")
        from morph.errors import ApprovalRequired
        try:
            engine.approve(agent, proposal["id"], confirm_token="guessed")
            expect("the agent was refused", False)
        except ApprovalRequired as exc:
            expect("the agent was refused", True, exc.message[:72] + "…")

        step(6, "A human approves, and MORPH verifies the result")
        from morph import actions
        full = actions.get_proposal(proposal["id"], include_token=True)
        applied = engine.approve(operator, proposal["id"],
                                 confirm_token=full["confirm_token"])
        verification = applied["result"]["verification"]
        say(f"status {applied['status']}, verified={verification['verified']}, "
            f"fields checked: {', '.join(verification['checked'])}")
        expect("the action applied", applied["status"] == "applied")
        expect("the read-back matched what the operation promised",
               verification["verified"] is True)

        step(7, "The proof: the Storefront's own API")
        after = source_get(storefront, tokens["storefront"], f"/v1/orders/{order_id}")
        say(f"{order_id}: review_status={after['review_status']!r} "
            f"review_reason={after['review_reason']!r} version={after['_version']}")
        history = source_get(storefront, tokens["storefront"],
                             f"/v1/orders/{order_id}/history")["entries"][0]
        say(f"{DIM}storefront changelog #{history['seq']}: {history['operation']} "
            f"by {history['actor']}{RESET}")
        expect("the Storefront's own record changed", after["review_status"] == "flagged")
        expect("its version advanced by exactly one",
               after["_version"] == before["_version"] + 1)
        expect("the Storefront attributes the write to the approver",
               history["actor"] == "morph:ops@example.com")

        step(8, "The goal changes; the workspace transforms and keeps your context")
        engine.set_context(operator, {"selection": {"storefront.orders": order_id}})
        new_goal = "Show stock running low and the restocks I should request"
        say(f"goal: {BOLD}{new_goal}{RESET}")
        transformed = engine.plan_workspace(operator, new_goal)
        say(f"revision {transformed['workspace']['revision']} "
            f"{DIM}(was {transformed['transformed_from']['revision']}: "
            f"{transformed['transformed_from']['goal']}){RESET}")
        for section in transformed["workspace"]["schema"]["sections"]:
            say(f"  {section['title']}: " + ", ".join(b["title"] for b in section["blocks"]))
        say(f"carried over: {'; '.join(transformed['context_carried']) or 'nothing'}")
        if transformed["context_dropped"]:
            say(f"{DIM}dropped: {'; '.join(transformed['context_dropped'])}{RESET}")
        expect("the workspace is now about the warehouse",
               any(entity.startswith("warehouse.")
                   for entity in transformed["entities_used"]),
               ", ".join(transformed["entities_used"]))
        expect("the earlier revision is still there",
               transformed["workspace"]["revision"] > 1)
        # Looking at the new workspace is also what records the baseline the next
        # refresh compares against.
        transformed_state = engine.state(operator)
        stock_table = next((b for s in transformed_state["workspace"]["schema"]["sections"]
                            for b in s["blocks"]
                            if b["kind"] == "table" and b["entity"].startswith("warehouse.")),
                           None)
        if stock_table:
            rows = transformed_state["data"]["blocks"][stock_table["id"]]["rows"]
            say(f"{DIM}{stock_table['title']}: {len(rows)} row(s) read live from the "
                f"warehouse{RESET}")

        step(9, "A second action, in a different application")
        sku = "SKU-HUB-03"
        warehouse = "http://127.0.0.1:8813"
        stock_before = source_get(warehouse, tokens["warehouse"], f"/v1/stock_items/{sku}")
        say(f"{sku}: available={stock_before['available']} "
            f"reorder_level={stock_before['reorder_level']} "
            f"restock_requested={stock_before['restock_requested']}")
        restock = engine.propose(agent, source="warehouse", entity="stock_items",
                                 record_id=sku, operation="request_restock",
                                 params={"quantity": 48, "note": "two delayed orders waiting"},
                                 reason="Below the reorder level with open demand")
        token = actions.get_proposal(restock["id"], include_token=True)["confirm_token"]
        restock_applied = engine.approve(operator, restock["id"], confirm_token=token)
        stock_after = source_get(warehouse, tokens["warehouse"], f"/v1/stock_items/{sku}")
        say(f"{sku}: restock_requested={stock_after['restock_requested']} "
            f"restock_quantity={stock_after['restock_quantity']} "
            f"version={stock_after['_version']}")
        expect("the warehouse recorded the restock request",
               stock_after["restock_requested"] is True and stock_after["restock_quantity"] == 48)
        expect("MORPH verified it too",
               restock_applied["result"]["verification"]["verified"] is True)

        step(10, "Re-read every source: what moved, and is the log intact")
        report = engine.refresh_data(operator)
        for change in report["changes"]:
            counts = change["counts"]
            say(f"{change['entity']}: +{counts['added']} −{counts['removed']} "
                f"~{counts['updated']}"
                + (f"  {DIM}updated: {', '.join(change['updated'][:4])}{RESET}"
                   if change["updated"] else ""))
        if not report["changes"]:
            say("no changes since the last read")
        chain = store.verify_audit_chain()
        say(f"audit log: {chain['entries']} entries, chain "
            f"{'intact' if chain['ok'] else 'BROKEN'}")
        for entry in reversed(store.audit_tail(8)):
            say(f"{DIM}#{entry['seq']:<3} {entry['kind']:<20} {entry['subject']:<42} "
                f"{entry['actor']}{RESET}")
        expect("the audit chain verifies", chain["ok"] is True)
        expect("both actions are in the log",
               sum(1 for entry in store.audit_tail(50) if entry["kind"] == "applied") == 2)

        print()
        if FAILURES:
            print(f"{RED}{BOLD}{len(FAILURES)} expectation(s) did not hold:{RESET}")
            for failure in FAILURES:
                print(f"   - {failure}")
            return 1
        print(f"{GREEN}{BOLD}Every expectation held.{RESET} A goal became a workspace over three "
              f"applications, an approved action was executed in one of them, and the change "
              f"was read back from that application's own API.")

        if args.serve:
            harness = subprocess.Popen(
                [sys.executable, "-m", "tests.e2e.harness.serve", "--port", "8820"],
                cwd=str(ROOT), env=dict(os.environ))
            print(f"\n{BOLD}Open http://127.0.0.1:8820/{RESET} for the MORPH dashboard "
                  f"(served through the Hermes plugin contract).")
            print("Source applications: http://127.0.0.1:8811 · :8812 · :8813")
            print("Press Ctrl-C to stop everything.")
            try:
                signal.pause()
            except (KeyboardInterrupt, AttributeError):
                pass
        return 0
    finally:
        for process in ([harness] if harness else []) + processes:
            if process and process.poll() is None:
                process.terminate()
        for process in ([harness] if harness else []) + processes:
            if process:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
        if not args.keep_data and not os.environ.get("MORPH_DEMO_KEEP"):
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
