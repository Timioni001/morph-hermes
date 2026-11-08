import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def load_core():
    spec = importlib.util.spec_from_file_location(
        "morph_test_core",
        ROOT / "core.py"
    )

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    return module


@pytest.fixture
def core(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "MORPH_DATA_DIR",
        str(tmp_path)
    )

    monkeypatch.delenv(
        "MORPH_REMOTE_JSON",
        raising=False
    )

    c = load_core()
    c.initialize()

    return c


def test_bootstrap_catalog_and_snapshot(core):
    assert len(core.catalog()["sources"]) == 3

    s = core.snapshot()

    assert s["goal"] == "Manage my business operations"

    assert any(
        w["value"] == 5
        for w in s["widgets"]
        if w["kind"] == "metric"
    )


def test_transform_changes_widgets_and_persists(core):
    original = core.workspace()["revision"]

    core.compose(
        "Show customers with delayed orders"
    )

    s = core.snapshot()

    assert s["revision"] == original + 1
    assert s["widgets"][0]["value"] == 3
    assert s["widgets"][2]["source"] == "orders"
    assert len(s["widgets"][2]["rows"]) == 3


def test_invalid_generation_is_rejected_without_mutation(core):
    first = core.workspace()["revision"]

    with pytest.raises(
        ValueError,
        match="Unknown source"
    ):
        core.compose(
            "Bad spec",
            {
                "widgets": [{
                    "kind": "table",
                    "source": "../../../etc",
                    "title": "pwn"
                }]
            }
        )

    assert core.workspace()["revision"] == first

    with pytest.raises(
        ValueError,
        match="Invalid table columns"
    ):
        core.validate_spec({
            "widgets": [{
                "kind": "table",
                "source": "orders",
                "fields": ["__proto__"]
            }]
        })


def test_human_approval_and_readback(core):
    p = core.propose_action(
        "support",
        "CS-201",
        "resolve_case"
    )

    assert p["approval_required"] is True

    assert core.list_records(
        "support"
    )[0]["status"] == "open"

    assert len(
        core.pending_actions()
    ) == 1

    executed = core.approve_action(
        p["id"]
    )

    assert executed["verified"] is True
    assert executed["record"]["status"] == "resolved"

    assert core.pending_actions() == []
    assert core.recent_events()[0]["action"] == "approved_action"

    with pytest.raises(ValueError):
        core.approve_action(p["id"])


def test_stale_approval_rejected(core):
    p = core.propose_action(
        "orders",
        "ORD-1042",
        "flag_for_review"
    )

    next_action = core.propose_action(
        "orders",
        "ORD-1042",
        "mark_followed_up"
    )

    core.approve_action(
        next_action["id"]
    )

    with pytest.raises(
        ValueError,
        match="record changed"
    ):
        core.approve_action(p["id"])

    assert core.list_records(
        "orders"
    )[0]["review_status"] == "new"


def test_reject_and_denied_operation(core):
    p = core.propose_action(
        "inventory",
        "SKU-01",
        "request_restock"
    )

    assert core.reject_action(
        p["id"]
    )["status"] == "rejected"

    assert core.list_records(
        "inventory"
    )[0]["restock_requested"] is False

    with pytest.raises(
        ValueError,
        match="not permitted"
    ):
        core.propose_action(
            "inventory",
            "SKU-01",
            "transfer_funds"
        )


def test_plugin_registration_and_model_design(core, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "morph_pkg",
        ROOT / "__init__.py",
        submodule_search_locations=[
            str(ROOT)
        ]
    )

    module = importlib.util.module_from_spec(spec)

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    tools = {}

    class Ctx:
        def __init__(self):
            self.llm = SimpleNamespace(
                complete_structured=self.complete
            )

        def register_tool(
            self,
            *,
            name,
            schema,
            handler,
            toolset
        ):
            tools[name] = handler

        def complete(self, **kwargs):
            assert kwargs["purpose"] == "morph.design"

            return SimpleNamespace(
                parsed={
                    "widgets": [{
                        "kind": "table",
                        "source": "orders",
                        "title": "Custom",
                        "fields": [
                            "id",
                            "customer"
                        ]
                    }]
                }
            )

    module.register(Ctx())

    assert set(tools) == {
        "morph_catalog",
        "morph_design",
        "morph_state",
        "morph_propose_action"
    }

    result = json.loads(
        tools["morph_design"]({
            "goal": "My custom operations view"
        })
    )

    assert result["success"] is True
    assert result["data"]["mode"] == "hermes-llm"


def test_fastapi_routes(core):
    spec = importlib.util.spec_from_file_location(
        "morph_test_api",
        ROOT / "dashboard" / "plugin_api.py"
    )

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    app = FastAPI()

    app.include_router(
        module.router,
        prefix="/api/plugins/morph"
    )

    client = TestClient(app)

    assert client.get(
        "/api/plugins/morph/state"
    ).status_code == 200

    resp = client.post(
        "/api/plugins/morph/quick-transform",
        json={"goal": "Check the inventory"}
    )

    assert resp.status_code == 200

    proposed = client.post(
        "/api/plugins/morph/propose",
        json={
            "source": "inventory",
            "record_id": "SKU-01",
            "operation": "request_restock"
        }
    )

    assert proposed.status_code == 200

    aid = proposed.json()["id"]

    assert client.post(
        f"/api/plugins/morph/approve/{aid}"
    ).json()["verified"] is True

    assert client.post(
        f"/api/plugins/morph/approve/{aid}"
    ).status_code == 409
