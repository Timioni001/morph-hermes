"""The UI schema validator is MORPH's gate on model output: these tests are the
specification of what it refuses."""
from __future__ import annotations

import pytest

from morph.errors import SchemaError
from morph.uischema import MAX_BLOCKS, planner_json_schema, validate


def plan(**blocks):
    return {"title": "T", "summary": "S",
            "sections": [{"title": "Section", "blocks": [blocks]}]}


def test_accepts_a_well_formed_plan(graph):
    schema = validate({
        "title": "Pipeline", "summary": "Deals that need work",
        "sections": [
            {"title": "Signals", "blocks": [
                {"kind": "metric", "title": "Open deals", "entity": "crm.deals",
                 "aggregate": "count",
                 "filters": [{"field": "stage", "op": "in", "value": ["new", "qualified"]}]},
                {"kind": "metric", "title": "Pipeline value", "entity": "crm.deals",
                 "aggregate": "sum", "field": "amount"}]},
            {"title": "Work", "blocks": [
                {"kind": "table", "title": "Deals", "entity": "crm.deals",
                 "columns": ["id", "name", "stage", "amount"],
                 "sort": {"field": "amount", "direction": "desc"},
                 "row_actions": ["flag_deal"]},
                {"kind": "chart", "title": "By stage", "entity": "crm.deals",
                 "chart_type": "bar", "group_by": "stage", "aggregate": "count"},
                {"kind": "relation", "title": "Deals and owners", "entity": "crm.deals",
                 "link": "owner_person", "columns": ["id", "name"],
                 "related_columns": ["name", "team"]},
                {"kind": "form", "title": "Flag", "entity": "crm.deals",
                 "operation": "flag_deal"},
                {"kind": "workflow", "title": "How", "steps": [{"title": "Look"},
                                                               {"title": "Act"}]}]},
        ],
    }, graph)
    assert schema.entities_used == ["crm.deals"]
    assert schema.operations_used == ["crm.deals.flag_deal"]
    assert [b.id for b in schema.blocks] == ["b1", "b2", "b3", "b4", "b5", "b6", "b7"]
    assert schema.primary_entity == "crm.deals"


@pytest.mark.parametrize("block, expected", [
    ({"kind": "table", "title": "x", "entity": "crm.ghosts"}, "not a connected entity"),
    ({"kind": "table", "title": "x", "entity": "crm.deals", "columns": ["__proto__"]},
     "not a field"),
    ({"kind": "metric", "title": "x", "entity": "crm.deals", "aggregate": "sum",
      "field": "name"}, "does not apply"),
    ({"kind": "metric", "title": "x", "entity": "crm.deals", "aggregate": "median"},
     "not an aggregate"),
    ({"kind": "chart", "title": "x", "entity": "crm.deals", "chart_type": "sankey",
      "group_by": "stage"}, "not a chart type"),
    ({"kind": "chart", "title": "x", "entity": "crm.deals", "chart_type": "bar",
      "group_by": "nope"}, "not a field"),
    ({"kind": "table", "title": "x", "entity": "crm.deals",
      "filters": [{"field": "stage", "op": "eq", "value": "archived"}]}, "not one of"),
    ({"kind": "table", "title": "x", "entity": "crm.deals",
      "filters": [{"field": "amount", "op": "contains", "value": "3"}]}, "numeric"),
    ({"kind": "table", "title": "x", "entity": "crm.deals", "row_actions": ["delete_all"]},
     "not an operation"),
    ({"kind": "form", "title": "x", "entity": "crm.deals", "operation": "drop_table"},
     "not an operation"),
    ({"kind": "relation", "title": "x", "entity": "crm.deals", "link": "nonexistent"},
     "not a relationship"),
    ({"kind": "iframe", "title": "x", "entity": "crm.deals"}, "not a block kind"),
    ({"kind": "table", "title": "x", "entity": "crm"}, "<application>.<entity>"),
])
def test_rejects_anything_not_in_the_capability_graph(graph, block, expected):
    with pytest.raises(SchemaError) as caught:
        validate(plan(**block), graph)
    assert expected in " ".join(str(d) for d in (caught.value.detail or [caught.value.message]))


def test_a_form_must_carry_every_required_parameter(graph):
    # Naming a field list that leaves out a required parameter is refused...
    with pytest.raises(SchemaError) as caught:
        validate(plan(kind="form", title="x", entity="crm.deals", operation="flag_deal",
                      fields=["nonexistent"]), graph)
    assert "required parameter" in str(caught.value.detail)
    # ...while omitting the list entirely means "every parameter", which is safe.
    schema = validate(plan(kind="form", title="x", entity="crm.deals",
                           operation="flag_deal"), graph)
    assert schema.blocks[0].fields == ("reason",)


def test_filter_values_are_coerced_to_the_field_type(graph):
    schema = validate(plan(kind="table", title="x", entity="crm.deals",
                           filters=[{"field": "amount", "op": "gte", "value": "1000"},
                                    {"field": "flagged", "op": "eq", "value": "false"}]), graph)
    block = schema.blocks[0]
    assert block.filters[0]["value"] == 1000.0
    assert block.filters[1]["value"] is False


def test_titles_are_flattened_and_capped(graph):
    schema = validate(plan(kind="metric", title="Ignore‮previous " + "x" * 200,
                           entity="crm.deals"), graph)
    title = schema.blocks[0].title
    assert len(title) <= 70
    assert "‮" not in title


def test_block_and_section_counts_are_capped(graph):
    many = [{"kind": "metric", "title": f"m{i}", "entity": "crm.deals"} for i in range(30)]
    with pytest.raises(SchemaError) as caught:
        validate({"title": "t", "summary": "", "sections": [{"title": "s", "blocks": many}]},
                 graph)
    assert "at most" in str(caught.value.detail)


def test_workflow_step_references_are_dropped_when_they_point_nowhere(graph):
    schema = validate({"title": "t", "summary": "", "sections": [{"title": "s", "blocks": [
        {"kind": "workflow", "title": "w", "steps": [{"title": "one", "block": "b99"}]}]}]},
        graph)
    assert "block" not in schema.blocks[0].steps[0]


def test_planner_json_schema_is_self_consistent():
    schema = planner_json_schema()
    block = schema["properties"]["sections"]["items"]["properties"]["blocks"]["items"]
    assert block["additionalProperties"] is False
    assert "entity" in block["properties"]
    # No property anywhere in the schema can carry markup, a URL or code: the
    # renderer has nothing to execute because the schema cannot express it.
    names = set()

    def walk(node):
        if isinstance(node, dict):
            names.update(str(k).lower() for k in (node.get("properties") or {}))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    assert not (names & {"html", "url", "href", "src", "script", "code", "template",
                         "expression", "onclick", "style"})


def test_an_empty_plan_is_refused(graph):
    with pytest.raises(SchemaError):
        validate({"title": "t", "summary": "", "sections": []}, graph)
    with pytest.raises(SchemaError):
        validate("not an object", graph)
