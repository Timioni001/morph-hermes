"""Shared runtime for MORPH's independent demo application services.

Each service in :mod:`apps` is a *separate* application: its own process, its own
SQLite database, its own bearer token and its own HTTP API.  MORPH reaches them
only over HTTP, exactly as it would reach a third-party SaaS product, so the
integration demonstrated by this repository is a real cross-application one.

The runtime deliberately stays small and declarative:

* :class:`FieldSpec` / :class:`EntitySpec` / :class:`OperationSpec` describe an
  application's schema, and the same description is published verbatim at
  ``GET /morph/schema`` — that is the introspection endpoint MORPH consumes.
* Operation effects are *data*, never code: a mapping of field name to a literal
  or a ``"{param}"`` placeholder.  Nothing supplied over the wire is evaluated.
* Writes carry optimistic concurrency (``If-Match`` on the record version) and
  idempotency (``Idempotency-Key``), and answer with the stored record read back
  from the database after the commit.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

__all__ = [
    "FieldSpec", "OperationSpec", "EntitySpec", "AppSpec", "build_app",
    "data_dir", "token_for", "PREDICATE_OPS",
]

# Filter predicates a service supports server-side.  MORPH's UI schema validator
# checks a plan's filters against this list, per entity, before any fetch.
PREDICATE_OPS = ("eq", "neq", "in", "nin", "gt", "gte", "lt", "lte", "contains", "exists")

_IDENT = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_PLACEHOLDER = re.compile(r"^\{([a-z][a-z0-9_]{0,39})\}$")
_MAX_LIMIT = 200
_LOCK = threading.RLock()


# ───────────────────────────── schema description ──────────────────────────────

@dataclass(frozen=True)
class FieldSpec:
    """One column of an entity, as published to schema consumers."""

    name: str
    type: str  # string | text | int | float | money | bool | enum | datetime | email | id
    label: str = ""
    values: tuple[str, ...] = ()       # enum domain
    role: str = ""                     # status | title | timestamp | amount | key | metric | flag
    unit: str = ""
    writable: bool = False             # only ever written through an operation
    description: str = ""
    required: bool = False             # operation parameters only
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    max_length: Optional[int] = None

    def public(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "type": self.type,
                               "label": self.label or self.name.replace("_", " ").title()}
        for key, value in (("values", list(self.values)), ("role", self.role), ("unit", self.unit),
                           ("description", self.description), ("required", self.required),
                           ("minimum", self.minimum), ("maximum", self.maximum),
                           ("max_length", self.max_length)):
            if value not in (None, "", [], False):
                out[key] = value
        out["writable"] = self.writable
        return out


@dataclass(frozen=True)
class OperationSpec:
    """A typed, named mutation the application is willing to perform.

    ``effect`` maps a field name to a literal value or to ``"{param}"``, which is
    substituted with the matching parameter value (type preserved).  ``precondition``
    is a list of predicates the record must satisfy; it is what makes a repeat of an
    already-applied operation a 409 rather than a silent no-op.
    """

    id: str
    label: str
    description: str
    effect: dict[str, Any]
    params: tuple[FieldSpec, ...] = ()
    precondition: tuple[dict[str, Any], ...] = ()
    risk: str = "low"  # low | medium | high
    reversible: bool = True

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id, "label": self.label, "description": self.description,
            "params": [p.public() for p in self.params],
            "effect_fields": sorted(self.effect),
            # The effect template is published so a consumer can show an operator
            # exactly what a write will set, and verify it afterwards. Values are
            # literals or "{param}" placeholders — never expressions.
            "effect": dict(self.effect),
            "precondition": [dict(p) for p in self.precondition],
            "risk": self.risk, "reversible": self.reversible,
            # Every write through MORPH needs a human decision; the source system
            # states that requirement itself so a connector cannot quietly drop it.
            "requires_approval": True,
        }


@dataclass(frozen=True)
class EntitySpec:
    name: str
    label: str
    fields: tuple[FieldSpec, ...]
    seed: tuple[dict[str, Any], ...]
    primary_key: str = "id"
    title_field: str = ""
    operations: tuple[OperationSpec, ...] = ()
    relationships: tuple[dict[str, Any], ...] = ()  # intra-application links
    description: str = ""

    def field_map(self) -> dict[str, FieldSpec]:
        return {f.name: f for f in self.fields}

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name, "label": self.label, "description": self.description,
            "primary_key": self.primary_key, "title_field": self.title_field or self.primary_key,
            "fields": [f.public() for f in self.fields],
            "operations": [o.public() for o in self.operations],
            "relationships": [dict(r) for r in self.relationships],
            "capabilities": {
                "read": True,
                "write": bool(self.operations),
                "filter_ops": list(PREDICATE_OPS),
                "sort": True,
                "paginate": True,
                "max_page_size": _MAX_LIMIT,
                "optimistic_concurrency": "version",
                "idempotent_writes": True,
            },
        }


@dataclass
class AppSpec:
    id: str
    label: str
    description: str
    entities: tuple[EntitySpec, ...]
    token_env: str
    port: int
    vendor: str = ""
    db_name: str = ""
    accent: str = "#475569"
    entity_map: dict[str, EntitySpec] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        if not _IDENT.match(self.id):
            raise ValueError(f"invalid application id: {self.id!r}")
        self.db_name = self.db_name or f"{self.id}.db"
        self.entity_map = {e.name: e for e in self.entities}


# ──────────────────────────────── persistence ──────────────────────────────────

def data_dir() -> Path:
    """Where the demo applications keep their databases and bearer tokens."""
    root = Path(os.environ.get("MORPH_DEMO_DATA_DIR") or (Path.home() / ".morph-demo"))
    root = root.expanduser()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def token_for(spec: AppSpec) -> str:
    """The service's bearer token: from its environment variable, else a generated
    one persisted next to its database with owner-only permissions."""
    from_env = os.environ.get(spec.token_env, "").strip()
    if from_env:
        return from_env
    path = data_dir() / f"{spec.id}.token"
    with _LOCK:
        if path.exists():
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        token = secrets.token_urlsafe(32)
        path.write_text(token + "\n", encoding="utf-8")
        path.chmod(0o600)
        return token


def _connect(spec: AppSpec) -> sqlite3.Connection:
    db = sqlite3.connect(str(data_dir() / spec.db_name), timeout=15, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=15000")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def _init_db(spec: AppSpec) -> None:
    with _LOCK, _connect(spec) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS records (
                entity TEXT NOT NULL, id TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
                updated_at REAL NOT NULL, payload TEXT NOT NULL, PRIMARY KEY (entity, id));
            CREATE TABLE IF NOT EXISTS op_log (
                idempotency_key TEXT PRIMARY KEY, entity TEXT NOT NULL, record_id TEXT NOT NULL,
                operation TEXT NOT NULL, result TEXT NOT NULL, created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS changelog (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, entity TEXT NOT NULL, record_id TEXT NOT NULL,
                operation TEXT NOT NULL, changed TEXT NOT NULL, at REAL NOT NULL, actor TEXT NOT NULL);
            """
        )
        # Seeding is idempotent and serialised across processes. Several processes
        # legitimately open the same database at once (the service itself, a
        # `--print-tokens` run, a test fixture), and without BEGIN IMMEDIATE two of
        # them both saw an empty table and both inserted.
        db.execute("BEGIN IMMEDIATE")
        try:
            now = time.time()
            for entity in spec.entities:
                for row in entity.seed:
                    db.execute(
                        "INSERT OR IGNORE INTO records (entity,id,version,updated_at,payload)"
                        " VALUES (?,?,1,?,?)",
                        (entity.name, str(row[entity.primary_key]), now,
                         json.dumps(row, ensure_ascii=False)))
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise


