"""The connector contract and the configuration loader."""
from __future__ import annotations

import json

import pytest

from morph import registry
from morph.config import reload_settings, settings
from morph.connectors import KINDS, Connector, Predicate
from morph.errors import ConfigError, NotFound, StaleRecord


def test_every_registered_kind_implements_the_whole_contract():
    required = {"describe", "health", "fetch", "get", "apply"}
    for name, klass in KINDS.items():
        assert issubclass(klass, Connector), name
        assert not (required - set(dir(klass))), name
        assert klass.kind == name


def test_descriptors_round_trip_through_public(graph):
    source = graph.sources[0]
    payload = source.public()
    assert payload["id"] == "crm"
    assert {entity["name"] for entity in payload["entities"]} == {"deals", "people"}
    operations = payload["entities"][0]["operations"]
    assert all(operation["requires_approval"] is True for operation in operations)


def test_filters_are_applied_even_if_a_source_ignores_them(graph):
    connector = registry.get("crm")
    result = connector.fetch("deals", filters=[Predicate("stage", "eq", "won")])
    assert [record["id"] for record in result.records] == ["D-3"]
    result = connector.fetch("deals", filters=[Predicate("amount", "gte", 1000)])
    assert {record["id"] for record in result.records} == {"D-1", "D-2"}
    result = connector.fetch("deals", filters=[Predicate("name", "contains", "acme")])
    assert [record["id"] for record in result.records] == ["D-1"]
    result = connector.fetch("deals", filters=[Predicate("note", "exists", True)])
    assert [record["id"] for record in result.records] == ["D-3"]


def test_records_carry_a_version_and_writes_bump_it(graph):
    connector = registry.get("crm")
    before = connector.get("deals", "D-1")
    assert before["_version"] == 1
    connector.apply("deals", "D-1", "flag_deal", {"reason": "x"}, expected_version=1,
                    idempotency_key="k", actor="test")
    after = connector.get("deals", "D-1")
    assert after["_version"] == 2 and after["flagged"] is True


def test_optimistic_concurrency_is_enforced_by_the_connector(graph):
    connector = registry.get("crm")
    with pytest.raises(StaleRecord):
        connector.apply("deals", "D-1", "flag_deal", {"reason": "x"}, expected_version=99,
                        idempotency_key="k2", actor="test")


def test_a_repeated_idempotency_key_replays_rather_than_reapplying(graph):
    connector = registry.get("crm")
    first = connector.apply("deals", "D-2", "flag_deal", {"reason": "once"},
                            expected_version=1, idempotency_key="same", actor="test")
    second = connector.apply("deals", "D-2", "flag_deal", {"reason": "twice"},
                             expected_version=1, idempotency_key="same", actor="test")
    assert first.replayed is False and second.replayed is True
    assert connector.get("deals", "D-2")["_version"] == 2
    assert connector.get("deals", "D-2")["note"] == "once"


def test_a_declared_precondition_is_enforced_by_the_connector(graph):
    connector = registry.get("crm")
    with pytest.raises(StaleRecord) as caught:
        connector.apply("deals", "D-3", "flag_deal", {"reason": "x"}, expected_version=1,
                        idempotency_key="k3", actor="test")
    assert "already flagged" in str(caught.value)


def test_unknown_entities_and_operations_are_refused(graph):
    connector = registry.get("crm")
    with pytest.raises(Exception):
        connector.fetch("ghosts")
    with pytest.raises(Exception):
        connector.apply("deals", "D-1", "nope", {}, expected_version=1,
                        idempotency_key="k4", actor="test")
    with pytest.raises(NotFound):
        registry.get("missing-connector")


def test_health_never_raises(graph):
    assert registry.get("crm").health()["ok"] is True
    assert all("id" in probe for probe in registry.health())


# ── configuration ─────────────────────────────────────────────────────────────

def test_the_bundled_default_configuration_is_valid(morph_home, monkeypatch):
    monkeypatch.delenv("MORPH_CONFIG", raising=False)
    current = reload_settings()
    assert current.source == "bundled-default"
    assert {c.id for c in current.connectors} == {"storefront", "helpdesk", "warehouse"}
    assert current.allow_loopback is True
    assert current.roles["operator"].can_approve is True


@pytest.mark.parametrize("bad, expected", [
    ({"connectors": [{"id": "Bad Id", "kind": "http-rest"}]}, "lower_snake_case"),
    ({"connectors": [{"id": "a", "kind": "telepathy"}]}, "unsupported kind"),
    ({"connectors": [{"id": "a", "kind": "memory"}, {"id": "a", "kind": "memory"}]}, "duplicate"),
    ({"links": [{"name": "x", "source": "a.b", "target": "c.d.e"}]}, "<app>.<entity>.<field>"),
    ({"roles": {"r": {"max_risk": "apocalyptic"}}}, "max_risk"),
    ({"default_role": "nobody"}, "not defined"),
    ({"proposal_ttl_seconds": 5}, "between"),
    ({"connectors": "nope"}, "must be a list"),
])
def test_invalid_configuration_is_refused_with_a_reason(morph_home, monkeypatch, bad, expected):
    monkeypatch.setenv("MORPH_CONFIG", json.dumps(bad))
    with pytest.raises(ConfigError) as caught:
        reload_settings()
    assert expected in caught.value.message


def test_a_configuration_file_can_be_pointed_at(morph_home, tmp_path, monkeypatch):
    path = tmp_path / "wiring.json"
    path.write_text(json.dumps({"connectors": [], "default_role": "analyst"}), encoding="utf-8")
    monkeypatch.delenv("MORPH_CONFIG", raising=False)
    monkeypatch.setenv("MORPH_CONFIG_FILE", str(path))
    current = reload_settings()
    assert current.default_role == "analyst"
    assert current.source.endswith("wiring.json")


def test_unparseable_configuration_says_so(morph_home, monkeypatch):
    monkeypatch.setenv("MORPH_CONFIG", "{not json")
    with pytest.raises(ConfigError) as caught:
        reload_settings()
    assert "valid JSON" in caught.value.message
