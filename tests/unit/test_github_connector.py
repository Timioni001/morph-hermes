"""The GitHub connector: a real third-party API behind the same adapter contract.

Driven through a fake transport so these run anywhere, with no token and no network.
``scripts/check_github_connector.py`` is the live counterpart.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from morph.config import ConnectorConfig
from morph.connectors.github import GitHubConnector, _epoch, _issue_record
from morph.errors import ConfigError, ConnectorError, NotFound, PermissionDenied, StaleRecord


class FakeResponse:
    def __init__(self, status: int, payload: Any = None, headers: dict | None = None):
        self.status_code = status
        self.content = b"" if payload is None else json.dumps(payload).encode()
        self.headers = headers or {}


class FakeGitHub:
    """Enough of the GitHub API to exercise the adapter honestly."""

    def __init__(self, issues: list[dict] | None = None, login: str = "octocat"):
        self.issues = {str(i["number"]): i for i in (issues or [])}
        self.login = login
        self.calls: list[tuple[str, str, Any]] = []
        self.fail_times = 0
        self.fail_status = 500

    def request(self, method, url, params=None, json=None, headers=None):  # noqa: A002
        self.calls.append((method, url, json))
        if self.fail_times > 0:
            self.fail_times -= 1
            return FakeResponse(self.fail_status, {"message": "slow down"},
                                {"retry-after": "0", "x-ratelimit-remaining": "0"})
        if url.endswith("/user"):
            return FakeResponse(200, {"login": self.login})
        if url.endswith("/repos/acme/widgets"):
            return FakeResponse(200, {"full_name": "acme/widgets"})
        if url.endswith("/repos/acme/widgets/issues"):
            state = dict(params or {}).get("state", "all") if isinstance(params, dict) else "all"
            if isinstance(params, list):
                state = dict(params).get("state", "all")
            rows = list(self.issues.values())
            if state in ("open", "closed"):
                rows = [r for r in rows if r["state"] == state]
            return FakeResponse(200, rows)
        if "/issues/" in url:
            number = url.rsplit("/", 1)[-1]
            issue = self.issues.get(number)
            if issue is None:
                return FakeResponse(404, {"message": "Not Found"})
            if method == "PATCH":
                if "state" in (json or {}):
                    issue["state"] = json["state"]
                if "state_reason" in (json or {}):
                    issue["state_reason"] = json["state_reason"]
                if "labels" in (json or {}):
                    issue["labels"] = [{"name": name} for name in json["labels"]]
                if "title" in (json or {}):
                    issue["title"] = json["title"]
                if "assignees" in (json or {}):
                    issue["assignee"] = ({"login": json["assignees"][0]}
                                         if json["assignees"] else None)
                issue["updated_at"] = "2026-11-09T12:00:00Z"
                return FakeResponse(200, issue)
            return FakeResponse(200, issue)
        return FakeResponse(404, {"message": "Not Found"})


def issue(number, state="open", assignee=None, updated="2026-11-08T10:00:00Z", **extra):
    return {"number": number, "title": f"Issue {number}", "state": state,
            "user": {"login": "reporter"},
            "assignee": {"login": assignee} if assignee else None,
            "labels": [{"name": "bug"}, {"name": "p1"}], "comments": 3,
            "milestone": None, "created_at": "2026-11-01T09:00:00Z",
            "updated_at": updated, **extra}


@pytest.fixture
def connector(morph_home):
    config = ConnectorConfig(id="github", kind="github", label="GitHub",
                             token_env="MORPH_GITHUB_TOKEN", max_retries=2,
                             retry_backoff=0.01, options={"repo": "acme/widgets"})
    built = GitHubConnector(config)
    built._client = FakeGitHub([issue(1), issue(2, state="closed"), issue(3, assignee="octocat")])
    built._headers = lambda: {"Authorization": "Bearer test"}  # noqa: SLF001
    return built


def test_the_repository_must_be_named_properly(morph_home):
    for bad in ("", "no-slash", "a/b/c", "../etc", ".hidden/x", "a/..", "a b/c"):
        with pytest.raises(ConfigError):
            GitHubConnector(ConnectorConfig(id="g", kind="github", label="g",
                                            options={"repo": bad}))


def test_a_github_issue_becomes_a_typed_morph_record():
    record = _issue_record(issue(7, assignee="dana"))
    assert record["number"] == "7"
    assert record["state"] == "open"
    assert record["assignee"] == "dana"
    assert record["labels"] == "bug, p1"
    assert record["label_count"] == 2
    assert record["is_pull_request"] is False
    assert record["_version"] == _epoch("2026-11-08T10:00:00Z") > 0


def test_the_schema_only_offers_operations_whose_effect_can_be_verified(connector):
    described = connector.describe(refresh=True)
    entity = described.entity("issues")
    assert {op.id for op in entity.operations} == {
        "close_issue", "close_as_not_planned", "reopen_issue", "assign_to_me", "unassign",
        "set_labels", "set_title"}
    for operation in entity.operations:
        # Every promised effect names a real field, set to a constant or to one of
        # the operation's own parameters, so a read-back can prove it. An
        # increment-shaped action (posting a comment) is deliberately absent.
        assert operation.effect
        params = {p.name for p in operation.params}
        for field_name, value in operation.effect.items():
            assert entity.field(field_name) is not None
            if isinstance(value, str) and value.startswith("{"):
                assert value.strip("{}") in params
        assert operation.requires_approval is True


def test_labels_are_set_as_a_whole_list_so_the_result_can_be_checked(connector):
    """However the list is typed, the promise is the form GitHub reports back."""
    params = connector.normalise_params("issues", "set_labels",
                                        {"labels": " triage,bug , Bug,,help wanted "})
    assert params == {"labels": "bug, help wanted, triage"}
    operation = connector.describe().entity("issues").operation("set_labels")
    assert operation.predict(params) == {"labels": "bug, help wanted, triage"}

    result = connector.apply("issues", "1", "set_labels", params, expected_version=None,
                             idempotency_key="k-labels", actor="dana")
    assert result.changed == {"labels": "bug, help wanted, triage"}
    assert connector._client.calls[-1][2] == {"labels": ["bug", "help wanted", "triage"]}


def test_clearing_every_label_is_a_promise_too(connector):
    params = connector.normalise_params("issues", "set_labels", {})
    operation = connector.describe().entity("issues").operation("set_labels")
    assert operation.predict(params) == {"labels": ""}
    result = connector.apply("issues", "1", "set_labels", params, expected_version=None,
                             idempotency_key="k-clear", actor="dana")
    assert result.record["labels"] == "" and result.record["label_count"] == 0


def test_closing_as_not_planned_says_so_on_github(connector):
    result = connector.apply("issues", "1", "close_as_not_planned", {}, expected_version=None,
                             idempotency_key="k-np", actor="dana")
    assert result.changed == {"state": "closed", "state_reason": "not_planned"}


def test_without_a_token_a_public_repository_is_read_and_nothing_is_offered(morph_home,
                                                                              monkeypatch):
    """No credential is not an error: it is a read-only connection that says so."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    built = GitHubConnector(ConnectorConfig(id="github", kind="github", label="GitHub",
                                            token_env="GITHUB_TOKEN",
                                            options={"repo": "acme/widgets"}))
    built._client = FakeGitHub([issue(1)])
    described = built.describe(refresh=True)
    assert described.entity("issues").operations == ()
    assert [r["number"] for r in built.fetch("issues").records] == ["1"]
    health = built.health()
    assert health["ok"] and health["writable"] is False
    assert "GITHUB_TOKEN" in health["hint"]
    assert not any(url.endswith("/user") for _m, url, _b in built._client.calls)