# ───────────────────────────── predicate evaluation ────────────────────────────

def _coerce(spec: FieldSpec | None, raw: Any) -> Any:
    if spec is None or raw is None:
        return raw
    try:
        if spec.type in ("int",):
            return int(raw)
        if spec.type in ("float", "money"):
            return float(raw)
        if spec.type == "bool":
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() in ("1", "true", "yes", "on")
    except (TypeError, ValueError):
        return raw
    return raw


def _matches(record: dict[str, Any], predicate: dict[str, Any], fields: dict[str, FieldSpec]) -> bool:
    name, op = predicate.get("field"), predicate.get("op", "eq")
    if name not in fields:
        return False
    want = _coerce(fields[name], predicate.get("value"))
    have = record.get(name)
    if op == "exists":
        return (have is not None) == bool(want)
    if have is None:
        return op in ("neq", "nin")
    try:
        if op == "eq":
            return have == want
        if op == "neq":
            return have != want
        if op == "in":
            return isinstance(want, list) and have in want
        if op == "nin":
            return isinstance(want, list) and have not in want
        if op == "contains":
            return str(want).lower() in str(have).lower()
        if op == "gt":
            return have > want
        if op == "gte":
            return have >= want
        if op == "lt":
            return have < want
        if op == "lte":
            return have <= want
    except TypeError:
        return False
    return False


