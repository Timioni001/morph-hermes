"""The MORPH engine façade.

Everything that drives MORPH — the Hermes tools, the dashboard API, the CLI and the
demo script — goes through this module, so there is exactly one implementation of
each operation and one place where authority is decided.

The pipeline, in the order it runs:

    goal → discovery → capability graph → planning → schema validation
         → resolution → proposal → human approval → read-back verification
"""
from __future__ import annotations

import threading
import time
from typing import Any, Optional

from morph import (
    actions, capability, planner, registry, resolve as resolver, store, sync, workspace,
)
from morph.actions import Authority, Principal
from morph.capability import CapabilityGraph
from morph.config import reload_settings, settings
from morph.errors import MorphError, NotFound, SchemaError
from morph.uischema import UiSchema, validate
from morph.util import clean_text, now

GRAPH_TTL_SECONDS = 20.0
BOOTSTRAP_GOAL = "Give me an overview of everything MORPH is connected to"

_graph_lock = threading.RLock()
_graph_cache: Optional[tuple[float, CapabilityGraph]] = None


# ───────────────────────────────── discovery ───────────────────────────────────

def graph(*, refresh: bool = False) -> CapabilityGraph:
    """The current capability graph, briefly cached so one page load reads each
    application's schema once."""
    global _graph_cache
    with _graph_lock:
        if _graph_cache and not refresh and (time.monotonic() - _graph_cache[0]) < GRAPH_TTL_SECONDS:
            return _graph_cache[1]
    descriptors, failures = registry.describe_all(refresh=refresh)
    built = capability.build(descriptors, settings().links, unavailable=failures)
    with _graph_lock:
        _graph_cache = (time.monotonic(), built)
    return built


def invalidate() -> None:
    global _graph_cache
    with _graph_lock:
        _graph_cache = None


def reload() -> dict[str, Any]:
    """Re-read configuration and rebuild every connector."""
    reload_settings()
    registry.reset()
    invalidate()
    current = settings()
    return {"config_source": current.source,
            "connectors": [c.id for c in current.connectors],
            "links": [l.name for l in current.links]}


def discover(principal: Principal, *, refresh: bool = False) -> dict[str, Any]:
    built = graph(refresh=refresh)
    current = settings()
    return {
        "config_source": current.source,
        "graph": built.public(),
        "summary": built.summary(),
        "health": registry.health(),
        "principal": principal.public(),
        "permissions": actions.permitted_operations(principal, built),
        "planner": {"enabled": current.planner_enabled,
                    "deterministic_fallback": True},
    }


# ───────────────────────────────── planning ────────────────────────────────────

def plan_workspace(principal: Principal, goal: str, *, llm: Any = None,
                   workspace_id: str = workspace.DEFAULT_WORKSPACE,
                   preserve_context: bool = True, refresh: bool = False) -> dict[str, Any]:
    """Compose or transform the workspace for *goal*."""
    goal = clean_text(goal, 400)
    if not 3 <= len(goal) <= 400:
        raise SchemaError("a goal must be between 3 and 400 characters")
    built = graph(refresh=refresh)
    if not built.entities:
        raise MorphError(
            "no connected application is reachable, so there is nothing to compose from. "
            "Start the demo applications with `python -m apps.serve`, or point MORPH at your own "
            "services (docs/CONNECTORS.md).",
            detail=built.public().get("unavailable"))

    previous = workspace.current(workspace_id)
    carried: list[str] = []
    dropped: list[str] = []
    context_for_planner: dict[str, Any] = {}
    if previous and preserve_context and previous.get("context"):
        context_for_planner = {"previous_goal": previous["goal"],
                               "previous_selection": (previous["context"] or {}).get("selection", {})}

    current = settings()
    # Interpretation is permission-aware: it can then say "that operation exists but
    # your role may not run it" instead of "nothing can do that".
    denied = {entry["ref"]: entry["reason"] for entry in actions.permitted_operations(principal, built)
              if not entry["allowed"]}
    reading = planner.interpret(goal, built, denied_operations=denied)
    result = planner.plan(goal, built, llm=llm if current.planner_enabled else None,
                          context=context_for_planner or None,
                          max_tokens=current.planner_max_tokens, intent=reading)
    if previous and preserve_context:
        context, carried, dropped = workspace.carry_context(previous.get("context") or {},
                                                            result.schema, built)
    else:
        context = {}

    saved = workspace.save(goal, result, workspace_id=workspace_id, context=context,
                           actor=principal.id, authority=principal.authority.value,
                           role=principal.role_id)
    return {
        "workspace": saved,
        "plan": {"mode": result.mode, "notes": result.notes, "model": result.model,
                 "attempts": result.attempts,
                 "intent": result.intent.public() if result.intent else None},
        "transformed_from": ({"goal": previous["goal"], "revision": previous["revision"]}
                             if previous else None),
        "context_carried": carried,
        "context_dropped": dropped,
        "entities_used": result.schema.entities_used,
        "operations_offered": result.schema.operations_used,
    }