def test_the_effect_of_assigning_is_the_authenticated_account(connector):
    entity = connector.describe(refresh=True).entity("issues")
    assign = entity.operation("assign_to_me")
    assert assign.effect == {"assignee": "octocat"}
    assert assign.predict({}) == {"assignee": "octocat"}


def test_reads_filter_locally_as_well_as_remotely(connector):
    from morph.connectors.base import Predicate
    connector.describe(refresh=True)
    result = connector.fetch("issues", filters=[Predicate("state", "eq", "open")])
    assert {r["number"] for r in result.records} == {"1", "3"}
    narrowed = connector.fetch("issues", filters=[Predicate("assignee", "eq", "octocat")])
    assert [r["number"] for r in narrowed.records] == ["3"]


def test_a_write_applies_and_reports_what_changed(connector):
    connector.describe(refresh=True)
    before = connector.get("issues", "1")
    result = connector.apply("issues", "1", "close_issue", {},
                             expected_version=before["_version"],
                             idempotency_key="k1", actor="morph:tester")
    assert result.replayed is False
    assert result.changed == {"state": "closed"}
    assert connector.get("issues", "1")["state"] == "closed"


def test_optimistic_concurrency_uses_the_issues_updated_at(connector):
    connector.describe(refresh=True)
    with pytest.raises(StaleRecord) as caught:
        connector.apply("issues", "1", "close_issue", {}, expected_version=1,
                        idempotency_key="k2", actor="t")
    assert "updated_at moved" in caught.value.message


