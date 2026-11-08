"""The dashboard backend, exercised exactly as Hermes mounts it."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
HEADERS = {"X-MORPH-Request": "1"}


@pytest.fixture
def client(live_config):
    spec = importlib.util.spec_from_file_location("morph_dashboard_api_test",
                                                  ROOT / "dashboard" / "plugin_api.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    app = FastAPI()
    app.include_router(module.router, prefix="/api/plugins/morph")
    with TestClient(app) as test_client:
        test_client.morph_app = app
        test_client.morph_module = module
        yield test_client


def test_the_router_exposes_only_the_intended_routes(client):
    paths = {getattr(route, "path", "") for route in client.morph_module.router.routes}
    assert "/approve/{proposal_id}" in paths, sorted(paths)
    # Nothing that would let a caller act without naming a proposal.
    assert not any("apply" in path or "execute" in path for path in paths)


def test_state_and_discover_are_readable(client):
    discover = client.get("/api/plugins/morph/discover?refresh=true")
    assert discover.status_code == 200
    assert len(discover.json()["graph"]["sources"]) == 3

    state = client.get("/api/plugins/morph/state")
    assert state.status_code == 200
    body = state.json()
    assert body["workspace"] is not None  # bootstrapped on first read
    assert body["principal"]["authority"] == "operator"
    assert body["audit_chain"]["ok"] is True


def test_the_full_goal_to_verified_action_flow(client):
    goal = client.post("/api/plugins/morph/goal",
                       json={"goal": "delayed orders needing review"}, headers=HEADERS)
    assert goal.status_code == 200, goal.text

    proposed = client.post("/api/plugins/morph/propose", headers=HEADERS, json={
        "source": "storefront", "entity": "orders", "record_id": "ORD-2041",
        "operation": "mark_followed_up", "params": {"note": "Called the customer"},
        "reason": "Customer chased twice"})
    assert proposed.status_code == 200, proposed.text
    proposal = proposed.json()
    assert proposal["status"] == "pending"

    # The pending list hands the operator the confirmation token they need.
    listed = client.get("/api/plugins/morph/proposals").json()
    assert listed[0]["confirm_token"]

    approved = client.post(f"/api/plugins/morph/approve/{proposal['id']}", headers=HEADERS,
                           json={"confirm_token": proposal["confirm_token"]})
    assert approved.status_code == 200, approved.text
    assert approved.json()["result"]["verification"]["verified"] is True

    verified = client.get(f"/api/plugins/morph/verify/{proposal['id']}").json()
    assert verified["verified"] is True

    record = client.get("/api/plugins/morph/record/storefront/orders/ORD-2041").json()
    assert record["record"]["follow_up"] == "done"


def test_mutating_routes_require_the_intent_header(client):
    for path, body in (("/api/plugins/morph/goal", {"goal": "anything at all"}),
                       ("/api/plugins/morph/refresh", {}),
                       ("/api/plugins/morph/context", {"selection": {}}),
                       ("/api/plugins/morph/propose",
                        {"source": "storefront", "entity": "orders", "record_id": "ORD-2041",
                         "operation": "flag_for_review", "params": {"reason": "x"}}),
                       ("/api/plugins/morph/approve/whatever", {"confirm_token": "x" * 12}),
                       ("/api/plugins/morph/reject/whatever", {}),
                       ("/api/plugins/morph/reload", {})):
        response = client.post(path, json=body)
        assert response.status_code == 403, f"{path} -> {response.status_code}"


def test_a_cross_site_caller_is_refused_even_with_the_header(client):
    response = client.post("/api/plugins/morph/goal", json={"goal": "anything at all"},
                           headers={**HEADERS, "Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403


def test_errors_carry_a_stable_machine_code(client):
    response = client.post("/api/plugins/morph/propose", headers=HEADERS, json={
        "source": "storefront", "entity": "orders", "record_id": "ORD-2041",
        "operation": "issue_refund", "params": {"reason": "nope"}})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "permission_denied"

    missing = client.get("/api/plugins/morph/record/storefront/orders/NOPE")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "not_found"


def test_request_bodies_are_bounded(client):
    response = client.post("/api/plugins/morph/goal", headers=HEADERS,
                           json={"goal": "x" * 900})
    assert response.status_code == 422
    response = client.post("/api/plugins/morph/propose", headers=HEADERS, json={
        "source": "storefront", "entity": "orders", "record_id": "ORD-2041",
        "operation": "flag_for_review", "params": {"reason": "y" * 5000}})
    # Accepted by the model but truncated/validated by the engine, never stored raw.
    if response.status_code == 200:
        assert len(response.json()["params"]["reason"]) <= 240
    else:
        assert response.status_code in (400, 422)
