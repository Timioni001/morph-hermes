"""The OpenAPI adapter: a service that has never heard of MORPH.

The document below is an ordinary typed REST API — the shape FastAPI, Spring,
NestJS and go-swagger all emit. Nothing in it mentions MORPH. Everything MORPH
knows about this service is derived from it.

Driven through a fake transport so these run anywhere, with no network.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from morph.config import ConnectorConfig
from morph.connectors.openapi import OpenApiConnector
from morph.errors import ConnectorError, NotFound, PermissionDenied, StaleRecord

SPEC: dict[str, Any] = {
    "openapi": "3.0.3",
    "info": {"title": "Fieldwork", "description": "Scheduling for field engineers."},
    "components": {
        "schemas": {
            "Job": {
                "type": "object",
                "required": ["id", "reference"],
                "properties": {
                    "id": {"type": "string", "format": "uuid"},
                    "reference": {"type": "string", "maxLength": 32},
                    "status": {"type": "string",
                               "enum": ["scheduled", "en_route", "on_site", "complete"]},
                    "priority": {"type": "integer", "minimum": 1, "maximum": 5},
                    "engineer_note": {"type": "string", "maxLength": 500},
                    "requires_parts": {"type": "boolean"},
                    "scheduled_for": {"type": "string", "format": "date-time"},
                    "contact_email": {"type": "string", "format": "email"},
                    "created_at": {"type": "string", "format": "date-time",
                                   "readOnly": True},
                    "site": {"type": "object", "properties": {"line1": {"type": "string"}}},
                    "version": {"type": "integer"},
                },
            },
            "JobPatch": {
                "type": "object",
                "properties": {
                    "status": {"type": "string",
                               "enum": ["scheduled", "en_route", "on_site", "complete"]},
                    "priority": {"type": "integer"},
                    "engineer_note": {"type": "string"},
                    "requires_parts": {"type": "boolean"},
                    "created_at": {"type": "string"},
                    "id": {"type": "string"},
                },
            },
            "JobList": {
                "type": "object",
                "properties": {"items": {"type": "array",
                                         "items": {"$ref": "#/components/schemas/Job"}}},
            },
        }
    },
    "paths": {
        "/health": {"get": {"responses": {"200": {"description": "ok"}}}},
        "/jobs": {"get": {"responses": {"200": {"description": "many", "content": {
            "application/json": {"schema": {"$ref": "#/components/schemas/JobList"}}}}}}},
        "/jobs/{job_id}": {
            "get": {"summary": "One job", "responses": {"200": {"description": "one", "content": {
                "application/json": {"schema": {"$ref": "#/components/schemas/Job"}}}}}},
            "patch": {"requestBody": {"content": {
                "application/json": {"schema": {"$ref": "#/components/schemas/JobPatch"}}}},
                "responses": {"200": {"description": "patched", "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/Job"}}}}}},
        },
        # A collection with no single-record route: not an entity.
        "/audit-events": {"get": {"responses": {"200": {"description": "many"}}}},
    },
}

JOB = {"id": "J-1", "reference": "WO-4471", "status": "scheduled", "priority": 3,
       "engineer_note": "", "requires_parts": False, "scheduled_for": "2026-02-01T09:00:00Z",
       "contact_email": "site@example.com", "created_at": "2026-01-02T00:00:00Z",
       "site": {"line1": "12 Dock Road"}, "version": 1}


class FakeResponse:
    def __init__(self, status: int, payload: Any = None, headers: dict | None = None):
        self.status_code = status
        self.content = b"" if payload is None else json.dumps(payload).encode()
        self.headers = headers or {}


class FakeService:
    def __init__(self, spec: dict | None = None):
        self.spec = spec if spec is not None else SPEC
        self.jobs = {JOB["id"]: dict(JOB)}
        self.calls: list[tuple[str, str, Any, dict]] = []
        self.patch_status = 200
        self.patch_returns_body = True

    def request(self, method, url, params=None, json=None, headers=None):  # noqa: A002
        self.calls.append((method, url, json, headers or {}))
        if url.endswith("/openapi.json"):
            return FakeResponse(200, self.spec)
        if url.endswith("/jobs"):
            return FakeResponse(200, {"items": list(self.jobs.values())})
        if "/jobs/" in url:
            key = url.rsplit("/", 1)[-1]
            record = self.jobs.get(key)
            if record is None:
                return FakeResponse(404, {"detail": "no such job"})
            if method.upper() == "PATCH":
                if self.patch_status != 200:
                    return FakeResponse(self.patch_status, {"detail": "conflict"})
                record.update(json or {})
                record["version"] = int(record.get("version", 1)) + 1
                return FakeResponse(200, record if self.patch_returns_body else None)
            return FakeResponse(200, record)
        return FakeResponse(404, {"detail": "not found"})


@pytest.fixture
def service(monkeypatch):
    fake = FakeService()
    monkeypatch.setattr(OpenApiConnector, "_get_client", lambda self: fake)
    monkeypatch.setattr(OpenApiConnector, "_checked_base", lambda self: "https://api.example.com")
    return fake


@pytest.fixture
def connector(service):
    return OpenApiConnector(ConnectorConfig(
        id="fieldwork", kind="openapi", label="Fieldwork",
        base_url="https://api.example.com", token_env=""))


# ──────────────────────────────── derivation ───────────────────────────────────

def test_entities_come_from_the_document_alone(connector):
    described = connector.describe()
    assert [e.name for e in described.entities] == ["jobs"]
    assert described.vendor == "Fieldwork"


def test_a_collection_with_no_record_route_is_not_an_entity(connector):
    """`/audit-events` can be listed but no single one can be read or verified."""
    assert "audit_events" not in [e.name for e in connector.describe().entities]


def test_field_types_come_from_type_and_format(connector):
    job = connector.describe().entity("jobs")
    kinds = {f.name: f.type for f in job.fields}
    assert kinds["id"] == "id"
    assert kinds["reference"] == "string"
    assert kinds["status"] == "enum"
    assert kinds["priority"] == "int"
    assert kinds["requires_parts"] == "bool"
    assert kinds["scheduled_for"] == "datetime"
    assert kinds["contact_email"] == "email"


def test_a_nested_object_is_not_a_field(connector):
    """MORPH's record model is flat; a nested value has no honest column."""
    assert "site" not in [f.name for f in connector.describe().entity("jobs").fields]


