"""The authority boundary, from the agent's side."""
from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import pytest

from morph import actions, engine, schemas, tools
from morph.actions import Authority, Principal

ROOT = Path(__file__).resolve().parents[2]


def test_no_registered_tool_can_apply_a_change():
    for name, schema in schemas.TOOLS.items():
        text = (schema["description"] + json.dumps(schema["parameters"])).lower()
        assert "approve" not in name
        if name == "morph_propose_action":
            assert "does not apply" in text or "not apply" in text
    assert set(tools.TOOL_NAMES) == set(schemas.TOOLS)


def test_tool_handlers_always_run_as_the_agent_principal(morph_home, memory_config):
    """A tool argument can never raise the caller's authority."""
    source = inspect.getsource(tools)
    assert "Principal.operator" not in source
    assert source.count("Principal.agent()") >= 1
    handlers = tools.build_handlers()
    result = json.loads(handlers["morph_discover"]({}))
    assert result["ok"] is True
    assert result["data"]["your_permissions"]


def test_the_propose_tool_never_returns_a_confirmation_token(memory_config, graph):
    handlers = tools.build_handlers()
    answer = json.loads(handlers["morph_propose_action"]({
        "source": "crm", "entity": "deals", "record_id": "D-1", "operation": "flag_deal",
        "params": {"reason": "because"}}))
    assert answer["ok"] is True
    data = answer["data"]
    assert data["applied"] is False
    assert "confirm_token" not in json.dumps(data)
    assert "person must approve" in data["next_step"]


def test_tools_never_raise_and_always_return_json(memory_config, graph):
    handlers = tools.build_handlers()
    for name, handler in handlers.items():
        for args in ({}, {"goal": ""}, {"proposal_id": "nope"},
                     {"source": "x", "entity": "y", "record_id": "z", "operation": "w"},
                     {"unexpected": object()}):
            body = handler(args)
            assert isinstance(body, str)
            parsed = json.loads(body)
            assert "ok" in parsed
            if parsed["ok"] is False:
                assert parsed["error"]


def test_the_pre_tool_call_hook_closes_the_morph_namespace():
    import importlib.util
    spec = importlib.util.spec_from_file_location("morph_plugin_guard", ROOT / "__init__.py")
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    blocked = plugin._pre_tool_call(tool_name="morph_approve", args={})
    assert blocked["action"] == "block"
    assert plugin._pre_tool_call(tool_name="morph_discover", args={}) is None
    assert plugin._pre_tool_call(tool_name="read_file", args={}) is None


def test_the_cli_refuses_to_approve_without_a_terminal(memory_config, graph, monkeypatch):
    from morph import cli
    from morph.errors import PermissionDenied
    operator = Principal.operator("ops@example.com")
    proposal = engine.propose(operator, source="crm", entity="deals", record_id="D-1",
                              operation="flag_deal", params={"reason": "x"})
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)

    class Args:
        proposal_id = proposal["id"]

    with pytest.raises(PermissionDenied) as caught:
        cli._approve(operator, Args(), False)
    assert "interactive terminal" in caught.value.message


def test_the_agent_role_cannot_be_given_approval_by_configuration(morph_home, monkeypatch):
    monkeypatch.setenv("MORPH_CONFIG", json.dumps({
        "connectors": [],
        "roles": {"agent": {"allow": ["*"], "can_approve": True, "max_risk": "high"}}}))
    from morph.config import settings
    settings.cache_clear()
    assert settings().roles["agent"].can_approve is False
    assert Principal.agent().authority is Authority.AGENT
    assert Principal.agent().can_approve is False


def test_the_source_tree_contains_no_second_approval_path():
    """Within the product surfaces, approval is reachable from exactly two places:
    the authenticated dashboard route and the interactive CLI (plus the engine
    function they both call). A grep is a blunt instrument, and that is the point:
    a new call site has to be noticed deliberately.

    ``tests/`` and ``demo/`` are excluded because both are scripts a person runs,
    standing in for the operator on purpose.
    """
    callers = []
    for path in ROOT.rglob("*.py"):
        if any(part in path.parts for part in (".venv", "node_modules", "tests", "demo")):
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"\bengine\.approve\b|\bactions\.approve\b", text):
            callers.append(path.relative_to(ROOT).as_posix())
    assert sorted(set(callers)) == ["dashboard/plugin_api.py", "morph/cli.py", "morph/engine.py"]
