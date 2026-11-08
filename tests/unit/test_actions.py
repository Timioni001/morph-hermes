"""The action engine's rules: permission, validation, idempotency, concurrency,
read-back verification and the audit chain."""
from __future__ import annotations

import pytest

from morph import actions, engine, registry, store
from morph.actions import Authority, Principal
from morph.errors import (
    ApprovalRequired, Conflict, NotFound, PermissionDenied, SchemaError, StaleRecord,
    VerificationFailed,
)


@pytest.fixture
def operator(memory_config):
    return Principal.operator("ops@example.com", "Ops")


@pytest.fixture
def agent(memory_config):
    return Principal.agent()


def test_the_agent_role_can_never_approve(memory_config, monkeypatch):
    """Even a configuration that tries to grant it is overridden."""
    import json
    config = dict(memory_config)
    config["roles"] = dict(config["roles"])
    config["roles"]["agent"] = {"allow": ["*"], "can_approve": True, "max_risk": "high"}
    monkeypatch.setenv("MORPH_CONFIG", json.dumps(config))
    from morph.config import settings
    settings.cache_clear()
    assert settings().roles["agent"].can_approve is False
    assert Principal.agent().can_approve is False


def test_propose_then_approve_applies_and_verifies(operator, graph):
    proposal = engine.propose(operator, source="crm", entity="deals", record_id="D-1",
                              operation="flag_deal", params={"reason": "stalled"})
    assert proposal["status"] == "pending"
    assert proposal["predicted"] == {"flagged": True, "note": "stalled"}
    assert proposal["before"] == {"flagged": False, "note": None}
    assert registry.get("crm").get("deals", "D-1")["flagged"] is False  # nothing written yet

    applied = engine.approve(operator, proposal["id"], confirm_token=proposal["confirm_token"])
    assert applied["status"] == "applied"
    assert applied["result"]["verification"]["verified"] is True
    assert registry.get("crm").get("deals", "D-1")["flagged"] is True


def test_an_agent_principal_is_refused_at_the_approval_boundary(agent, operator, graph):
    proposal = engine.propose(agent, source="crm", entity="deals", record_id="D-1",
                              operation="flag_deal", params={"reason": "x"})
    assert "confirm_token" not in proposal
    assert proposal["confirm_token_issued"] is True
    token = actions.get_proposal(proposal["id"], include_token=True)["confirm_token"]
    with pytest.raises(ApprovalRequired):
        engine.approve(agent, proposal["id"], confirm_token=token)
    assert registry.get("crm").get("deals", "D-1")["flagged"] is False


def test_the_confirmation_token_must_match(operator, graph):
    proposal = engine.propose(operator, source="crm", entity="deals", record_id="D-1",
                              operation="flag_deal", params={"reason": "x"})
    with pytest.raises(PermissionDenied):
        engine.approve(operator, proposal["id"], confirm_token="not-the-token")
    assert actions.get_proposal(proposal["id"])["status"] == "pending"


def test_risk_ceiling_blocks_a_high_risk_operation(operator, graph):
    with pytest.raises(PermissionDenied) as caught:
        engine.propose(operator, source="crm", entity="deals", record_id="D-1",
                       operation="close_deal")
    assert "high-risk" in caught.value.message


def test_an_unknown_operation_is_refused_before_anything_is_read(operator, graph):
    with pytest.raises(PermissionDenied) as caught:
        engine.propose(operator, source="crm", entity="deals", record_id="D-1",
                       operation="rm_rf")
    assert "does not expose" in caught.value.message


def test_parameters_are_validated_server_side(operator, graph):
    with pytest.raises(SchemaError):
        engine.propose(operator, source="crm", entity="deals", record_id="D-1",
                       operation="flag_deal")  # missing required reason
    with pytest.raises(SchemaError):
        engine.propose(operator, source="crm", entity="deals", record_id="D-1",
                       operation="flag_deal", params={"reason": "ok", "extra": 1})
    with pytest.raises(SchemaError):
        engine.propose(operator, source="crm", entity="deals", record_id="D-1",
                       operation="flag_deal", params={"reason": {"nested": "object"}})


def test_a_precondition_the_source_declares_is_pre_checked(operator, graph):
    with pytest.raises(Conflict) as caught:
        engine.propose(operator, source="crm", entity="deals", record_id="D-3",
                       operation="flag_deal", params={"reason": "again"})
    assert "already flagged" in caught.value.message


def test_a_missing_record_is_reported_not_guessed(operator, graph):
    with pytest.raises(NotFound):
        engine.propose(operator, source="crm", entity="deals", record_id="D-999",
                       operation="flag_deal", params={"reason": "x"})


def test_an_idempotency_key_returns_the_same_proposal(operator, graph):
    first = engine.propose(operator, source="crm", entity="deals", record_id="D-1",
                           operation="flag_deal", params={"reason": "x"},
                           idempotency_key="abc-123")
    second = engine.propose(operator, source="crm", entity="deals", record_id="D-2",
                            operation="flag_deal", params={"reason": "different"},
                            idempotency_key="abc-123")
    assert first["id"] == second["id"]
    assert second["record_id"] == "D-1"
    assert len(actions.list_proposals()) == 1