def test_only_what_the_patch_body_accepts_is_writable(connector):
    job = connector.describe().entity("jobs")
    writable = {f.name for f in job.fields if f.writable}
    assert writable == {"status", "priority", "engineer_note", "requires_parts"}


def test_a_read_only_field_is_never_writable_even_when_the_patch_body_lists_it(connector):
    """`created_at` is in JobPatch, but the schema marks it readOnly."""
    job = connector.describe().entity("jobs")
    assert job.field("created_at").writable is False


def test_an_identifier_is_never_writable_even_when_the_patch_body_lists_it(connector):
    assert connector.describe().entity("jobs").field("id").writable is False


def test_the_primary_key_and_title_are_inferred(connector):
    job = connector.describe().entity("jobs")
    assert job.primary_key == "id"
    assert job.title_field == "reference"


# ──────────────────────────────── operations ───────────────────────────────────

def test_an_enum_becomes_one_operation_per_value(connector):
    job = connector.describe().entity("jobs")
    ids = {op.id for op in job.operations}
    assert {"set_status_scheduled", "set_status_en_route", "set_status_on_site",
            "set_status_complete"} <= ids


def test_each_enum_operation_states_its_exact_effect_and_precondition(connector):
    op = connector.describe().entity("jobs").operation("set_status_complete")
    assert op.effect == {"status": "complete"}
    assert op.effect_fields == ("status",)
    assert op.precondition[0] == {"field": "status", "op": "neq", "value": "complete",
                                  "message": "status is already 'complete'"}
    assert op.requires_approval is True


def test_a_boolean_becomes_a_matched_pair(connector):
    job = connector.describe().entity("jobs")
    assert job.operation("set_requires_parts").effect == {"requires_parts": True}
    assert job.operation("clear_requires_parts").effect == {"requires_parts": False}


def test_an_open_valued_field_takes_the_value_as_a_parameter(connector):
    op = connector.describe().entity("jobs").operation("set_engineer_note")
    assert [p.name for p in op.params] == ["value"]
    assert op.effect == {"engineer_note": "{value}"}
    assert op.predict({"value": "waiting on access"}) == {"engineer_note": "waiting on access"}