def test_a_declared_precondition_is_checked_before_the_write(connector):
    connector.describe(refresh=True)
    closed = connector.get("issues", "2")
    with pytest.raises(StaleRecord) as caught:
        connector.apply("issues", "2", "close_issue", {},
                        expected_version=closed["_version"], idempotency_key="k3", actor="t")
    assert "already closed" in caught.value.message
    assert connector._client.calls[-1][0] == "GET"   # nothing was PATCHed


def test_a_repeated_idempotency_key_replays_rather_than_reapplying(connector):
    connector.describe(refresh=True)
    before = connector.get("issues", "1")
    first = connector.apply("issues", "1", "close_issue", {},
                            expected_version=before["_version"], idempotency_key="same",
                            actor="t")
    patches = sum(1 for call in connector._client.calls if call[0] == "PATCH")
    second = connector.apply("issues", "1", "close_issue", {},
                             expected_version=before["_version"], idempotency_key="same",
                             actor="t")
    assert first.replayed is False and second.replayed is True
    assert sum(1 for call in connector._client.calls if call[0] == "PATCH") == patches


def test_a_missing_issue_is_reported_not_guessed(connector):
    connector.describe(refresh=True)
    assert connector.get("issues", "999") is None
    with pytest.raises(NotFound):
        connector.apply("issues", "999", "close_issue", {}, expected_version=None,
                        idempotency_key="k4", actor="t")


def test_a_read_only_connector_never_reaches_github(morph_home):
    config = ConnectorConfig(id="github", kind="github", label="GitHub", read_only=True,
                             options={"repo": "acme/widgets"})
    built = GitHubConnector(config)
    built._client = FakeGitHub([issue(1)])
    built._headers = lambda: {}
    with pytest.raises(PermissionDenied):
        built.apply("issues", "1", "close_issue", {}, expected_version=None,
                    idempotency_key="k", actor="t")
    assert not any(call[0] == "PATCH" for call in built._client.calls)


def test_the_primary_rate_limit_is_waited_out_not_hammered(connector):
    connector._client.fail_times = 1
    connector._client.fail_status = 403      # GitHub signals its limit with 403 + remaining 0
    described = connector.describe(refresh=True)
    assert described.entity("issues") is not None


