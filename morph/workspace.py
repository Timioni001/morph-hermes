"""Workspace lifecycle: compose, store, transform, and carry context across.

A *transformation* is the interesting operation here.  When the goal changes, MORPH
does not clear the screen and start again: it plans a new workspace, then carries
forward everything from the old one that still makes sense against the new plan —
the record the operator had selected, the filters they pinned — and reports exactly
what was carried and what had to be dropped.  Every revision is kept, so a
transformation is reversible.
"""
from __future__ import annotations

from typing import Any, Optional

from morph import store
from morph.capability import CapabilityGraph
from morph.errors import NotFound, SchemaError
from morph.planner import PlanResult
from morph.uischema import UiSchema, validate, _Errors, _validate_filters
from morph.util import clean_text, now

DEFAULT_WORKSPACE = "main"
MAX_PINNED_PER_ENTITY = 3


def _public(row: Any, *, include_schema: bool = True) -> dict[str, Any]:
    data = dict(row)
    out = {"id": data["id"], "goal": data["goal"], "title": data["title"],
           "revision": data["revision"], "mode": data["mode"],
           "notes": store.loads(data["notes_json"], []),
           "intent": store.loads(data.get("intent_json"), {}),
           "context": store.loads(data["context_json"], {}),
           "created_at": round(data["created_at"], 3),
           "updated_at": round(data["updated_at"], 3)}
    if include_schema:
        out["schema"] = store.loads(data["schema_json"], {})
    return out


