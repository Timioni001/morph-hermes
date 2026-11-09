"""The action engine: propose, approve, apply, verify, audit.

The rules this module exists to enforce:

* **Only a human approves.** Every write is a two-step: a proposal (which the agent
  may create) and an approval (which it may not). Authority is carried on the
  :class:`Principal`, and :func:`approve` refuses an agent principal before it looks
  at anything else. There is no tool, hook or configuration key that gives the agent
  ``OPERATOR`` authority — ``morph.config`` forces ``can_approve`` off for the agent
  role even if a configuration file sets it.
* **Permission is checked twice.** At proposal time and again at approval time,
  because a role can change in between.
* **Writes are idempotent.** A proposal owns one idempotency key and passes it to
  the source application, so a retried approval cannot apply twice.
* **Concurrency is optimistic and honest.** A proposal records the record version
  it was built against; if the record moved, the proposal becomes ``stale`` and
  nothing is written.
* **Success is read back, not assumed.** After the write MORPH re-reads the record
  from the source application and compares it with what the operation promised. A
  mismatch is reported as ``unverified`` — never as success.
* **Nothing is atomic across applications.** A batch is a sequence of independent
  writes and its result says so, per item.
"""
from __future__ import annotations

import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Optional

from morph import registry
from morph.capability import CapabilityGraph
from morph.config import Role, settings
from morph.connectors import OperationDescriptor
from morph.connectors.http_rest import _local_match
from morph.connectors.base import Predicate
from morph.errors import (
    ApprovalRequired, Conflict, MorphError, NotFound, PermissionDenied, RateLimited,
    SchemaError, StaleRecord, VerificationFailed,
)
from morph import hostsurface, store
from morph.util import clean_text, match_pattern, now, risk_at_most

PROPOSAL_STATUSES = ("pending", "applying", "applied", "unverified", "failed", "rejected",
                     "stale", "expired")
ACTIVE_STATUSES = ("pending", "applying")


class Authority(str, Enum):
    """Who is acting. The distinction is structural, not advisory."""

    AGENT = "agent"
    OPERATOR = "operator"


@dataclass(frozen=True)
class Principal:
    id: str
    display: str
    role_id: str
    authority: Authority

    @property
    def role(self) -> Role:
        return settings().role(self.role_id)

    def public(self) -> dict[str, Any]:
        role = self.role
        return {"id": self.id, "display": self.display, "role": role.id, "role_label": role.label,
                "authority": self.authority.value, "can_approve": self.can_approve,
                "max_risk": role.max_risk}

    @property
    def can_approve(self) -> bool:
        # Two independent conditions, both required. An agent principal can never
        # satisfy the first, whatever a configuration file says about its role.
        return self.authority is Authority.OPERATOR and self.role.can_approve

    @classmethod
    def agent(cls, *, display: str = "Hermes agent") -> "Principal":
        return cls(id="agent", display=display, role_id=settings().agent_role,
                   authority=Authority.AGENT)

    @classmethod
    def operator(cls, identity: str = "", display: str = "") -> "Principal":
        current = settings()
        key = (identity or "").strip().lower()
        role_id = current.user_roles.get(key, current.default_role)
        return cls(id=key or "local-operator", display=clean_text(display or identity or "Operator", 60),
                   role_id=role_id, authority=Authority.OPERATOR)


# ─────────────────────────────── rate limiting ─────────────────────────────────

class _Bucket:
    """Small per-principal token bucket, so a loop cannot flood a source system."""

    def __init__(self, capacity: int, refill_per_second: float) -> None:
        self.capacity = capacity
        self.refill = refill_per_second
        self._state: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def take(self, key: str) -> bool:
        with self._lock:
            tokens, at = self._state.get(key, (float(self.capacity), time.monotonic()))
            current = time.monotonic()
            tokens = min(self.capacity, tokens + (current - at) * self.refill)
            if tokens < 1.0:
                self._state[key] = (tokens, current)
                return False
            self._state[key] = (tokens - 1.0, current)
            return True


_propose_limit = _Bucket(capacity=30, refill_per_second=0.5)
_approve_limit = _Bucket(capacity=20, refill_per_second=0.33)


def reset_rate_limits() -> None:
    _propose_limit._state.clear()  # noqa: SLF001 - module-private test seam
    _approve_limit._state.clear()  # noqa: SLF001