def test_the_change_feed_reports_only_what_moved(connector):
    connector.describe(refresh=True)
    first = connector.changes(since=0)
    assert first["supported"] is True
    assert {c["record_id"] for c in first["changes"]} == {"1", "2", "3"}
    cursor = first["cursor"]
    assert connector.changes(since=cursor)["changes"] == []
    # Something moves on GitHub, outside MORPH.
    connector._client.issues["2"]["updated_at"] = "2026-11-10T08:00:00Z"
    later = connector.changes(since=cursor)
    assert [c["record_id"] for c in later["changes"]] == ["2"]


def test_a_broken_feed_degrades_instead_of_raising(connector):
    connector._client.fail_times = 99
    connector._client.fail_status = 500
    answer = connector.changes(since=0)
    assert answer["supported"] is False and answer["error"]
    assert answer["changes"] == []


def test_github_is_just_another_connector_in_the_graph(morph_home, monkeypatch):
    """The point of the adapter: nothing above it knows GitHub is special."""
    from morph import capability, engine, registry
    from morph.config import settings

    monkeypatch.setenv("MORPH_GITHUB_TOKEN", "test")
    monkeypatch.setenv("MORPH_CONFIG", json.dumps({
        "connectors": [{"id": "github", "kind": "github", "label": "GitHub",
                        "token_env": "MORPH_GITHUB_TOKEN",
                        "options": {"repo": "acme/widgets"}}],
        "default_role": "operator"}))
    settings.cache_clear()
    registry.reset()
    engine.invalidate()
    built = registry.get("github")
    built._client = FakeGitHub([issue(1), issue(2, state="closed")])

    graph = engine.graph(refresh=True)
    assert "github.issues" in graph.entities
    assert {ref for ref, _ in graph.writable_operations} == {
        f"github.issues.{name}" for name in (
            "close_issue", "close_as_not_planned", "reopen_issue", "assign_to_me",
            "unassign", "set_labels", "set_title")}

    from morph.actions import Principal
    planned = engine.plan_workspace(Principal.operator("ops@example.com"),
                                    "triage the open issues")
    assert planned["entities_used"] == ["github.issues"]
    assert any(block["kind"] in ("board", "table")
               for section in planned["workspace"]["schema"]["sections"]
               for block in section["blocks"])


def test_the_whole_loop_on_github_propose_approve_verify(morph_home, monkeypatch):
    """What the product is: an agent proposes, a person approves, the issue is re-read."""
    from morph import engine, registry
    from morph.actions import Principal
    from morph.config import settings
    from morph.errors import MorphError

    monkeypatch.setenv("MORPH_GITHUB_TOKEN", "test")
    monkeypatch.setenv("MORPH_CONFIG", json.dumps({
        "connectors": [{"id": "widgets", "kind": "github", "label": "acme/widgets",
                        "token_env": "MORPH_GITHUB_TOKEN",
                        "options": {"repo": "acme/widgets"}}],
        "default_role": "operator"}))
    settings.cache_clear()
    registry.reset()
    engine.invalidate()
    fake = FakeGitHub([issue(1)])
    registry.get("widgets")._client = fake
    engine.graph(refresh=True)

    agent = Principal.agent()
    proposal = engine.propose(agent, source="widgets", entity="issues", record_id="1",
                              operation="set_labels", params={"labels": "stale,  Bug"},
                              reason="no reply since spring")
    assert proposal["status"] == "pending" and "confirm_token" not in proposal
    assert not [c for c in fake.calls if c[0] == "PATCH"], "proposing must not write"
    with pytest.raises(MorphError):
        engine.approve(agent, proposal["id"], confirm_token="guess")

    operator = Principal.operator("dana@example.com")
    token = next(p for p in engine.proposals(operator)
                 if p["id"] == proposal["id"])["confirm_token"]
    applied = engine.approve(operator, proposal["id"], confirm_token=token)
    assert applied["status"] == "applied"
    assert applied["result"]["verification"]["verified"] is True
    assert fake.issues["1"]["labels"] == [{"name": "Bug"}, {"name": "stale"}]
