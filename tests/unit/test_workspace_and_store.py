"""Workspace revisions, context handling and MORPH's own storage."""
from __future__ import annotations

import pytest

from morph import engine, planner, store, workspace
from morph.actions import Principal
from morph.errors import NotFound


@pytest.fixture
def operator(memory_config):
    return Principal.operator("ops@example.com")


def test_every_goal_makes_a_new_revision_and_keeps_the_old_one(operator, graph):
    engine.plan_workspace(operator, "deals that are new")
    engine.plan_workspace(operator, "people on the east team")
    history = workspace.revisions()
    assert [entry["revision"] for entry in history] == [2, 1]
    assert history[-1]["goal"] == "deals that are new"
    restored = engine.restore_revision(operator, 1)
    assert restored["revision"] == 3 and restored["goal"] == "deals that are new"


def test_restoring_a_revision_that_does_not_exist(operator, graph):
    engine.plan_workspace(operator, "deals")
    with pytest.raises(NotFound):
        engine.restore_revision(operator, 99)


def test_context_is_validated_against_the_live_graph(operator, graph):
    engine.plan_workspace(operator, "deals")
    result = engine.set_context(operator, {
        "selection": {"crm.deals": "D-1", "ghost.entity": "X"},
        "pinned_filters": {"crm.deals": [{"field": "stage", "op": "eq", "value": "new"},
                                         {"field": "stage", "op": "eq", "value": "invented"}]},
        "note": "looking at the renewal"})
    assert result["context"]["selection"] == {"crm.deals": "D-1"}
    assert any("ghost.entity" in item for item in result["dropped"])
    assert result["context"]["pinned_filters"]["crm.deals"] == [
        {"field": "stage", "op": "eq", "value": "new"}]
    assert result["context"]["note"] == "looking at the renewal"


def test_a_pinned_filter_narrows_the_resolved_data(operator, graph):
    engine.plan_workspace(operator, "deals")
    state = engine.state(operator)
    table = next(b for s in state["workspace"]["schema"]["sections"] for b in s["blocks"]
                 if b["kind"] == "table")
    assert len(state["data"]["blocks"][table["id"]]["rows"]) == 3

    engine.set_context(operator, {"pinned_filters": {
        "crm.deals": [{"field": "stage", "op": "eq", "value": "won"}]}})
    narrowed = engine.state(operator)
    rows = narrowed["data"]["blocks"][table["id"]]["rows"]
    assert [row["id"] for row in rows] == ["D-3"]
    assert narrowed["data"]["blocks"][table["id"]]["pinned_filters"]


def test_context_survives_a_transformation_that_still_shows_the_entity(operator, graph):
    engine.plan_workspace(operator, "deals")
    engine.set_context(operator, {"selection": {"crm.deals": "D-1"}})
    result = engine.plan_workspace(operator, "people and the deals they own")
    assert "selected crm.deals D-1" in result["context_carried"]
    assert engine.state(operator)["workspace"]["context"]["selection"]["crm.deals"] == "D-1"


def test_context_that_no_longer_fits_is_dropped_with_an_explanation(graph):
    """A workspace that no longer shows an entity cannot keep a selection in it."""
    from morph.uischema import validate
    people_only = validate({"title": "People", "summary": "", "sections": [{"title": "s",
        "blocks": [{"kind": "table", "title": "People", "entity": "crm.people",
                    "columns": ["id", "name"]}]}]}, graph)
    kept, carried, dropped = workspace.carry_context(
        {"selection": {"crm.deals": "D-1", "crm.people": "P-2"},
         "pinned_filters": {"crm.deals": [{"field": "stage", "op": "eq", "value": "won"}]}},
        people_only, graph)
    assert kept["selection"] == {"crm.people": "P-2"}
    assert carried == ["selected crm.people P-2"]
    assert any("crm.deals" in item for item in dropped)


def test_the_stored_workspace_is_revalidated_on_load(operator, graph, monkeypatch):
    engine.plan_workspace(operator, "deals")
    with store.write() as connection:
        connection.execute(
            "UPDATE workspaces SET schema_json=? WHERE id='main'",
            ('{"version":"morph.ui.v1","title":"t","summary":"","sections":'
             '[{"id":"s1","title":"s","blocks":[{"id":"b1","kind":"table",'
             '"entity":"crm.ghosts","title":"x","columns":["id"]}]}]}',))
    state = engine.state(operator)
    assert state["workspace"]["schema_error"]
    assert state["data"] == {}


def test_signatures_support_change_detection(operator, graph):
    from morph.resolve import diff_signatures
    before = {"crm.deals": {"D-1": 1, "D-2": 1}}
    after = {"crm.deals": {"D-1": 2, "D-3": 1}}
    changes = diff_signatures(before, after)
    assert changes[0]["updated"] == ["D-1"]
    assert changes[0]["added"] == ["D-3"]
    assert changes[0]["removed"] == ["D-2"]


def test_storage_lives_under_the_configured_data_directory(morph_home):
    assert store.db_path().parent == morph_home
    with store.read() as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"workspaces", "revisions", "proposals", "audit", "signatures"} <= tables


def test_morph_stores_no_business_records(operator, graph):
    engine.plan_workspace(operator, "deals")
    engine.state(operator)
    with store.read() as connection:
        blob = " ".join(str(row) for table in ("workspaces", "revisions", "signatures")
                        for row in connection.execute(f"SELECT * FROM {table}"))
    # Record ids appear in signatures (that is the point); field values never do.
    assert "Acme renewal" not in blob
    assert "Globex expansion" not in blob
