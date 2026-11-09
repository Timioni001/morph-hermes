"""The claim this project stands on, tested against three real services:

a goal becomes a useful interface over several independently running applications,
an approved action is executed in one of them, and the change is visible in that
application's own API afterwards.
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.request

from pathlib import Path

import pytest

from morph import actions, engine, registry, resolve, store, workspace
from morph.actions import Principal


def read_source(live_apps, app: str, path: str):
    request = urllib.request.Request(
        live_apps.url(app) + path,
        headers={"Authorization": "Bearer " + live_apps.tokens[app]})
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - loopback
        return json.loads(response.read())


def test_discovery_sees_three_independent_applications(live_config):
    found = engine.discover(Principal.operator("ops@example.com"), refresh=True)
    summary = found["summary"]
    assert {a["id"] for a in summary["applications"]} == {"storefront", "helpdesk", "warehouse"}
    assert "storefront.orders" in summary["entities"]
    assert "helpdesk.cases" in summary["entities"]
    assert "warehouse.stock_items" in summary["entities"]
    assert any(link["cross_application"] for link in summary["cross_application_links"])
    assert all(probe["ok"] for probe in found["health"])


def test_a_goal_becomes_a_workspace_resolved_from_live_data(live_config):
    operator = Principal.operator("ops@example.com")
    result = engine.plan_workspace(
        operator, "Work through delayed orders and the complaints about them")
    assert result["workspace"]["revision"] == 1
    assert "storefront.orders" in result["entities_used"]

    state = engine.state(operator)
    blocks = state["data"]["blocks"]
    assert state["data"]["degraded"] is False

    schema = state["workspace"]["schema"]
    lead = next(b for s in schema["sections"] for b in s["blocks"]
                if b["kind"] == "metric" and b.get("filters"))
    # The number on screen matches what the storefront itself reports.
    live = read_source(live_config, "storefront", "/v1/orders?status=delayed")
    assert blocks[lead["id"]]["value"] == live["total"] == 5

    relation = next((b for s in schema["sections"] for b in s["blocks"]
                     if b["kind"] == "relation"), None)
    assert relation is not None, "the planner should join two applications for this goal"
    resolved = blocks[relation["id"]]
    assert resolved["link"]["cross_application"] is True
    assert any(row["related"] for row in resolved["rows"])


def test_an_approved_action_lands_in_the_source_application(live_config):
    operator = Principal.operator("ops@example.com")
    engine.plan_workspace(operator, "delayed orders needing review")
    before = read_source(live_config, "storefront", "/v1/orders/ORD-2043")
    assert before["review_status"] == "none"

    proposal = engine.propose(operator, source="storefront", entity="orders",
                              record_id="ORD-2043", operation="flag_for_review",
                              params={"reason": "48h late, customer chasing"},
                              reason="Triage")
    # Proposing must not touch the source.
    assert read_source(live_config, "storefront", "/v1/orders/ORD-2043") == before

    applied = engine.approve(operator, proposal["id"],
                             confirm_token=proposal["confirm_token"])
    assert applied["status"] == "applied"
    assert applied["result"]["verification"]["verified"] is True

    after = read_source(live_config, "storefront", "/v1/orders/ORD-2043")
    assert after["review_status"] == "flagged"
    assert after["review_reason"] == "48h late, customer chasing"
    assert after["_version"] == before["_version"] + 1

    # The storefront logged the write itself, attributed to the human who approved.
    history = read_source(live_config, "storefront", "/v1/orders/ORD-2043/history")
    assert history["entries"][0]["operation"] == "flag_for_review"
    assert history["entries"][0]["actor"] == "morph:ops@example.com"

    # And MORPH can verify it again from scratch.
    assert engine.verify(proposal["id"])["verified"] is True


def test_an_action_in_a_second_application_works_the_same_way(live_config):
    operator = Principal.operator("ops@example.com")
    proposal = engine.propose(operator, source="helpdesk", entity="cases",
                              record_id="CASE-5103", operation="escalate_case",
                              params={"reason": "No tracking after 40h", "priority": "urgent"})
    engine.approve(operator, proposal["id"], confirm_token=proposal["confirm_token"])
    case = read_source(live_config, "helpdesk", "/v1/cases/CASE-5103")
    assert case["status"] == "escalated"
    assert case["priority"] == "urgent"
    assert case["escalation_reason"] == "No tracking after 40h"


def test_change_detection_reports_what_moved_in_the_sources(live_config):
    operator = Principal.operator("ops@example.com")
    engine.plan_workspace(operator, "stock running low and restocks to request")
    engine.state(operator)  # records the first signature

    proposal = engine.propose(operator, source="warehouse", entity="stock_items",
                              record_id="SKU-HUB-03", operation="request_restock",
                              params={"quantity": 40, "note": "two orders waiting"})
    engine.approve(operator, proposal["id"], confirm_token=proposal["confirm_token"])

    report = engine.refresh_data(operator)
    moved = {change["entity"]: change for change in report["changes"]}
    assert "warehouse.stock_items" in moved
    assert "SKU-HUB-03" in moved["warehouse.stock_items"]["updated"]
    assert read_source(live_config, "warehouse",
                       "/v1/stock_items/SKU-HUB-03")["restock_requested"] is True


def test_transformation_preserves_the_operator_context(live_config):
    operator = Principal.operator("ops@example.com")
    engine.plan_workspace(operator, "delayed orders and the complaints about them")
    engine.set_context(operator, {"selection": {"storefront.orders": "ORD-2045"},
                                  "pinned_filters": {"storefront.orders": [
                                      {"field": "channel", "op": "eq", "value": "marketplace"}]}})

    result = engine.plan_workspace(operator, "orders and the shipments running late")
    assert result["workspace"]["revision"] == 2
    assert result["transformed_from"]["goal"].startswith("delayed orders")
    assert any("ORD-2045" in item for item in result["context_carried"])

    state = engine.state(operator)
    assert state["workspace"]["context"]["selection"]["storefront.orders"] == "ORD-2045"
    # A pinned filter keeps narrowing the new workspace.
    schema = state["workspace"]["schema"]
    table = next(b for s in schema["sections"] for b in s["blocks"]
                 if b["kind"] == "table" and b["entity"] == "storefront.orders")
    rows = state["data"]["blocks"][table["id"]]["rows"]
    assert rows, "the pinned filter should still match something"
    assert all(row.get("channel", "marketplace") == "marketplace" for row in rows)


def test_restoring_an_earlier_revision(live_config):
    operator = Principal.operator("ops@example.com")
    engine.plan_workspace(operator, "delayed orders")
    engine.plan_workspace(operator, "stock levels")
    restored = engine.restore_revision(operator, 1)
    assert restored["revision"] == 3
    assert restored["goal"] == "delayed orders"
    assert len(workspace.revisions()) == 3


def test_one_application_down_degrades_instead_of_failing(live_apps, morph_home, monkeypatch):
    """A connector pointed at a dead port must not take the workspace with it."""
    config = live_apps.config()
    config["connectors"].append({
        "id": "ghost", "kind": "http-rest", "label": "Ghost",
        "base_url": "http://127.0.0.1:9", "token_env": "", "timeout": 1.0})
    for name, token in live_apps.tokens.items():
        monkeypatch.setenv(f"MORPH_{name.upper()}_TOKEN", token)
    monkeypatch.setenv("MORPH_CONFIG", json.dumps(config))
    from morph.config import settings
    settings.cache_clear()
    registry.reset()
    engine.invalidate()

    operator = Principal.operator("ops@example.com")
    found = engine.discover(operator, refresh=True)
    unavailable = {entry["id"] for entry in found["summary"]["unavailable"]}
    assert unavailable == {"ghost"}
    # The other three still compose a full workspace.
    result = engine.plan_workspace(operator, "delayed orders and the complaints about them")
    assert "storefront.orders" in result["entities_used"]
    state = engine.state(operator)
    assert state["data"]["degraded"] is False
    assert any(not probe["ok"] for probe in state["health"])


def test_a_source_that_dies_mid_session_marks_only_its_own_blocks(live_apps, morph_home,
                                                                  monkeypatch):
    for name, token in live_apps.tokens.items():
        monkeypatch.setenv(f"MORPH_{name.upper()}_TOKEN", token)
    monkeypatch.setenv("MORPH_CONFIG", json.dumps(live_apps.config()))
    from morph.config import settings
    settings.cache_clear()
    registry.reset()
    engine.invalidate()

    operator = Principal.operator("ops@example.com")
    engine.plan_workspace(operator, "delayed orders and the complaints about them")
    built = engine.graph()
    schema = workspace.load_schema(graph=built)

    # Kill whichever second application this workspace actually reads from, and
    # leave the storefront alive.
    victim = next(source for source in schema.sources_used if source != "storefront")
    connector = registry.connectors()[victim]
    connector.config = connector.config.__class__(
        **{**connector.config.__dict__, "base_url": "http://127.0.0.1:9"})
    connector.close()

    resolution = resolve.resolve(schema, built, context={})
    assert resolution.degraded is True
    assert any(failure["source"] == victim for failure in resolution.failures)

    victim_blocks = [block for block in schema.blocks
                     if block.entity.startswith(victim + ".")]
    assert victim_blocks and all("error" in resolution.blocks[block.id]
                                 for block in victim_blocks)
    # Storefront blocks keep working — except a relation block, which by definition
    # reads both sides and so fails with the side that died. That is the honest
    # outcome: MORPH does not show half a join as a whole one.
    independent = [block for block in schema.blocks
                   if block.entity.startswith("storefront.") and block.kind != "relation"]
    assert independent
    assert all("error" not in resolution.blocks[block.id] for block in independent)
    joins = [block for block in schema.blocks if block.kind == "relation"]
    assert all("error" in resolution.blocks[block.id] for block in joins
               if victim in (block.entity.split(".")[0],
                             next((l.target_entity.split(".")[0]
                                   for l in built.links_from(block.entity)
                                   if l.name == block.link), "")))


def test_the_plugin_registers_against_the_real_hermes_contract(live_config):
    """`register(ctx)` with a recording stub: the shape Hermes itself calls."""
    import importlib
    import morph.hostllm as hostllm

    plugin = importlib.import_module("__init__") if "__init__" in sys.modules else None
    spec = importlib.util.spec_from_file_location(
        "morph_plugin_under_test", str(__import__("pathlib").Path(__file__).resolve()
                                       .parents[2] / "__init__.py"))
    plugin = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = plugin
    spec.loader.exec_module(plugin)

    registered: dict[str, dict] = {"tools": {}, "hooks": {}, "commands": {}, "cli": {},
                                   "prompt": {}, "skills": {}, "events": [], "messages": []}

    class Ctx:
        llm = object()

        def emit(self, event, payload=None):
            registered["events"].append((event, payload))
            return 1

        def inject_message(self, content, role="user", **kwargs):
            registered["messages"].append(content)
            return True

        def register_skill(self, *, name, path, description="", **kwargs):
            registered["skills"][name] = (path, description)

        def get_config(self, key, default=None):
            return default

        def register_tool(self, *, name, toolset, schema, handler, **kwargs):
            registered["tools"][name] = (toolset, schema, handler)

        def register_hook(self, hook, callback, **kwargs):
            registered["hooks"][hook] = callback

        def register_command(self, *, name, handler, description="", args_hint=""):
            registered["commands"][name] = handler

        def register_cli_command(self, *, name, help, setup_fn, handler_fn=None, **kwargs):
            registered["cli"][name] = (setup_fn, handler_fn)

        def register_system_prompt_section(self, *, id, content, **kwargs):
            registered["prompt"][id] = content

    plugin.register(Ctx())
    manifest = __import__("yaml").safe_load(
        (__import__("pathlib").Path(__file__).resolve().parents[2] / "plugin.yaml")
        .read_text(encoding="utf-8"))
    assert sorted(registered["tools"]) == sorted(manifest["provides_tools"])
    assert sorted(registered["hooks"]) == sorted(manifest["provides_hooks"])
    assert "morph" in registered["commands"] and "morph" in registered["cli"]
    assert "morph" in registered["prompt"]
    assert sorted(registered["skills"]) == ["connect-an-application", "triage",
                                            "verified-change"]
    for path, description in registered["skills"].values():
        assert Path(path).is_file() and description
    for _toolset, schema, _handler in registered["tools"].values():
        assert schema["parameters"]["additionalProperties"] is False
    # No registered tool can approve anything.
    assert not any("approve" in name for name in registered["tools"])

    # The outward surfaces are live: proposing emits an event and leaves the operator
    # a line, because the agent cannot move the action any further on its own.
    from morph import actions, hostsurface
    from morph.actions import Principal
    engine.plan_workspace(Principal.operator("ops@example.com"), "delayed orders")
    engine.propose(Principal.agent(), source="storefront", entity="orders",
                   record_id="ORD-2041", operation="flag_for_review",
                   params={"reason": "late"})
    assert any(event == "action_proposed" for event, _ in registered["events"])
    assert any("waiting for your approval" in message for message in registered["messages"])
    hostllm.bind(None)
    hostsurface.bind(emit=None, notify=None)


def test_several_processes_can_open_the_same_demo_database_at_once(tmp_path):
    """Seeding must be idempotent and serialised.

    The service, a `--print-tokens` run and a test fixture all legitimately open
    the same SQLite file at the same time; without a write lock around the seed,
    two of them saw an empty table and both inserted.
    """
    import concurrent.futures
    import importlib
    import os

    os.environ["MORPH_DEMO_DATA_DIR"] = str(tmp_path)
    import apps._runtime as runtime
    importlib.reload(runtime)
    storefront = importlib.reload(importlib.import_module("apps.storefront"))

    def seed() -> None:
        runtime._init_db(storefront.SPEC)

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for future in [pool.submit(seed) for _ in range(6)]:
            future.result()

    import sqlite3
    connection = sqlite3.connect(str(tmp_path / "storefront.db"))
    try:
        count = connection.execute(
            "SELECT COUNT(*) FROM records WHERE entity='orders'").fetchone()[0]
    finally:
        connection.close()
    assert count == len(storefront.ORDERS.seed)