def bootstrap(principal: Optional[Principal] = None,
              workspace_id: str = workspace.DEFAULT_WORKSPACE) -> Optional[dict[str, Any]]:
    """Compose a first workspace if none exists and at least one application answers."""
    if workspace.current(workspace_id, include_schema=False) is not None:
        return None
    built = graph()
    if not built.entities:
        return None
    return plan_workspace(principal or Principal.operator(display="bootstrap"), BOOTSTRAP_GOAL,
                          llm=None, workspace_id=workspace_id, preserve_context=False)


# ─────────────────────────────── workspace state ───────────────────────────────

def _schema_for(saved: dict[str, Any], built: CapabilityGraph) -> tuple[Optional[UiSchema], str]:
    try:
        return validate(saved["schema"], built), ""
    except SchemaError as exc:
        detail = exc.detail if isinstance(exc.detail, list) else [exc.message]
        return None, clean_text("; ".join(str(d) for d in detail[:3]), 300)


def state(principal: Principal, *, workspace_id: str = workspace.DEFAULT_WORKSPACE,
          refresh: bool = False, detect_changes: bool = True) -> dict[str, Any]:
    """Everything needed to render MORPH: schema, live data, approvals, audit, health."""
    actions.expire_overdue()
    built = graph(refresh=refresh)
    saved = workspace.current(workspace_id)
    if saved is None:
        created = bootstrap(principal, workspace_id)
        saved = workspace.current(workspace_id)
        if saved is None:
            return {
                "workspace": None,
                "reason": "no workspace yet and no connected application is reachable",
                "graph": built.public(), "health": registry.health(),
                "principal": principal.public(),
                "proposals": [], "audit": store.audit_tail(10),
                "config_source": settings().source,
            }
        if created:
            saved["plan"] = created.get("plan")

    schema, schema_error = _schema_for(saved, built)
    data: dict[str, Any] = {}
    if schema is not None:
        previous = store.load_signatures(workspace_id) if detect_changes else {}
        resolution = resolver.resolve(schema, built, context=saved.get("context"),
                                      previous_signatures=previous)
        store.save_signatures(workspace_id, resolution.signatures)
        data = resolution.public()
        if not sync.cursors(workspace_id):
            sync.poll(workspace_id)  # seed cursors; the first look is history, not news

    return {
        "workspace": {**saved, "schema_error": schema_error},
        "data": data,
        "graph": built.public(),
        "summary": built.summary(),
        "health": registry.health(),
        "principal": principal.public(),
        "permissions": actions.permitted_operations(principal, built),
        # Only an operator principal is handed confirmation tokens; this is what
        # lets the dashboard approve, and what keeps the agent unable to.
        "proposals": actions.list_proposals(
            workspace_id=workspace_id,
            include_tokens=principal.authority is Authority.OPERATOR),
        "recent_decisions": actions.list_proposals(
            statuses=("applied", "unverified", "failed", "rejected", "stale", "expired"), limit=12,
            workspace_id=workspace_id),
        "revisions": workspace.revisions(workspace_id),
        "audit": store.audit_tail(25),
        "audit_chain": store.verify_audit_chain(),
        "config_source": settings().source,
        "served_at": round(now(), 3),
    }


