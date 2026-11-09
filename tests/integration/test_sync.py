"""Change synchronisation against the real applications."""
from __future__ import annotations

import json
import os
import urllib.request

from morph import engine, sync
from morph.actions import Principal


def source_write(live_apps, app: str, path: str, params: dict, actor: str) -> None:
    request = urllib.request.Request(
        live_apps.url(app) + path, method="POST",
        data=json.dumps({"params": params}).encode(),
        headers={"Authorization": "Bearer " + live_apps.tokens[app],
                 "Content-Type": "application/json", "X-Actor": actor})
    urllib.request.urlopen(request, timeout=10).read()  # noqa: S310 - loopback


def test_the_first_poll_is_history_not_news(live_config):
    operator = Principal.operator("ops@example.com")
    engine.plan_workspace(operator, "delayed orders")
    source_write(live_config, "storefront", "/v1/orders/ORD-2041/operations/flag_for_review",
                 {"reason": "before MORPH ever looked"}, "someone-else")
    first = sync.poll("main")
    assert first["mechanism"] == "cursor"
    assert first["changes"] == []          # pre-existing history is not announced
    assert first["backfilled"] >= 1
    assert sync.cursors("main")


def test_a_change_made_outside_morph_is_detected_and_attributed(live_config):
    operator = Principal.operator("ops@example.com")
    engine.plan_workspace(operator, "support cases")
    engine.state(operator)                  # seeds cursors
    source_write(live_config, "helpdesk", "/v1/cases/CASE-5108/operations/assign_case",
                 {"assignee": "AG-4"}, "a-person-in-the-helpdesk-ui")
    report = sync.poll("main")
    assert [c["record_id"] for c in report["changes"]] == ["CASE-5108"]
    assert report["changes"][0]["actor"] == "a-person-in-the-helpdesk-ui"
    assert report["entities_touched"] == ["helpdesk.cases"]
    # ...and the next poll is quiet.
    assert sync.poll("main")["changes"] == []


def test_morphs_own_write_shows_up_in_the_sources_feed(live_config):
    operator = Principal.operator("ops@example.com")
    engine.plan_workspace(operator, "delayed orders needing review")
    engine.state(operator)
    proposal = engine.propose(operator, source="storefront", entity="orders",
                              record_id="ORD-2045", operation="flag_for_review",
                              params={"reason": "75h late"})
    engine.approve(operator, proposal["id"], confirm_token=proposal["confirm_token"])
    report = engine.refresh_data(operator)
    feed = report["source_feed"]
    assert feed["mechanism"] == "cursor"
    assert any(c["record_id"] == "ORD-2045" and c["actor"] == "morph:ops@example.com"
               for c in feed["changes"])
    # The refresh names the blocks that need redrawing rather than the whole page.
    assert report["affected_blocks"]


def test_a_source_without_a_feed_degrades_to_signature_diffing(memory_config, morph_home):
    operator = Principal.operator("ops@example.com")
    engine.plan_workspace(operator, "deals")
    engine.state(operator)
    report = sync.poll("main")
    assert report["mechanism"] == "none"
    assert report["sources_without_a_feed"] == ["crm"]
