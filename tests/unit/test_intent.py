"""Intent interpretation: the reading of a goal that both planners work from."""
from __future__ import annotations

import pytest

from morph import intent


def read(goal, graph, **kwargs):
    return intent.interpret(goal, graph, **kwargs)


def test_mode_follows_the_kind_of_work_asked_for(graph):
    assert read("show me the deals that are stuck", graph).mode == "review"
    assert read("compare total deal value by stage", graph).mode == "analyse"
    assert read("flag the biggest deal", graph).mode == "act"
    assert read("monitor deals live", graph).mode == "monitor"
    assert read("deals", graph).mode == "explore"


def test_focus_is_ranked_and_explains_itself(graph):
    reading = read("which deals are in the won stage", graph)
    assert reading.primary == "crm.deals"
    assert reading.focus[0].named is True
    assert "won" in reading.focus[0].reason
    assert reading.focus[0].filters == ({"field": "stage", "op": "eq", "value": "won"},)


def test_an_action_word_does_not_also_become_a_filter(graph):
    """"Flag the deals" must not filter to deals already flagged — those are exactly
    the ones that do not need flagging."""
    reading = read("flag the qualified deals", graph)
    assert reading.mode == "act"
    fields = {f["field"] for f in reading.focus[0].filters}
    assert "flagged" not in fields
    assert {"field": "stage", "op": "eq", "value": "qualified"} in list(reading.focus[0].filters)


def test_an_entity_the_goal_never_named_is_not_narrowed_by_a_stray_word(graph):
    reading = read("deals in the east", graph)
    people = next((f for f in reading.focus if f.ref == "crm.people"), None)
    if people is not None and not people.named:
        assert all(f["field"] != "team" for f in people.filters) or people.named


def test_missing_prerequisites_are_reported_rather_than_papered_over(graph):
    reading = read("show me the invoices and their payment terms", graph)
    assert reading.missing
    assert any("nothing connected covers" in m for m in reading.missing)


def test_an_operation_that_exists_but_is_denied_says_so(graph):
    reading = read("close the Acme deal", graph,
                   denied_operations={"crm.deals.close_deal": "high-risk for your role"})
    assert any("not permitted for you" in m for m in reading.missing)
    assert any("crm.deals.close_deal" in m for m in reading.missing)


def test_ordinary_english_is_not_reported_as_a_missing_prerequisite(graph):
    reading = read("please show me the deals that are stuck right now", graph)
    assert not any("nothing connected covers" in m for m in reading.missing), reading.missing


def test_the_hint_given_to_the_model_carries_no_record_values(graph):
    hint = read("flag the won deals", graph).hint()
    blob = str(hint)
    for leaked in ("Acme renewal", "Globex", "Dana", "8400"):
        assert leaked not in blob
    assert hint["mode"] == "act"
    assert "crm.deals" in hint["likely_focus"]


def test_board_and_time_candidates_are_surfaced_for_the_planner(graph):
    reading = read("deals", graph)
    assert "crm.deals.stage" in reading.board_fields
    assert any(m.startswith("crm.deals.") for m in reading.metrics)


def test_a_synonym_reaches_the_schema_without_taking_the_goal_over(graph):
    """The operator says what they say; the schema says what it says. A declared
    synonym should bridge the two without outvoting a word they actually typed."""
    from morph.intent import SYNONYMS, expansions_of, tokens_of
    tokens = tokens_of("show the deals for unhappy clients")
    expansions = expansions_of(tokens)
    assert "customer" in expansions          # client → customer
    assert "deal" in tokens                  # and the word they typed is still first-class
    assert all(word not in tokens for word in expansions)

    reading = read("show the deals for unhappy clients", graph)
    assert reading.primary == "crm.deals"    # not flipped onto the synonym's entity


def test_synonyms_never_invent_a_filter(graph):
    """A synonym may draw attention to a field; it may not silently narrow on it."""
    reading = read("which deals look risky", graph)
    for entry in reading.focus:
        for predicate in entry.filters:
            descriptor = graph.field_of(entry.ref, predicate["field"])
            assert descriptor is not None
            assert predicate["value"] in descriptor.values


def test_the_synonym_table_stays_small_and_one_directional(graph):
    """It is a bridge, not a thesaurus: keep it reviewable."""
    from morph.intent import SYNONYMS
    assert len(SYNONYMS) <= 40
    for word, expansions in SYNONYMS.items():
        assert word.islower() and len(expansions) <= 4
        assert word not in expansions