def refresh_data(principal: Principal, *, workspace_id: str = workspace.DEFAULT_WORKSPACE
                 ) -> dict[str, Any]:
    """Re-read every source behind the workspace and report what moved."""
    built = graph(refresh=True)
    saved = workspace.require(workspace_id)
    schema, schema_error = _schema_for(saved, built)
    if schema is None:
        raise SchemaError("the saved workspace no longer matches the connected applications",
                          detail=schema_error)
    # Ask each application's own feed first: one small call each, and it sees changes
    # this workspace never read — including one a person made in the source system.
    feed = sync.poll(workspace_id)
    previous = store.load_signatures(workspace_id)
    resolution = resolver.resolve(schema, built, context=saved.get("context"),
                                  previous_signatures=previous)
    store.save_signatures(workspace_id, resolution.signatures)
    touched = set(feed["entities_touched"]) | {c["entity"] for c in resolution.changes}
    affected = [block.id for block in schema.blocks if block.entity in touched]
    return {"changes": resolution.changes, "source_feed": feed,
            "affected_blocks": affected,
            "degraded": resolution.degraded,
            "failures": resolution.failures, "fetched_at": round(resolution.fetched_at, 3),
            "data": resolution.public(), "health": registry.health()}


def set_context(principal: Principal, patch: Any, *,
                workspace_id: str = workspace.DEFAULT_WORKSPACE) -> dict[str, Any]:
    built = graph()
    saved = workspace.require(workspace_id)
    schema, _ = _schema_for(saved, built)
    return workspace.set_context(workspace_id, patch, built, schema)


def restore_revision(principal: Principal, revision: int, *,
                     workspace_id: str = workspace.DEFAULT_WORKSPACE) -> dict[str, Any]:
    return workspace.restore(workspace_id, revision, actor=principal.id,
                             authority=principal.authority.value, role=principal.role_id)


# ───────────────────────────────── actions ─────────────────────────────────────

def propose(principal: Principal, *, source: str, entity: str, record_id: str, operation: str,
            params: Any = None, workspace_id: str = workspace.DEFAULT_WORKSPACE,
            reason: str = "", idempotency_key: str = "") -> dict[str, Any]:
    return actions.propose(principal, graph(), source=source, entity=entity, record_id=record_id,
                           operation=operation, params=params, workspace_id=workspace_id,
                           reason=reason, idempotency_key=idempotency_key)


def approve(principal: Principal, proposal_id: str, *, confirm_token: str) -> dict[str, Any]:
    return actions.approve(principal, graph(), proposal_id, confirm_token=confirm_token)


def approve_many(principal: Principal, items: Any) -> dict[str, Any]:
    return actions.approve_many(principal, graph(), items or [])


def reject(principal: Principal, proposal_id: str, reason: str = "") -> dict[str, Any]:
    return actions.reject(principal, proposal_id, reason)


def verify(proposal_id: str) -> dict[str, Any]:
    return actions.verify_proposal(proposal_id)


def proposals(principal: Principal, *, workspace_id: str = workspace.DEFAULT_WORKSPACE,
              statuses: Any = None, limit: int = 40) -> list[dict[str, Any]]:
    kwargs: dict[str, Any] = {"workspace_id": workspace_id, "limit": limit,
                              "include_tokens": principal.authority is Authority.OPERATOR}
    if statuses:
        kwargs["statuses"] = statuses
    return actions.list_proposals(**kwargs)


def record_detail(principal: Principal, source: str, entity: str, record_id: str) -> dict[str, Any]:
    """One authoritative record read straight from its application, with the
    operations this principal may propose against it."""
    built = graph()
    ref = f"{source}.{entity}"
    described = built.entity(ref)
    if described is None:
        raise NotFound(f"{ref} is not a connected entity")
    record = registry.get(source).get(entity, record_id)
    if record is None:
        raise NotFound(f"{ref} has no record {record_id!r}")
    verdicts = {v["ref"]: v for v in actions.permitted_operations(principal, built)}
    return {
        "entity_ref": ref, "record_id": record_id, "record": record,
        "fields": [f.public() for f in described.fields],
        "title": clean_text(record.get(described.title_field) or record_id, 80),
        "operations": [{**op.public(), **{k: verdicts.get(f"{ref}.{op.id}", {}).get(k)
                                          for k in ("allowed", "reason")}}
                       for op in described.operations],
        "read_at": round(now(), 3),
    }