def _resolve_effect(effect: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Substitute ``{param}`` placeholders. Values are copied, never evaluated."""
    out: dict[str, Any] = {}
    for key, value in effect.items():
        if isinstance(value, str):
            match = _PLACEHOLDER.match(value)
            if match:
                out[key] = params.get(match.group(1))
                continue
        out[key] = value
    return out


def _validate_params(op: OperationSpec, raw: Any) -> dict[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise HTTPException(422, "params must be an object")
    allowed = {p.name: p for p in op.params}
    unknown = sorted(set(raw) - set(allowed))
    if unknown:
        raise HTTPException(422, f"unknown parameter(s): {', '.join(unknown)}")
    clean: dict[str, Any] = {}
    for name, spec in allowed.items():
        if name not in raw or raw[name] is None or raw[name] == "":
            if spec.required:
                raise HTTPException(422, f"parameter {name!r} is required")
            continue
        value = raw[name]
        if spec.type in ("int",):
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise HTTPException(422, f"parameter {name!r} must be an integer") from None
        elif spec.type in ("float", "money"):
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise HTTPException(422, f"parameter {name!r} must be a number") from None
        elif spec.type == "bool":
            value = bool(value)
        else:
            if not isinstance(value, str):
                raise HTTPException(422, f"parameter {name!r} must be a string")
            value = value.strip()
            limit = spec.max_length or 400
            if len(value) > limit:
                raise HTTPException(422, f"parameter {name!r} exceeds {limit} characters")
            if spec.values and value not in spec.values:
                raise HTTPException(422, f"parameter {name!r} must be one of: {', '.join(spec.values)}")
        if spec.minimum is not None and isinstance(value, (int, float)) and value < spec.minimum:
            raise HTTPException(422, f"parameter {name!r} must be >= {spec.minimum}")
        if spec.maximum is not None and isinstance(value, (int, float)) and value > spec.maximum:
            raise HTTPException(422, f"parameter {name!r} must be <= {spec.maximum}")
        clean[name] = value
    return clean


# ──────────────────────────────── application ──────────────────────────────────

def build_app(spec: AppSpec) -> FastAPI:
    """Build the FastAPI application for one independent demo service."""
    _init_db(spec)
    expected_token = token_for(spec)

    app = FastAPI(title=f"{spec.label} API", version="1.0.0", docs_url=None, redoc_url=None)
    app.state.spec = spec

    def require_token(authorization: str = Header(default="")) -> str:
        parts = authorization.split(" ", 1)
        supplied = parts[1].strip() if len(parts) == 2 and parts[0].lower() == "bearer" else ""
        if not supplied or not secrets.compare_digest(supplied, expected_token):
            raise HTTPException(401, "a valid bearer token is required")
        return supplied

    def entity_or_404(name: str) -> EntitySpec:
        entity = spec.entity_map.get(name)
        if entity is None:
            raise HTTPException(404, f"unknown entity {name!r}")
        return entity

    def read_rows(entity: EntitySpec) -> list[dict[str, Any]]:
        with _connect(spec) as db:
            rows = db.execute(
                "SELECT id,version,updated_at,payload FROM records WHERE entity=? ORDER BY id",
                (entity.name,),
            ).fetchall()
        out = []
        for row in rows:
            payload = json.loads(row["payload"])
            payload["_version"] = row["version"]
            payload["_updated_at"] = round(row["updated_at"], 3)
            out.append(payload)
        return out

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "application": spec.id, "label": spec.label,
                "entities": [e.name for e in spec.entities], "time": round(time.time(), 3)}

    @app.get("/morph/schema")
    def schema(_: str = Depends(require_token)) -> dict[str, Any]:
        return {
            "schema_version": "morph.source.v1",
            "id": spec.id, "label": spec.label, "vendor": spec.vendor or spec.label,
            "description": spec.description,
            "entities": [e.public() for e in spec.entities],
        }

    @app.get("/v1/_changes")
    def changes(
        since: int = Query(default=0, ge=0),
        limit: int = Query(default=200, ge=1, le=1000),
        _: str = Depends(require_token),
    ) -> dict[str, Any]:
        """Everything this application has changed since sequence *since*.

        A consumer that keeps the cursor can ask "what moved?" in one cheap call
        instead of re-reading every record, which is the difference between polling
        that scales and polling that does not.
        """
        with _connect(spec) as db:
            rows = db.execute(
                "SELECT seq,entity,record_id,operation,at,actor FROM changelog"
                " WHERE seq > ? ORDER BY seq ASC LIMIT ?", (since, limit)).fetchall()
            head = db.execute("SELECT COALESCE(MAX(seq),0) FROM changelog").fetchone()[0]
        return {
            "application": spec.id, "since": since, "cursor": head,
            "has_more": bool(rows) and rows[-1]["seq"] < head,
            "changes": [{"seq": r["seq"], "entity": r["entity"], "record_id": r["record_id"],
                         "operation": r["operation"], "at": round(r["at"], 3),
                         "actor": r["actor"]} for r in rows],
        }

    @app.get("/v1/{entity_name}")
    def list_records(
        entity_name: str,
        request: Request,
        limit: int = Query(default=100, ge=1, le=_MAX_LIMIT),
        offset: int = Query(default=0, ge=0),
        _: str = Depends(require_token),
    ) -> dict[str, Any]:
        entity = entity_or_404(entity_name)
        fields = entity.field_map()
        rows = read_rows(entity)
        for key, value in request.query_params.multi_items():
            if key in ("limit", "offset"):
                continue
            name, _, op = key.partition("__")
            if name not in fields:
                raise HTTPException(422, f"unknown filter field {name!r}")
            op = op or "eq"
            if op not in PREDICATE_OPS:
                raise HTTPException(422, f"unsupported filter operator {op!r}")
            parsed: Any = value
            if op in ("in", "nin"):
                parsed = [v for v in value.split(",") if v != ""]
                parsed = [_coerce(fields[name], v) for v in parsed]
            rows = [r for r in rows if _matches(r, {"field": name, "op": op, "value": parsed}, fields)]
        total = len(rows)
        return {"items": rows[offset:offset + limit], "total": total,
                "limit": limit, "offset": offset, "entity": entity.name}

    @app.get("/v1/{entity_name}/{record_id}")
    def get_record(entity_name: str, record_id: str, _: str = Depends(require_token)) -> dict[str, Any]:
        entity = entity_or_404(entity_name)
        for row in read_rows(entity):
            if str(row.get(entity.primary_key)) == record_id:
                return row
        raise HTTPException(404, "record not found")

    @app.get("/v1/{entity_name}/{record_id}/history")
    def record_history(entity_name: str, record_id: str, _: str = Depends(require_token)) -> dict[str, Any]:
        entity_or_404(entity_name)
        with _connect(spec) as db:
            rows = db.execute(
                "SELECT seq,operation,changed,at,actor FROM changelog WHERE entity=? AND record_id=?"
                " ORDER BY seq DESC LIMIT 50", (entity_name, record_id)).fetchall()
        return {"entries": [{"seq": r["seq"], "operation": r["operation"], "actor": r["actor"],
                             "at": round(r["at"], 3), "changed": json.loads(r["changed"])} for r in rows]}

    @app.post("/v1/{entity_name}/{record_id}/operations/{operation_id}")
    def run_operation(
        entity_name: str,
        record_id: str,
        operation_id: str,
        body: Optional[dict[str, Any]] = None,
        if_match: str = Header(default="", alias="If-Match"),
        idempotency_key: str = Header(default="", alias="Idempotency-Key"),
        actor: str = Header(default="", alias="X-Actor"),
        _: str = Depends(require_token),
    ) -> JSONResponse:
        entity = entity_or_404(entity_name)
        operation = next((o for o in entity.operations if o.id == operation_id), None)
        if operation is None:
            raise HTTPException(404, f"{entity.name} does not expose operation {operation_id!r}")
        params = _validate_params(operation, (body or {}).get("params"))
        key = idempotency_key.strip()[:120]
        actor = (actor.strip() or "unknown")[:80]

        with _LOCK, _connect(spec) as db:
            if key:
                prior = db.execute("SELECT result FROM op_log WHERE idempotency_key=?", (key,)).fetchone()
                if prior is not None:
                    stored = json.loads(prior["result"])
                    stored["replayed"] = True
                    return JSONResponse(stored)
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT version,payload FROM records WHERE entity=? AND id=?",
                             (entity.name, record_id)).fetchone()
            if row is None:
                db.execute("ROLLBACK")
                raise HTTPException(404, "record not found")
            if if_match.strip() and if_match.strip().strip('"') != str(row["version"]):
                db.execute("ROLLBACK")
                raise HTTPException(409, f"version mismatch: record is at version {row['version']}")
            record = json.loads(row["payload"])
            fields = entity.field_map()
            for predicate in operation.precondition:
                if not _matches(record, dict(predicate), fields):
                    db.execute("ROLLBACK")
                    raise HTTPException(409, f"precondition not met: {predicate.get('message') or predicate}")
            updates = _resolve_effect(operation.effect, params)
            unknown = sorted(set(updates) - set(fields))
            if unknown:
                db.execute("ROLLBACK")
                raise HTTPException(500, f"operation writes unknown field(s): {', '.join(unknown)}")
            before = {k: record.get(k) for k in updates}
            record.update({k: v for k, v in updates.items() if v is not None})
            now = time.time()
            db.execute("UPDATE records SET payload=?, version=version+1, updated_at=? WHERE entity=? AND id=?",
                       (json.dumps(record, ensure_ascii=False), now, entity.name, record_id))
            db.execute("INSERT INTO changelog (entity,record_id,operation,changed,at,actor) VALUES (?,?,?,?,?,?)",
                       (entity.name, record_id, operation.id,
                        json.dumps({"before": before, "after": {k: record.get(k) for k in updates}}), now, actor))
            fresh = db.execute("SELECT version,updated_at,payload FROM records WHERE entity=? AND id=?",
                               (entity.name, record_id)).fetchone()
            stored = json.loads(fresh["payload"])
            stored["_version"] = fresh["version"]
            stored["_updated_at"] = round(fresh["updated_at"], 3)
            result = {"applied": True, "replayed": False, "operation": operation.id,
                      "entity": entity.name, "record_id": record_id,
                      "changed": {k: stored.get(k) for k in updates}, "record": stored}
            if key:
                db.execute("INSERT OR REPLACE INTO op_log (idempotency_key,entity,record_id,operation,result,created_at)"
                           " VALUES (?,?,?,?,?,?)",
                           (key, entity.name, record_id, operation.id, json.dumps(result), now))
            db.execute("COMMIT")
        return JSONResponse(result)

    @app.get("/", response_class=HTMLResponse)
    def viewer() -> str:
        """A plain server-rendered view of this application's own records.

        It exists so a human (or a browser test) can confirm a change landed in the
        source system itself, not only in MORPH.  No token: it is read-only and the
        demo services bind to loopback.
        """
        return _render_viewer(spec, {e.name: read_rows(e) for e in spec.entities})

    return app


def _esc(value: Any) -> str:
    text = "—" if value is None else ("Yes" if value is True else "No" if value is False else str(value))
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;").replace("'", "&#39;"))


def _render_viewer(spec: AppSpec, data: dict[str, list[dict[str, Any]]]) -> str:
    parts: list[str] = []
    for entity in spec.entities:
        rows = data.get(entity.name, [])
        # Show what a person checking on a record cares about: its key, its name,
        # its status-like fields, and every field an operation can write — so a
        # change made through an integration is visible here without hunting.
        preferred = [entity.primary_key, entity.title_field]
        preferred += [f.name for f in entity.fields if f.writable]
        preferred += [f.name for f in entity.fields
                      if f.role in ("status", "metric", "amount")]
        preferred += [f.name for f in entity.fields]
        columns: list[str] = []
        for name in preferred:
            if name not in columns:
                columns.append(name)
            if len(columns) >= 10:
                break
        head = "".join(f"<th>{_esc(c.replace('_', ' ').title())}</th>" for c in columns + ["Version"])
        body = "".join(
            "<tr>" + "".join(f"<td>{_esc(r.get(c))}</td>" for c in columns)
            + f"<td class='v'>v{_esc(r.get('_version'))}</td></tr>"
            for r in rows)
        parts.append(
            f"<section><h2>{_esc(entity.label)} <span class='count'>{len(rows)}</span></h2>"
            f"<div class='scroll'><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div></section>")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(spec.label)} — source system</title>
<style>
 :root {{ --accent: {_esc(spec.accent)}; }}
 * {{ box-sizing: border-box; }}
 body {{ margin:0; background:#fff; color:#0f172a; font:15px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Inter,sans-serif; }}
 header {{ padding:28px max(16px, env(safe-area-inset-left)) 18px; border-bottom:1px solid #e8ecf3; }}
 h1 {{ margin:0; font-size:22px; letter-spacing:-.02em; }}
 header p {{ margin:6px 0 0; color:#5b6575; font-size:13.5px; }}
 .tag {{ display:inline-block; margin-top:10px; padding:3px 9px; border-radius:999px; background:color-mix(in srgb, var(--accent) 10%, #fff); color:var(--accent); font-size:12px; font-weight:600; }}
 main {{ padding:20px max(16px, env(safe-area-inset-left)) 56px; display:grid; gap:26px; }}
 h2 {{ font-size:14px; text-transform:uppercase; letter-spacing:.07em; color:#5b6575; margin:0 0 10px; }}
 .count {{ background:#f2f5f9; border-radius:999px; padding:2px 8px; font-size:12px; color:#0f172a; }}
 .scroll {{ overflow-x:auto; border:1px solid #e8ecf3; border-radius:12px; }}
 table {{ border-collapse:collapse; width:100%; font-size:13.5px; }}
 th,td {{ text-align:left; padding:9px 12px; border-bottom:1px solid #f1f4f8; white-space:nowrap; }}
 th {{ background:#fafbfd; font-weight:600; color:#49536a; font-size:12px; text-transform:uppercase; letter-spacing:.04em; }}
 tbody tr:last-child td {{ border-bottom:0; }}
 td.v {{ color:var(--accent); font-variant-numeric:tabular-nums; font-weight:600; }}
</style></head>
<body><header><h1>{_esc(spec.label)}</h1><p>{_esc(spec.description)}</p>
<div class="tag">Independent application · port {_esc(spec.port)} · own database</div></header>
<main>{''.join(parts)}</main></body></html>"""