def test_a_read_only_entity_offers_no_operations(monkeypatch, service):
    spec = json.loads(json.dumps(SPEC))
    del spec["paths"]["/jobs/{job_id}"]["patch"]
    service.spec = spec
    connector = OpenApiConnector(ConnectorConfig(
        id="fieldwork", kind="openapi", label="Fieldwork",
        base_url="https://api.example.com", token_env=""))
    job = connector.describe().entity("jobs")
    assert job.operations == ()
    assert job.writable is False


# ───────────────────────────────── reading ─────────────────────────────────────

def test_records_are_unwrapped_from_whatever_envelope_the_service_uses(connector):
    assert [r["id"] for r in connector.fetch("jobs").records] == ["J-1"]


def test_filters_are_evaluated_locally_because_the_document_promises_nothing(connector):
    from morph.connectors import Predicate
    assert connector.fetch("jobs", filters=[Predicate("status", "eq", "complete")]).records == []
    kept = connector.fetch("jobs", filters=[Predicate("status", "eq", "scheduled")])
    assert [r["id"] for r in kept.records] == ["J-1"]


def test_a_missing_record_reads_as_absent_not_as_an_error(connector):
    assert connector.get("jobs", "nope") is None


def test_no_change_feed_is_claimed(connector):
    assert connector.changes()["supported"] is False


# ───────────────────────────────── writing ─────────────────────────────────────

def _apply(connector, operation="set_status_en_route", params=None):
    return connector.apply("jobs", "J-1", operation, params or {},
                           expected_version=1, idempotency_key="k1", actor="ops@example.com")


def test_applying_sends_only_the_operation_s_effect(connector, service):
    result = _apply(connector)
    patch = [c for c in service.calls if c[0].upper() == "PATCH"][0]
    assert patch[2] == {"status": "en_route"}
    assert result.record["status"] == "en_route"
    assert result.changed["status"] == {"before": "scheduled", "after": "en_route"}


def test_the_actor_and_an_idempotency_key_are_sent(connector, service):
    _apply(connector)
    headers = [c for c in service.calls if c[0].upper() == "PATCH"][0][3]
    assert headers["X-Actor"] == "ops@example.com"
    assert headers["Idempotency-Key"] == "k1"


def test_a_version_field_becomes_an_if_match(connector, service):
    _apply(connector)
    headers = [c for c in service.calls if c[0].upper() == "PATCH"][0][3]
    assert headers["If-Match"] == "1"


def test_a_service_that_answers_204_is_read_back_rather_than_assumed(connector, service):
    service.patch_returns_body = False
    result = _apply(connector)
    assert result.record["status"] == "en_route"      # from the re-read, not from the response


def test_a_precondition_failure_from_the_service_is_a_stale_record(connector, service):
    service.patch_status = 412
    with pytest.raises(StaleRecord):
        _apply(connector)


def test_an_unknown_operation_is_refused_before_any_request(connector, service):
    with pytest.raises(ConnectorError):
        connector.apply("jobs", "J-1", "delete_everything", {},
                        expected_version=None, idempotency_key="k", actor="x")
    assert not [c for c in service.calls if c[0].upper() == "PATCH"]


def test_a_read_only_connector_refuses_before_any_request(service):
    connector = OpenApiConnector(ConnectorConfig(
        id="fieldwork", kind="openapi", label="Fieldwork",
        base_url="https://api.example.com", token_env="", read_only=True))
    with pytest.raises(PermissionDenied):
        _apply(connector)
    assert not [c for c in service.calls if c[0].upper() == "PATCH"]


def test_writing_to_a_record_that_vanished_is_not_found(connector, service):
    service.jobs.clear()
    with pytest.raises(NotFound):
        _apply(connector)


# ─────────────────────────────── bad documents ─────────────────────────────────

def test_a_non_openapi_3_document_is_refused(monkeypatch, service):
    service.spec = {"swagger": "2.0", "paths": {}}
    connector = OpenApiConnector(ConnectorConfig(
        id="x", kind="openapi", label="X", base_url="https://api.example.com", token_env=""))
    with pytest.raises(ConnectorError, match="version 3"):
        connector.describe()


def test_a_document_with_no_usable_collection_says_so(monkeypatch, service):
    service.spec = {"openapi": "3.0.0", "info": {}, "paths": {"/ping": {"get": {}}}}
    connector = OpenApiConnector(ConnectorConfig(
        id="x", kind="openapi", label="X", base_url="https://api.example.com", token_env=""))
    with pytest.raises(ConnectorError, match="collection"):
        connector.describe()