# ──────────────────────────────── permissions ──────────────────────────────────

def authorize(principal: Principal, op_ref: str, risk: str) -> None:
    """Raise :class:`PermissionDenied` unless *principal* may run *op_ref*.

    The connector's own read-only setting is checked first and no role overrides it:
    it is a property of the connection, not of the person.
    """
    source = op_ref.split(".", 1)[0]
    config = settings().connector(source)
    if config is not None and config.read_only:
        raise PermissionDenied(
            f"connector {source!r} is connected read-only; no action against it can be proposed")
    role = principal.role
    for pattern in role.deny:
        if match_pattern(pattern, op_ref):
            raise PermissionDenied(
                f"role {role.label!r} is denied {op_ref} (rule {pattern!r})")
    if not any(match_pattern(pattern, op_ref) for pattern in role.allow):
        raise PermissionDenied(
            f"role {role.label!r} does not allow {op_ref}; nothing was changed")
    if not risk_at_most(risk, role.max_risk):
        raise PermissionDenied(
            f"{op_ref} is a {risk}-risk action and role {role.label!r} is limited to "
            f"{role.max_risk}-risk actions")


def permitted_operations(principal: Principal, graph: CapabilityGraph) -> list[dict[str, Any]]:
    """Every operation in the graph with this principal's verdict on it — the UI
    greys out what cannot be done instead of failing after a click."""
    out = []
    for ref, operation in graph.writable_operations:
        entry: dict[str, Any] = {"ref": ref, "label": operation.label, "risk": operation.risk,
                                 "requires_approval": True, "allowed": True, "reason": ""}
        try:
            authorize(principal, ref, operation.risk)
        except PermissionDenied as exc:
            entry["allowed"], entry["reason"] = False, exc.message
        out.append(entry)
    return out


# ──────────────────────────── parameter validation ─────────────────────────────