def test_a_stale_record_voids_the_proposal(operator, graph):
    first = engine.propose(operator, source="crm", entity="deals", record_id="D-1",
                           operation="flag_deal", params={"reason": "one"},
                           idempotency_key="k1")
    second = engine.propose(operator, source="crm", entity="deals", record_id="D-1",
                            operation="flag_deal", params={"reason": "two"},
                            idempotency_key="k2")
    engine.approve(operator, second["id"], confirm_token=second["confirm_token"])
    with pytest.raises(StaleRecord):
        engine.approve(operator, first["id"], confirm_token=first["confirm_token"])
    assert actions.get_proposal(first["id"])["status"] == "stale"
    # The second write stands; the first never happened.
    assert registry.get("crm").get("deals", "D-1")["note"] == "two"


def test_approving_twice_is_refused(operator, graph):
    proposal = engine.propose(operator, source="crm", entity="deals", record_id="D-1",
                              operation="flag_deal", params={"reason": "x"})
    engine.approve(operator, proposal["id"], confirm_token=proposal["confirm_token"])
    with pytest.raises(Conflict):
        engine.approve(operator, proposal["id"], confirm_token=proposal["confirm_token"])


def test_rejecting_changes_nothing(operator, graph):
    proposal = engine.propose(operator, source="crm", entity="deals", record_id="D-1",
                              operation="flag_deal", params={"reason": "x"})
    rejected = engine.reject(operator, proposal["id"], "not now")
    assert rejected["status"] == "rejected"
    assert registry.get("crm").get("deals", "D-1")["flagged"] is False
    with pytest.raises(Conflict):
        engine.approve(operator, proposal["id"], confirm_token=proposal["confirm_token"])


def test_verification_failure_is_reported_as_unverified(operator, graph, monkeypatch):
    """If the source accepts the write but the read-back disagrees, MORPH must not
    call it a success."""
    proposal = engine.propose(operator, source="crm", entity="deals", record_id="D-1",
                              operation="flag_deal", params={"reason": "x"})
    connector = registry.get("crm")
    real_get = connector.get

    def lying_get(entity, record_id):
        record = real_get(entity, record_id)
        if record is not None and record_id == "D-1" and record.get("flagged"):
            record = dict(record)
            record["flagged"] = False  # the source "forgot" the write
        return record

    monkeypatch.setattr(connector, "get", lying_get)
    with pytest.raises(VerificationFailed):
        engine.approve(operator, proposal["id"], confirm_token=proposal["confirm_token"])
    assert actions.get_proposal(proposal["id"])["status"] == "unverified"


def test_the_audit_log_is_hash_chained_and_detects_tampering(operator, graph):
    proposal = engine.propose(operator, source="crm", entity="deals", record_id="D-1",
                              operation="flag_deal", params={"reason": "x"})
    engine.approve(operator, proposal["id"], confirm_token=proposal["confirm_token"])
    assert store.verify_audit_chain()["ok"] is True
    kinds = [entry["kind"] for entry in store.audit_tail(10)]
    assert "applied" in kinds and "proposed" in kinds

    with store.write() as connection:
        connection.execute("UPDATE audit SET subject='tampered' WHERE seq=(SELECT MIN(seq) FROM audit)")
    report = store.verify_audit_chain()
    assert report["ok"] is False
    assert report["broken_at"] is not None


def test_a_batch_reports_each_item_and_claims_no_atomicity(operator, graph):
    good = engine.propose(operator, source="crm", entity="deals", record_id="D-1",
                          operation="flag_deal", params={"reason": "x"}, idempotency_key="g")
    other = engine.propose(operator, source="crm", entity="deals", record_id="D-2",
                           operation="flag_deal", params={"reason": "y"}, idempotency_key="o")
    result = engine.approve_many(operator, [
        {"id": good["id"], "confirm_token": good["confirm_token"]},
        {"id": other["id"], "confirm_token": "wrong-token"},
    ])
    assert result["atomic"] is False
    assert len(result["applied"]) == 1
    assert result["failed"][0]["code"] == "permission_denied"
    assert registry.get("crm").get("deals", "D-1")["flagged"] is True
    assert registry.get("crm").get("deals", "D-2")["flagged"] is False


def test_permission_verdicts_are_published_for_the_ui(operator, graph):
    verdicts = {entry["ref"]: entry for entry in actions.permitted_operations(operator, graph)}
    assert verdicts["crm.deals.flag_deal"]["allowed"] is True
    assert verdicts["crm.deals.close_deal"]["allowed"] is False
    assert "high-risk" in verdicts["crm.deals.close_deal"]["reason"]


def test_a_denied_role_cannot_propose_at_all(memory_config, graph, monkeypatch):
    import json
    config = dict(memory_config)
    config["default_role"] = "analyst"
    monkeypatch.setenv("MORPH_CONFIG", json.dumps(config))
    from morph.config import settings
    settings.cache_clear()
    analyst = Principal.operator("read@example.com")
    assert analyst.role.id == "analyst"
    with pytest.raises(PermissionDenied):
        engine.propose(analyst, source="crm", entity="deals", record_id="D-1",
                       operation="flag_deal", params={"reason": "x"})


def test_expired_proposals_cannot_be_approved(operator, graph):
    proposal = engine.propose(operator, source="crm", entity="deals", record_id="D-1",
                              operation="flag_deal", params={"reason": "x"})
    with store.write() as connection:
        connection.execute("UPDATE proposals SET expires_at=1 WHERE id=?", (proposal["id"],))
    with pytest.raises(Conflict):
        engine.approve(operator, proposal["id"], confirm_token=proposal["confirm_token"])
    assert actions.expire_overdue() >= 0
