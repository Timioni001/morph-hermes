"""The connector contract every MORPH adapter implements.

A connector is the only thing in MORPH that knows how to talk to a particular
application.  Everything above it — the capability graph, the planner, the UI
schema, the action engine — works purely against these descriptors, which is what
makes a new application a new adapter rather than a change to the engine.

Descriptors are parsed from whatever the remote application publishes, so this
module treats them as untrusted input: identifiers must be well formed, labels and
descriptions are flattened to short safe strings, and collections are capped.
"""
from __future__ import annotations

import abc
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

from urllib.parse import quote

from morph.errors import ConnectorError, NotFound, SchemaError
from morph.util import RISK_ORDER, clean_text

FIELD_TYPES = ("id", "string", "text", "int", "float", "money", "bool", "enum", "datetime", "email")
NUMERIC_TYPES = ("int", "float", "money")
PREDICATE_OPS = ("eq", "neq", "in", "nin", "gt", "gte", "lt", "lte", "contains", "exists")
AGGREGATES = ("count", "sum", "avg", "min", "max")

MAX_ENTITIES = 24
MAX_FIELDS = 60
MAX_OPERATIONS = 24
MAX_PARAMS = 8
MAX_ENUM_VALUES = 40

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_EFFECT_PLACEHOLDER = re.compile(r"^\{([a-z][a-z0-9_]{0,39})\}$")