def validate_params(operation: OperationDescriptor, raw: Any) -> dict[str, Any]:
    """Server-side validation of operation parameters.

    The dashboard validates too, for the operator's benefit; this is the check that
    counts, and it runs on every path including the agent's.
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise SchemaError("params must be an object")
    allowed = {p.name: p for p in operation.params}
    unknown = sorted(set(raw) - set(allowed))
    if unknown:
        raise SchemaError(f"{operation.id} has no parameter(s): {', '.join(unknown)}")
    clean: dict[str, Any] = {}
    for name, spec in allowed.items():
        if name not in raw or raw[name] is None or raw[name] == "":
            if spec.required:
                raise SchemaError(f"{operation.id} requires {name!r} ({spec.label})")
            continue
        value = raw[name]
        if spec.type == "int":
            if isinstance(value, bool) or not str(value).strip().lstrip("-").isdigit():
                raise SchemaError(f"{name!r} must be a whole number")
            value = int(value)
        elif spec.type in ("float", "money"):
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise SchemaError(f"{name!r} must be a number") from None
        elif spec.type == "bool":
            value = bool(value)
        else:
            if isinstance(value, (dict, list)):
                raise SchemaError(f"{name!r} must be text")
            limit = spec.max_length or 400
            value = clean_text(value, limit)
            if not value:
                raise SchemaError(f"{name!r} cannot be blank")
            if spec.values and value not in spec.values:
                raise SchemaError(f"{name!r} must be one of: {', '.join(spec.values)}")
        if spec.minimum is not None and isinstance(value, (int, float)) and value < spec.minimum:
            raise SchemaError(f"{name!r} must be at least {spec.minimum:g}")
        if spec.maximum is not None and isinstance(value, (int, float)) and value > spec.maximum:
            raise SchemaError(f"{name!r} must be at most {spec.maximum:g}")
        clean[name] = value
    return clean


# ──────────────────────────────── proposals ────────────────────────────────────

def _resolve_operation(graph: CapabilityGraph, source: str, entity: str,
                       operation_id: str) -> tuple[str, Any, OperationDescriptor]:
    ref = f"{source}.{entity}"
    described = graph.entity(ref)
    if described is None:
        raise NotFound(f"{ref} is not a connected entity")
    operation = described.operation(operation_id)
    if operation is None:
        available = ", ".join(o.id for o in described.operations) or "none"
        raise PermissionDenied(
            f"{ref} does not expose {operation_id!r}; it offers: {available}")
    return ref, described, operation


def _row(record: Any) -> dict[str, Any]:
    return {k: v for k, v in dict(record).items()}


def _proposal_public(row: Any, *, include_token: bool = False) -> dict[str, Any]:
    data = _row(row)
    out = {
        "id": data["id"], "workspace_id": data["workspace_id"],
        "source": data["source"], "entity": data["entity"],
        "entity_ref": f"{data['source']}.{data['entity']}",
        "record_id": data["record_id"], "operation": data["operation"],
        "operation_ref": f"{data['source']}.{data['entity']}.{data['operation']}",
        "params": store.loads(data["params_json"], {}),
        "expected_version": data["expected_version"],
        "before": store.loads(data["before_json"], {}),
        "predicted": store.loads(data["predicted_json"], {}),
        "risk": data["risk"], "status": data["status"],
        "requested_by": data["requested_by"], "requested_role": data["requested_role"],
        "authority": data["authority"], "reason": data.get("reason") or "",
        "created_at": round(data["created_at"], 3), "expires_at": round(data["expires_at"], 3),
        "decided_at": round(data["decided_at"], 3) if data.get("decided_at") else None,
        "decided_by": data.get("decided_by"),
        "result": store.loads(data.get("result_json"), None),
        "error": data.get("error") or "",
        "requires_approval": True,
    }
    if include_token:
        out["confirm_token"] = data["confirm_token"]
    else:
        out["confirm_token_issued"] = bool(data["confirm_token"])
    return out


def propose(principal: Principal, graph: CapabilityGraph, *, source: str, entity: str,
            record_id: str, operation: str, params: Any = None, workspace_id: str = "main",
            reason: str = "", idempotency_key: str = "") -> dict[str, Any]:
    """Prepare one write for a human to approve. Nothing is written here."""
    if not _propose_limit.take(principal.id):
        raise RateLimited("too many proposals in a short window; wait a moment and retry")
    ref, described, descriptor = _resolve_operation(graph, source, entity, operation)
    op_ref = f"{ref}.{operation}"
    authorize(principal, op_ref, descriptor.risk)
    clean_params = validate_params(descriptor, params)

    key = clean_text(idempotency_key, 100) or uuid.uuid4().hex
    existing = find_by_idempotency_key(
        key, include_token=principal.authority is Authority.OPERATOR)
    if existing is not None:
        # The same request again is the same proposal, not a second one.
        return existing

    connector = registry.get(source)
    clean_params = connector.normalise_params(entity, operation, clean_params)
    record = connector.get(entity, record_id)
    if record is None:
        raise NotFound(f"{ref} has no record {record_id!r}")
    for predicate in descriptor.precondition:
        check = Predicate(str(predicate.get("field")), str(predicate.get("op") or "eq"),
                          predicate.get("value"))
        if not _local_match(record, check):
            raise Conflict(clean_text(predicate.get("message")
                                      or f"{record_id} is not in a state where {operation} applies", 200))
    predicted = descriptor.predict(clean_params)
    before = {k: record.get(k) for k in predicted}
    proposal_id = uuid.uuid4().hex
    confirm_token = secrets.token_urlsafe(18)
    created = now()
    expires = created + settings().proposal_ttl_seconds

    with store.write() as connection:
        connection.execute(
            "INSERT INTO proposals (id,workspace_id,source,entity,record_id,operation,params_json,"
            "expected_version,before_json,predicted_json,risk,status,requested_by,requested_role,"
            "authority,idempotency_key,confirm_token,reason,created_at,expires_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,'pending',?,?,?,?,?,?,?,?)",
            (proposal_id, workspace_id, source, entity, record_id, operation,
             store.dumps(clean_params), connector.version_of(record), store.dumps(before),
             store.dumps(predicted), descriptor.risk, principal.id, principal.role_id,
             principal.authority.value, key, confirm_token, clean_text(reason, 200),
             created, expires))
        store.append_audit(connection, actor=principal.id, authority=principal.authority.value,
                           role=principal.role_id, kind="proposed", subject=op_ref,
                           payload={"proposal": proposal_id, "record": record_id,
                                    "params": clean_params, "predicted": predicted,
                                    "before": before, "risk": descriptor.risk})
        row = connection.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()

    hostsurface.emit("action_proposed", {
        "proposal": proposal_id, "operation": op_ref, "record": record_id,
        "risk": descriptor.risk, "requested_by": principal.id,
        "authority": principal.authority.value})
    if principal.authority is Authority.AGENT:
        # The agent cannot approve, so the only way this moves is if a person is
        # told. One line, in their session, naming what is waiting.
        hostsurface.notify(
            f"MORPH: {op_ref} on {record_id} is waiting for your approval "
            f"({descriptor.risk} risk). Approve or reject it in the MORPH dashboard tab.")

    # The confirmation token is the thing an approval must present, so it is handed
    # only to operator-authority callers (the authenticated dashboard). An agent
    # receives the proposal and is told a token exists, which is all it needs to ask
    # a human to look — and is not enough to approve anything, by any route.
    return _proposal_public(row, include_token=principal.authority is Authority.OPERATOR)


def find_by_idempotency_key(key: str, *, include_token: bool = False) -> Optional[dict[str, Any]]:
    with store.read() as connection:
        row = connection.execute("SELECT * FROM proposals WHERE idempotency_key=?", (key,)).fetchone()
    return _proposal_public(row, include_token=include_token) if row else None


def get_proposal(proposal_id: str, *, include_token: bool = False) -> dict[str, Any]:
    with store.read() as connection:
        row = connection.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
    if row is None:
        raise NotFound(f"no proposal {proposal_id!r}")
    return _proposal_public(row, include_token=include_token)


def list_proposals(*, statuses: Iterable[str] = ACTIVE_STATUSES, limit: int = 40,
                   workspace_id: str = "", include_tokens: bool = False) -> list[dict[str, Any]]:
    """Pending (by default) proposals.

    ``include_tokens`` is for operator-authority callers only: the dashboard needs
    each pending proposal's confirmation token to be able to approve it. Tool
    results never set it.
    """
    statuses = [s for s in statuses if s in PROPOSAL_STATUSES] or list(ACTIVE_STATUSES)
    marks = ",".join("?" for _ in statuses)
    query = f"SELECT * FROM proposals WHERE status IN ({marks})"
    args: list[Any] = list(statuses)
    if workspace_id:
        query += " AND workspace_id=?"
        args.append(workspace_id)
    query += " ORDER BY created_at DESC LIMIT ?"
    args.append(max(1, min(limit, 200)))
    with store.read() as connection:
        rows = connection.execute(query, args).fetchall()
    return [_proposal_public(r, include_token=include_tokens) for r in rows]


def expire_overdue() -> int:
    """Expire proposals nobody decided within the configured window."""
    with store.write() as connection:
        changed = connection.execute(
            "UPDATE proposals SET status='expired', decided_at=? WHERE status='pending'"
            " AND expires_at < ?", (now(), now())).rowcount
        if changed:
            store.append_audit(connection, actor="system", authority="system", role="system",
                               kind="expired", subject="proposals", payload={"count": changed})
    return int(changed or 0)


def reject(principal: Principal, proposal_id: str, reason: str = "") -> dict[str, Any]:
    with store.write() as connection:
        row = connection.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        if row is None:
            raise NotFound(f"no proposal {proposal_id!r}")
        if row["status"] not in ACTIVE_STATUSES:
            raise Conflict(f"proposal {proposal_id} is {row['status']}, not awaiting a decision")
        connection.execute(
            "UPDATE proposals SET status='rejected', decided_at=?, decided_by=?, error=? WHERE id=?",
            (now(), principal.id, clean_text(reason, 200), proposal_id))
        store.append_audit(connection, actor=principal.id, authority=principal.authority.value,
                           role=principal.role_id, kind="rejected",
                           subject=f"{row['source']}.{row['entity']}.{row['operation']}",
                           payload={"proposal": proposal_id, "reason": clean_text(reason, 200)})
        fresh = connection.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
    return _proposal_public(fresh)


# ──────────────────────────────── approval ─────────────────────────────────────

def approve(principal: Principal, graph: CapabilityGraph, proposal_id: str, *,
            confirm_token: str) -> dict[str, Any]:
    """Apply an approved proposal and verify it against the source application.

    Refuses outright for an agent principal, for a role that may not approve, and
    for a missing or wrong confirmation token.
    """
    if principal.authority is not Authority.OPERATOR:
        raise ApprovalRequired(
            "only a human operator acting through the MORPH dashboard can approve an action; "
            "the agent can propose one and wait")
    if not principal.role.can_approve:
        raise PermissionDenied(f"role {principal.role.label!r} cannot approve actions")
    if not _approve_limit.take(principal.id):
        raise RateLimited("too many approvals in a short window; wait a moment and retry")

    # Claim the proposal inside one serialised transaction so two concurrent
    # approvals cannot both proceed.
    with store.write() as connection:
        row = connection.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        if row is None:
            raise NotFound(f"no proposal {proposal_id!r}")
        proposal = _row(row)
        if proposal["status"] != "pending":
            raise Conflict(f"proposal {proposal_id} is {proposal['status']}, not pending approval")
        if proposal["expires_at"] < now():
            connection.execute("UPDATE proposals SET status='expired', decided_at=? WHERE id=?",
                               (now(), proposal_id))
            raise Conflict(f"proposal {proposal_id} expired; propose it again")
        if not confirm_token or not secrets.compare_digest(str(confirm_token),
                                                           str(proposal["confirm_token"])):
            store.append_audit(connection, actor=principal.id,
                               authority=principal.authority.value, role=principal.role_id,
                               kind="approval_refused", subject=proposal_id,
                               payload={"reason": "confirmation token mismatch"})
            raise PermissionDenied("the approval confirmation token does not match this proposal")
        connection.execute("UPDATE proposals SET status='applying' WHERE id=?", (proposal_id,))

    op_ref = f"{proposal['source']}.{proposal['entity']}.{proposal['operation']}"
    try:
        _, described, descriptor = _resolve_operation(graph, proposal["source"],
                                                      proposal["entity"], proposal["operation"])
        authorize(principal, op_ref, descriptor.risk)  # re-checked: roles can change
        params = store.loads(proposal["params_json"], {})
        connector = registry.get(proposal["source"])

        current = connector.get(proposal["entity"], proposal["record_id"])
        if current is None:
            raise NotFound(f"{proposal['record_id']} no longer exists in {proposal['source']!r}")
        live_version = connector.version_of(current)
        if (proposal["expected_version"] is not None and live_version is not None
                and live_version != proposal["expected_version"]):
            raise StaleRecord(
                f"{proposal['record_id']} moved from version {proposal['expected_version']} to "
                f"{live_version} since the proposal was made; review it and propose again")

        applied = connector.apply(proposal["entity"], proposal["record_id"], proposal["operation"],
                                  params, expected_version=proposal["expected_version"],
                                  idempotency_key=proposal["idempotency_key"],
                                  actor=f"morph:{principal.id}")
        verification = verify_record(connector, proposal["entity"], proposal["record_id"],
                                     store.loads(proposal["predicted_json"], {}))
    except MorphError as exc:
        _record_failure(principal, proposal, op_ref, exc)
        raise
    except Exception as exc:  # noqa: BLE001 - unexpected adapter failure is still a failed action
        wrapped = MorphError(f"{type(exc).__name__}: {exc}")
        _record_failure(principal, proposal, op_ref, wrapped)
        raise wrapped from exc

    status = "applied" if verification["verified"] else "unverified"
    result = {"changed": applied.changed, "replayed": applied.replayed,
              "verification": verification, "record": verification.get("record")}
    with store.write() as connection:
        connection.execute(
            "UPDATE proposals SET status=?, decided_at=?, decided_by=?, result_json=?, error=?"
            " WHERE id=?",
            (status, now(), principal.id, store.dumps(result),
             "" if verification["verified"] else "read-back did not match the promised effect",
             proposal_id))
        store.append_audit(connection, actor=principal.id, authority=principal.authority.value,
                           role=principal.role_id,
                           kind="applied" if verification["verified"] else "applied_unverified",
                           subject=op_ref,
                           payload={"proposal": proposal_id, "record": proposal["record_id"],
                                    "params": params, "changed": applied.changed,
                                    "replayed": applied.replayed,
                                    "verified": verification["verified"],
                                    "mismatches": verification.get("mismatches", [])})
    hostsurface.emit("action_applied", {
        "proposal": proposal_id, "operation": op_ref, "record": proposal["record_id"],
        "status": status, "verified": verification["verified"],
        "approved_by": principal.id, "changed": applied.changed})

    out = get_proposal(proposal_id)
    if not verification["verified"]:
        raise VerificationFailed(
            "the source application accepted the write but the read-back did not match what the "
            "operation promised; the action is recorded as unverified",
            detail={"proposal": out, "verification": verification})
    return out


def _record_failure(principal: Principal, proposal: dict[str, Any], op_ref: str,
                    exc: MorphError) -> None:
    status = "stale" if isinstance(exc, StaleRecord) else "failed"
    with store.write() as connection:
        connection.execute(
            "UPDATE proposals SET status=?, decided_at=?, decided_by=?, error=? WHERE id=?",
            (status, now(), principal.id, clean_text(exc.message, 200), proposal["id"]))
        store.append_audit(connection, actor=principal.id, authority=principal.authority.value,
                           role=principal.role_id, kind=f"approval_{status}", subject=op_ref,
                           payload={"proposal": proposal["id"], "error": clean_text(exc.message, 200),
                                    "code": exc.code})


def verify_record(connector: Any, entity: str, record_id: str,
                  predicted: dict[str, Any]) -> dict[str, Any]:
    """Re-read the record from the source and compare it with the promised effect."""
    try:
        record = connector.get(entity, record_id)
    except MorphError as exc:
        return {"verified": False, "reason": f"could not re-read the record: {exc.message}",
                "mismatches": [], "record": None, "checked_at": now()}
    if record is None:
        return {"verified": False, "reason": "the record no longer exists", "mismatches": [],
                "record": None, "checked_at": now()}
    mismatches = []
    for field_name, expected in (predicted or {}).items():
        actual = record.get(field_name)
        if _loose_equal(actual, expected):
            continue
        mismatches.append({"field": field_name, "expected": expected, "actual": actual})
    return {"verified": not mismatches, "mismatches": mismatches,
            "checked": sorted(predicted or {}), "record": record,
            "version": record.get("_version"), "checked_at": now(),
            "reason": "" if not mismatches else "field(s) do not match the promised effect"}


def _loose_equal(actual: Any, expected: Any) -> bool:
    if actual == expected:
        return True
    if isinstance(expected, bool) or isinstance(actual, bool):
        return bool(actual) == bool(expected)
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return abs(float(actual) - float(expected)) < 1e-9
    if isinstance(expected, str) and actual is not None:
        return clean_text(actual, 400) == clean_text(expected, 400)
    return False


def verify_proposal(proposal_id: str) -> dict[str, Any]:
    """Read-only verification of an applied proposal, straight from the source."""
    proposal = get_proposal(proposal_id)
    if proposal["status"] not in ("applied", "unverified", "failed"):
        return {"proposal": proposal_id, "status": proposal["status"], "verified": False,
                "reason": f"nothing was applied: the proposal is {proposal['status']}"}
    connector = registry.get(proposal["source"])
    report = verify_record(connector, proposal["entity"], proposal["record_id"],
                           proposal["predicted"])
    return {"proposal": proposal_id, "status": proposal["status"],
            "operation_ref": proposal["operation_ref"], "record_id": proposal["record_id"],
            **report}


def approve_many(principal: Principal, graph: CapabilityGraph,
                 items: Iterable[dict[str, str]]) -> dict[str, Any]:
    """Approve several proposals in sequence.

    Each write is independent: there is no cross-application transaction and this
    result never pretends otherwise.  ``applied`` and ``failed`` together describe
    exactly what happened, in order.
    """
    applied: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for item in list(items)[:20]:
        proposal_id = str(item.get("id") or "")
        try:
            applied.append(approve(principal, graph, proposal_id,
                                   confirm_token=str(item.get("confirm_token") or "")))
        except MorphError as exc:
            failed.append({"id": proposal_id, "code": exc.code, "error": exc.message})
    return {"applied": applied, "failed": failed,
            "atomic": False,
            "note": "Each action was applied on its own. MORPH cannot offer a transaction across "
                    "independent applications, so a partial result is a real outcome, not an error."}
