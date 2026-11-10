"""Catalog review of 1870be8: model-supplied ids stay in their path; secrets stay in scope."""
from __future__ import annotations

import sys
import types

import pytest

from morph import config
from morph.connectors.base import record_segment
from morph.connectors.github import _issue_number
from morph.errors import NotFound

HOSTILE = ["../../../other-owner/repo/issues/1", "..", "1/../2", "a/b", "1%2F2", "",
           " ", "1?x=1", "1#x", "-1", "x" * 81, "%2e%2e"]


@pytest.mark.parametrize("bad", HOSTILE + ["01", "abc", "1.5"])
def test_a_github_issue_is_only_ever_a_number(bad):
    with pytest.raises(NotFound):
        _issue_number(bad)


def test_a_real_issue_number_passes():
    assert _issue_number("42") == "42" and _issue_number(7) == "7"


@pytest.mark.parametrize("bad", HOSTILE)
def test_a_record_id_cannot_leave_its_path(bad):
    with pytest.raises(NotFound):
        record_segment(bad)


@pytest.mark.parametrize("good", ["ORD-2045", "SKU-HUB-03", "42", "case_7", "a.b:c"])
def test_ordinary_record_ids_pass_unchanged(good):
    assert record_segment(good) == good.replace(":", "%3A")


def test_the_github_connector_refuses_a_traversal_before_any_request(morph_home, monkeypatch):
    from morph.config import ConnectorConfig
    from morph.connectors.github import GitHubConnector

    built = GitHubConnector(ConnectorConfig(id="gh", kind="github", label="gh",
                                            options={"repo": "acme/widgets"}))
    sent = []
    built._request = lambda *a, **k: sent.append(a) or (200, {}, {})
    built.entity_or_raise = lambda entity: None
    with pytest.raises(NotFound):
        built.get("issues", "../../../other-owner/repo/issues/1")
    assert sent == []


def _fake_scope(monkeypatch, behaviour):
    module = types.ModuleType("agent.secret_scope")
    module.get_secret = behaviour
    package = types.ModuleType("agent")
    package.secret_scope = module
    monkeypatch.setitem(sys.modules, "agent", package)
    monkeypatch.setitem(sys.modules, "agent.secret_scope", module)


def test_inside_hermes_an_empty_scope_never_falls_back_to_the_process(monkeypatch):
    """Another profile's token in os.environ must not leak into this one."""
    monkeypatch.setenv("SOME_TOKEN", "another-profiles-token")
    _fake_scope(monkeypatch, lambda name, default="": "")
    assert config.get_secret("SOME_TOKEN") == ""


def test_inside_hermes_a_scope_error_fails_closed(monkeypatch):
    monkeypatch.setenv("SOME_TOKEN", "another-profiles-token")

    def broken(name, default=""):
        raise RuntimeError("no scope bound")
    _fake_scope(monkeypatch, broken)
    assert config.get_secret("SOME_TOKEN") == ""


def test_inside_hermes_the_scope_value_is_used(monkeypatch):
    monkeypatch.setenv("SOME_TOKEN", "process")
    _fake_scope(monkeypatch, lambda name, default="": " scoped ")
    assert config.get_secret("SOME_TOKEN") == "scoped"


def test_outside_hermes_the_process_environment_is_used(monkeypatch):
    monkeypatch.setitem(sys.modules, "agent", None)
    monkeypatch.setitem(sys.modules, "agent.secret_scope", None)
    monkeypatch.setenv("SOME_TOKEN", "process")
    assert config.get_secret("SOME_TOKEN") == "process"