def _parse_effect(raw: Any, param_names: set[str]) -> dict[str, Any]:
    """Accept an operation's effect template, rejecting anything that is not data.

    A value is either a JSON scalar or a ``{param}`` placeholder naming one of the
    operation's own parameters. Nested structures, unknown placeholders and other
    strings-with-braces are dropped: an effect template is a promise about field
    values, not a small language.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in list(raw.items())[:MAX_FIELDS]:
        if not _NAME.match(str(key)):
            continue
        if isinstance(value, str):
            match = _EFFECT_PLACEHOLDER.match(value)
            if match:
                if match.group(1) in param_names:
                    out[str(key)] = value
                continue
            if "{" in value or "}" in value:
                continue
            out[str(key)] = clean_text(value, 120)
            continue
        if value is None or isinstance(value, (bool, int, float)):
            out[str(key)] = value
    return out


def _name(value: Any, what: str) -> str:
    text = str(value or "")
    if not _NAME.match(text):
        raise SchemaError(f"{what} {text!r} is not a valid lower_snake_case identifier")
    return text


@dataclass(frozen=True)
class FieldDescriptor:
    name: str
    type: str
    label: str
    values: tuple[str, ...] = ()
    role: str = ""
    unit: str = ""
    writable: bool = False
    description: str = ""
    required: bool = False
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    max_length: Optional[int] = None

    @property
    def numeric(self) -> bool:
        return self.type in NUMERIC_TYPES

    @classmethod
    def parse(cls, raw: Any, *, what: str = "field") -> "FieldDescriptor":
        if not isinstance(raw, dict):
            raise SchemaError(f"{what} must be an object")
        ftype = str(raw.get("type") or "string")
        if ftype not in FIELD_TYPES:
            ftype = "string"
        values = raw.get("values") or []
        if not isinstance(values, list):
            values = []
        name = _name(raw.get("name"), what)
        return cls(
            name=name,
            type=ftype,
            label=clean_text(raw.get("label") or name.replace("_", " ").title(), 48),
            values=tuple(clean_text(v, 48) for v in values[:MAX_ENUM_VALUES]),
            role=clean_text(raw.get("role"), 24),
            unit=clean_text(raw.get("unit"), 12),
            writable=bool(raw.get("writable")),
            description=clean_text(raw.get("description"), 160),
            required=bool(raw.get("required")),
            minimum=_number(raw.get("minimum")),
            maximum=_number(raw.get("maximum")),
            max_length=int(raw["max_length"]) if str(raw.get("max_length", "")).isdigit() else None,
        )

    def public(self) -> dict[str, Any]:
        out = {"name": self.name, "type": self.type, "label": self.label}
        for key in ("values", "role", "unit", "description", "required", "minimum", "maximum", "max_length"):
            value = getattr(self, key)
            if value not in (None, "", ()):
                out[key] = list(value) if isinstance(value, tuple) else value
        out["writable"] = self.writable
        return out


def _number(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class OperationDescriptor:
    id: str
    label: str
    description: str
    params: tuple[FieldDescriptor, ...] = ()
    effect: dict[str, Any] = field(default_factory=dict)
    effect_fields: tuple[str, ...] = ()
    precondition: tuple[dict[str, Any], ...] = ()
    risk: str = "low"
    reversible: bool = True
    requires_approval: bool = True

    @classmethod
    def parse(cls, raw: Any) -> "OperationDescriptor":
        if not isinstance(raw, dict):
            raise SchemaError("operation must be an object")
        op_id = _name(raw.get("id"), "operation id")
        params_raw = raw.get("params") or []
        if not isinstance(params_raw, list):
            params_raw = []
        risk = str(raw.get("risk") or "low")
        pre = raw.get("precondition") or []
        param_names = {str(p.get("name")) for p in params_raw if isinstance(p, dict)}
        return cls(
            id=op_id,
            label=clean_text(raw.get("label") or op_id.replace("_", " ").title(), 60),
            description=clean_text(raw.get("description"), 200),
            params=tuple(FieldDescriptor.parse(p, what="operation parameter")
                         for p in params_raw[:MAX_PARAMS]),
            effect=_parse_effect(raw.get("effect"), param_names),
            effect_fields=tuple(str(f) for f in (raw.get("effect_fields")
                                                 or list(_parse_effect(raw.get("effect"),
                                                                       param_names)))[:MAX_FIELDS]
                                if _NAME.match(str(f))),
            precondition=tuple(p for p in pre[:8] if isinstance(p, dict)),
            risk=risk if risk in RISK_ORDER else "low",
            reversible=bool(raw.get("reversible", True)),
            # A source may demand approval; it may never waive MORPH's own requirement.
            requires_approval=True,
        )

    def predict(self, params: dict[str, Any]) -> dict[str, Any]:
        """The field values this operation is expected to set, given *params*.

        Used to show the operator what they are approving and, after the write, to
        check the authoritative read-back against what was promised.
        """
        out: dict[str, Any] = {}
        for key, value in self.effect.items():
            if isinstance(value, str):
                match = _EFFECT_PLACEHOLDER.match(value)
                if match:
                    supplied = params.get(match.group(1))
                    if supplied is None:
                        continue
                    out[key] = supplied
                    continue
            out[key] = value
        return out

    def param(self, name: str) -> Optional[FieldDescriptor]:
        return next((p for p in self.params if p.name == name), None)

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "description": self.description,
                "params": [p.public() for p in self.params],
                "effect_fields": list(self.effect_fields), "effect": dict(self.effect),
                "precondition": [dict(p) for p in self.precondition],
                "risk": self.risk, "reversible": self.reversible, "requires_approval": True}


@dataclass(frozen=True)
class EntityDescriptor:
    name: str
    label: str
    description: str
    primary_key: str
    title_field: str
    fields: tuple[FieldDescriptor, ...]
    operations: tuple[OperationDescriptor, ...] = ()
    relationships: tuple[dict[str, Any], ...] = ()
    filter_ops: tuple[str, ...] = PREDICATE_OPS
    sortable: bool = True
    paginated: bool = True
    writable: bool = False
    max_page_size: int = 200

    def field(self, name: str) -> Optional[FieldDescriptor]:
        return next((f for f in self.fields if f.name == name), None)

    def operation(self, op_id: str) -> Optional[OperationDescriptor]:
        return next((o for o in self.operations if o.id == op_id), None)

    @classmethod
    def parse(cls, raw: Any) -> "EntityDescriptor":
        if not isinstance(raw, dict):
            raise SchemaError("entity must be an object")
        name = _name(raw.get("name"), "entity name")
        fields_raw = raw.get("fields") or []
        if not isinstance(fields_raw, list) or not fields_raw:
            raise SchemaError(f"entity {name!r} publishes no fields")
        fields = tuple(FieldDescriptor.parse(f) for f in fields_raw[:MAX_FIELDS])
        names = {f.name for f in fields}
        primary_key = str(raw.get("primary_key") or "id")
        if primary_key not in names:
            raise SchemaError(f"entity {name!r}: primary_key {primary_key!r} is not one of its fields")
        title_field = str(raw.get("title_field") or primary_key)
        if title_field not in names:
            title_field = primary_key
        caps = raw.get("capabilities") or {}
        if not isinstance(caps, dict):
            caps = {}
        ops_raw = raw.get("operations") or []
        operations = tuple(OperationDescriptor.parse(o) for o in ops_raw[:MAX_OPERATIONS]
                           if isinstance(o, dict))
        for op in operations:
            unknown = sorted(set(op.effect_fields) - names)
            if unknown:
                raise SchemaError(
                    f"entity {name!r}: operation {op.id!r} claims unknown field(s) {', '.join(unknown)}")
        filter_ops = tuple(o for o in (caps.get("filter_ops") or PREDICATE_OPS) if o in PREDICATE_OPS)
        rels_raw = raw.get("relationships") or []
        relationships = tuple(
            {"name": str(r.get("name")), "target_entity": str(r.get("target_entity")),
             "local_field": str(r.get("local_field")), "target_field": str(r.get("target_field")),
             "cardinality": "one" if str(r.get("cardinality")) == "one" else "many"}
            for r in rels_raw[:12]
            if isinstance(r, dict) and str(r.get("local_field")) in names)
        return cls(
            name=name,
            label=clean_text(raw.get("label") or name.replace("_", " ").title(), 60),
            description=clean_text(raw.get("description"), 200),
            primary_key=primary_key, title_field=title_field, fields=fields,
            operations=operations, relationships=relationships,
            filter_ops=filter_ops or PREDICATE_OPS,
            sortable=bool(caps.get("sort", True)), paginated=bool(caps.get("paginate", True)),
            writable=bool(operations),
            max_page_size=max(1, min(int(caps.get("max_page_size") or 200), 2000)),
        )

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name, "label": self.label, "description": self.description,
            "primary_key": self.primary_key, "title_field": self.title_field,
            "fields": [f.public() for f in self.fields],
            "operations": [o.public() for o in self.operations],
            "relationships": [dict(r) for r in self.relationships],
            "capabilities": {"read": True, "write": self.writable, "filter_ops": list(self.filter_ops),
                             "sort": self.sortable, "paginate": self.paginated,
                             "max_page_size": self.max_page_size},
        }


@dataclass(frozen=True)
class SourceDescriptor:
    id: str
    label: str
    vendor: str
    description: str
    kind: str
    entities: tuple[EntityDescriptor, ...]
    fetched_at: float = 0.0

    def entity(self, name: str) -> Optional[EntityDescriptor]:
        return next((e for e in self.entities if e.name == name), None)

    @classmethod
    def parse(cls, raw: Any, *, connector_id: str, kind: str, fetched_at: float) -> "SourceDescriptor":
        if not isinstance(raw, dict):
            raise SchemaError(f"connector {connector_id!r} returned a non-object schema")
        entities_raw = raw.get("entities") or []
        if not isinstance(entities_raw, list) or not entities_raw:
            raise SchemaError(f"connector {connector_id!r} published no entities")
        entities = tuple(EntityDescriptor.parse(e) for e in entities_raw[:MAX_ENTITIES])
        names = [e.name for e in entities]
        if len(set(names)) != len(names):
            raise SchemaError(f"connector {connector_id!r} published duplicate entity names")
        return cls(
            id=connector_id,
            label=clean_text(raw.get("label") or connector_id, 60),
            vendor=clean_text(raw.get("vendor"), 60),
            description=clean_text(raw.get("description"), 200),
            kind=kind, entities=entities, fetched_at=fetched_at,
        )

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "vendor": self.vendor,
                "description": self.description, "kind": self.kind,
                "entities": [e.public() for e in self.entities],
                "fetched_at": round(self.fetched_at, 3)}


@dataclass(frozen=True)
class Predicate:
    field: str
    op: str
    value: Any = None

    def public(self) -> dict[str, Any]:
        return {"field": self.field, "op": self.op, "value": self.value}


@dataclass
class FetchResult:
    records: list[dict[str, Any]]
    total: int
    truncated: bool = False


@dataclass
class ApplyResult:
    record: dict[str, Any]
    changed: dict[str, Any]
    replayed: bool = False


RECORD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")


def record_segment(record_id: Any) -> str:
    """A record id made safe to place in a URL path.

    Record ids can come from the model, so one is never trusted to stay inside the
    path it is put in: ``../`` would let an HTTP client collapse the path onto
    another resource. Only a plain identifier is accepted, and it is still
    percent-encoded with no safe characters.
    """
    rid = str(record_id if record_id is not None else "")
    if not RECORD_ID.match(rid) or ".." in rid:
        raise NotFound(f"{rid[:40]!r} is not a valid record id")
    return quote(rid, safe="")


class Connector(abc.ABC):
    """Adapter contract. Implementations are registered in :mod:`morph.registry`."""

    kind = "abstract"

    def __init__(self, config: Any) -> None:
        self.config = config
        self.id = config.id

    # — discovery —
    @abc.abstractmethod
    def describe(self, *, refresh: bool = False) -> SourceDescriptor:
        """Introspect the application's schema."""

    @abc.abstractmethod
    def health(self) -> dict[str, Any]:
        """Cheap liveness probe. Must never raise."""

    # — reads —
    @abc.abstractmethod
    def fetch(self, entity: str, *, filters: Sequence[Predicate] = (), limit: int = 100,
              sort: Optional[tuple[str, str]] = None) -> FetchResult:
        """Read records, with as much of the filtering pushed to the source as it supports."""

    @abc.abstractmethod
    def get(self, entity: str, record_id: str) -> Optional[dict[str, Any]]:
        """Authoritative single-record read, used for pre-checks and verification."""

    # — change feed —
    def changes(self, since: int = 0) -> dict[str, Any]:
        """What this application changed since a cursor.

        Optional: a source that cannot offer one returns ``supported: False`` and
        MORPH falls back to comparing read signatures, which is correct but reads
        everything. The default says "not supported" rather than guessing.
        """
        return {"supported": False, "cursor": since, "changes": []}

    # — writes —
    @abc.abstractmethod
    def apply(self, entity: str, record_id: str, operation: str, params: dict[str, Any], *,
              expected_version: Optional[int], idempotency_key: str, actor: str) -> ApplyResult:
        """Run one named operation. Must enforce optimistic concurrency when
        ``expected_version`` is given and must be idempotent for a repeated key."""

    def normalise_params(self, entity: str, operation: str,
                         params: dict[str, Any]) -> dict[str, Any]:
        """Rewrite *params* into the form the source will report them back in.

        The promise shown to the approver and the read-back check are both built
        from the parameters, so a connector whose source canonicalises a value
        (sorts a list, trims a title) says so here — otherwise a correct write
        would fail verification on formatting alone.
        """
        del entity, operation
        return params

    # — shared helpers —
    def entity_or_raise(self, entity: str) -> EntityDescriptor:
        described = self.describe().entity(entity)
        if described is None:
            raise ConnectorError(f"{self.id!r} has no entity {entity!r}")
        return described

    @staticmethod
    def version_of(record: dict[str, Any]) -> Optional[int]:
        raw = record.get("_version")
        return int(raw) if isinstance(raw, int) else None