def current(workspace_id: str = DEFAULT_WORKSPACE, *, include_schema: bool = True
            ) -> Optional[dict[str, Any]]:
    with store.read() as connection:
        row = connection.execute("SELECT * FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
    return _public(row, include_schema=include_schema) if row else None


def require(workspace_id: str = DEFAULT_WORKSPACE) -> dict[str, Any]:
    found = current(workspace_id)
    if found is None:
        raise NotFound("no workspace has been composed yet; set a goal first")
    return found


def load_schema(workspace_id: str = DEFAULT_WORKSPACE, graph: Optional[CapabilityGraph] = None
                ) -> UiSchema:
    """Re-validate the stored workspace against the live capability graph.

    Re-validation on load is deliberate: an application may have been disconnected
    or changed its schema since the workspace was composed, and a workspace that no
    longer matches reality must fail loudly rather than render stale blocks.
    """
    saved = require(workspace_id)
    if graph is None:
        raise SchemaError("a capability graph is required to load a workspace")
    return validate(saved["schema"], graph)


def revisions(workspace_id: str = DEFAULT_WORKSPACE, limit: int = 20) -> list[dict[str, Any]]:
    with store.read() as connection:
        rows = connection.execute(
            "SELECT workspace_id,revision,goal,title,mode,notes_json,created_at FROM revisions"
            " WHERE workspace_id=? ORDER BY revision DESC LIMIT ?",
            (workspace_id, max(1, min(limit, 100)))).fetchall()
    return [{"revision": r["revision"], "goal": r["goal"], "title": r["title"], "mode": r["mode"],
             "notes": store.loads(r["notes_json"], []), "created_at": round(r["created_at"], 3)}
            for r in rows]


def save(goal: str, result: PlanResult, *, workspace_id: str = DEFAULT_WORKSPACE,
         context: Optional[dict[str, Any]] = None, actor: str = "system",
         authority: str = "system", role: str = "system") -> dict[str, Any]:
    """Store a planned workspace as the next revision."""
    schema_json = store.dumps(result.schema.public())
    context_json = store.dumps(context or {})
    notes_json = store.dumps(result.notes)
    intent_json = store.dumps(result.intent.public() if result.intent else {})
    at = now()
    with store.write() as connection:
        row = connection.execute("SELECT revision,created_at FROM workspaces WHERE id=?",
                                 (workspace_id,)).fetchone()
        revision = int(row["revision"]) + 1 if row else 1
        created = row["created_at"] if row else at
        connection.execute(
            "INSERT INTO workspaces (id,goal,title,revision,schema_json,context_json,mode,"
            "notes_json,intent_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET goal=excluded.goal, title=excluded.title,"
            " revision=excluded.revision, schema_json=excluded.schema_json,"
            " context_json=excluded.context_json, mode=excluded.mode,"
            " notes_json=excluded.notes_json, intent_json=excluded.intent_json,"
            " updated_at=excluded.updated_at",
            (workspace_id, clean_text(goal, 400), result.schema.title, revision, schema_json,
             context_json, result.mode, notes_json, intent_json, created, at))
        connection.execute(
            "INSERT OR REPLACE INTO revisions (workspace_id,revision,goal,title,schema_json,"
            "context_json,mode,notes_json,intent_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (workspace_id, revision, clean_text(goal, 400), result.schema.title, schema_json,
             context_json, result.mode, notes_json, intent_json, at))
        store.append_audit(connection, actor=actor, authority=authority, role=role,
                           kind="workspace_composed", subject=workspace_id,
                           payload={"goal": clean_text(goal, 200), "revision": revision,
                                    "mode": result.mode, "notes": result.notes,
                                    "entities": result.schema.entities_used,
                                    "blocks": len(result.schema.blocks)})
        fresh = connection.execute("SELECT * FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
    return _public(fresh)


def restore(workspace_id: str, revision: int, *, actor: str = "system", authority: str = "system",
            role: str = "system") -> dict[str, Any]:
    """Make an earlier revision current again, as a new revision."""
    with store.write() as connection:
        row = connection.execute(
            "SELECT * FROM revisions WHERE workspace_id=? AND revision=?",
            (workspace_id, int(revision))).fetchone()
        if row is None:
            raise NotFound(f"workspace {workspace_id!r} has no revision {revision}")
        head = connection.execute("SELECT revision,created_at FROM workspaces WHERE id=?",
                                  (workspace_id,)).fetchone()
        next_revision = int(head["revision"]) + 1 if head else 1
        at = now()
        connection.execute(
            "INSERT INTO workspaces (id,goal,title,revision,schema_json,context_json,mode,"
            "notes_json,intent_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET goal=excluded.goal, title=excluded.title,"
            " revision=excluded.revision, schema_json=excluded.schema_json,"
            " context_json=excluded.context_json, mode=excluded.mode,"
            " notes_json=excluded.notes_json, intent_json=excluded.intent_json,"
            " updated_at=excluded.updated_at",
            (workspace_id, row["goal"], row["title"], next_revision, row["schema_json"],
             row["context_json"], f"restored:{revision}", row["notes_json"],
             row["intent_json"] if "intent_json" in row.keys() else "{}",
             head["created_at"] if head else at, at))
        connection.execute(
            "INSERT OR REPLACE INTO revisions (workspace_id,revision,goal,title,schema_json,"
            "context_json,mode,notes_json,intent_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (workspace_id, next_revision, row["goal"], row["title"], row["schema_json"],
             row["context_json"], f"restored:{revision}", row["notes_json"],
             row["intent_json"] if "intent_json" in row.keys() else "{}", at))
        store.append_audit(connection, actor=actor, authority=authority, role=role,
                           kind="workspace_restored", subject=workspace_id,
                           payload={"from_revision": revision, "revision": next_revision})
        fresh = connection.execute("SELECT * FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
    return _public(fresh)


# ───────────────────────────── context handling ────────────────────────────────

def sanitise_context(raw: Any, graph: CapabilityGraph, schema: Optional[UiSchema] = None
                     ) -> tuple[dict[str, Any], list[str]]:
    """Validate a context against the live graph, returning it plus what was dropped."""
    dropped: list[str] = []
    if not isinstance(raw, dict):
        return {}, ["the whole context was not an object"]
    allowed_entities = set(schema.entities_used) if schema else set(graph.entities)

    selection: dict[str, str] = {}
    for ref, record_id in (raw.get("selection") or {}).items():
        ref, record_id = str(ref), clean_text(record_id, 80)
        if ref not in graph.entities:
            dropped.append(f"selection for {ref} (no longer connected)")
            continue
        if ref not in allowed_entities:
            dropped.append(f"selection for {ref} (not shown in this workspace)")
            continue
        if record_id:
            selection[ref] = record_id

    pinned: dict[str, list[dict[str, Any]]] = {}
    for ref, filters in (raw.get("pinned_filters") or {}).items():
        ref = str(ref)
        entity = graph.entity(ref)
        if entity is None or ref not in allowed_entities:
            dropped.append(f"pinned filters for {ref}")
            continue
        errors = _Errors()
        clean = _validate_filters(filters, entity, errors, f"pinned_filters.{ref}")
        if errors:
            dropped.append(f"pinned filters for {ref} ({errors[0]})")
        if clean:
            pinned[ref] = [dict(f) for f in clean[:MAX_PINNED_PER_ENTITY]]

    out: dict[str, Any] = {}
    if selection:
        out["selection"] = selection
    if pinned:
        out["pinned_filters"] = pinned
    note = clean_text(raw.get("note"), 200)
    if note:
        out["note"] = note
    return out, dropped


def set_context(workspace_id: str, patch: Any, graph: CapabilityGraph,
                schema: Optional[UiSchema] = None) -> dict[str, Any]:
    """Merge an operator interaction (a selection, a pinned filter) into the context."""
    saved = require(workspace_id)
    merged = dict(saved["context"] or {})
    incoming, dropped = sanitise_context(patch, graph, schema)
    for key in ("selection", "pinned_filters"):
        if key in incoming:
            current_value = dict(merged.get(key) or {})
            current_value.update(incoming[key])
            merged[key] = {k: v for k, v in current_value.items() if v}
    if "note" in incoming:
        merged["note"] = incoming["note"]
    if isinstance(patch, dict) and patch.get("clear"):
        for ref in [str(r) for r in patch["clear"]] if isinstance(patch["clear"], list) else []:
            for key in ("selection", "pinned_filters"):
                if isinstance(merged.get(key), dict):
                    merged[key].pop(ref, None)
    with store.write() as connection:
        connection.execute("UPDATE workspaces SET context_json=?, updated_at=? WHERE id=?",
                           (store.dumps(merged), now(), workspace_id))
    return {"context": merged, "dropped": dropped}


def carry_context(previous: dict[str, Any], new_schema: UiSchema,
                  graph: CapabilityGraph) -> tuple[dict[str, Any], list[str], list[str]]:
    """Carry an old context into a new workspace.

    Returns ``(context, carried, dropped)`` where both lists are human-readable, so
    the dashboard and the agent can say what survived a transformation.
    """
    kept, dropped = sanitise_context(previous or {}, graph, new_schema)
    carried: list[str] = []
    for ref, record_id in (kept.get("selection") or {}).items():
        carried.append(f"selected {ref} {record_id}")
    for ref, filters in (kept.get("pinned_filters") or {}).items():
        carried.append(f"{len(filters)} pinned filter(s) on {ref}")
    return kept, carried, dropped
