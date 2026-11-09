"""Data resolution: run a validated workspace against the connected applications.

One resolve pass performs the smallest set of reads that satisfies every block
(identical entity+filter combinations are fetched once), aggregates in process, and
joins across applications in memory because no application can join against
another's database.

Three properties matter more than speed here:

* **Partial failure is visible.** An application that is down fails its own blocks
  and nothing else; the resolution is marked ``degraded`` and names what is missing.
  MORPH never presents a partial read as a complete one.
* **Change detection is real.** Each entity read yields a signature over record
  ids and versions, so a later pass can say precisely what was added, removed or
  changed, rather than "something moved".
* **Nothing is cached across passes.** Every number on screen comes from a read
  performed in that pass, and ``fetched_at`` says when.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from morph import registry
from morph.capability import CapabilityGraph
from morph.connectors import Predicate
from morph.errors import MorphError
from morph.uischema import Block, UiSchema
from morph.util import clean_text, digest, now

MAX_JOIN_RECORDS = 200


@dataclass
class EntityRead:
    ref: str
    records: list[dict[str, Any]]
    total: int
    truncated: bool
    signature: dict[str, int]


@dataclass
class Resolution:
    blocks: dict[str, dict[str, Any]] = field(default_factory=dict)
    signatures: dict[str, dict[str, int]] = field(default_factory=dict)
    failures: list[dict[str, str]] = field(default_factory=list)
    fetched_at: float = 0.0
    degraded: bool = False
    changes: list[dict[str, Any]] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return {"blocks": self.blocks, "failures": self.failures, "degraded": self.degraded,
                "fetched_at": round(self.fetched_at, 3), "changes": self.changes,
                "signature_digest": {ref: digest(sig) for ref, sig in self.signatures.items()}}


def _predicates(block: Block, pinned: Optional[dict[str, list[dict[str, Any]]]] = None
                ) -> list[Predicate]:
    """A block's own filters plus any the operator pinned for that entity.

    Pinned filters survive a transformation, which is how a narrowed view stays
    narrowed when the goal changes. They are validated against the capability graph
    before they ever reach here.
    """
    raw = list(block.filters) + list((pinned or {}).get(block.entity, []))
    return [Predicate(f["field"], f["op"], f.get("value")) for f in raw]


def _aggregate(records: list[dict[str, Any]], aggregate: str, field_name: str) -> Optional[float]:
    if aggregate == "count":
        return len(records)
    values = [r.get(field_name) for r in records]
    numbers = [float(v) for v in values
               if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not numbers:
        return None
    if aggregate == "sum":
        return round(sum(numbers), 2)
    if aggregate == "avg":
        return round(sum(numbers) / len(numbers), 2)
    if aggregate == "min":
        return round(min(numbers), 2)
    if aggregate == "max":
        return round(max(numbers), 2)
    return None


def _project(record: dict[str, Any], columns: Iterable[str]) -> dict[str, Any]:
    out = {name: record.get(name) for name in columns}
    if "_version" in record:
        out["_version"] = record["_version"]
    return out


class _Reader:
    """Per-pass read cache. Identical entity+filter+limit requests hit once."""

    def __init__(self, graph: CapabilityGraph) -> None:
        self.graph = graph
        self._cache: dict[str, EntityRead] = {}
        self.failures: dict[str, str] = {}

    def read(self, ref: str, predicates: list[Predicate], limit: int) -> EntityRead:
        key = f"{ref}|{digest([p.public() for p in predicates])}|{limit}"
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        app, entity = ref.split(".", 1)
        if ref in self.failures or app in self.failures:
            raise MorphError(self.failures.get(ref) or self.failures[app])
        try:
            result = registry.get(app).fetch(entity, filters=predicates, limit=limit)
        except MorphError as exc:
            self.failures[app] = exc.message
            raise
        except Exception as exc:  # noqa: BLE001 - a broken adapter is a source outage
            self.failures[app] = f"{type(exc).__name__}: {exc}"
            raise MorphError(self.failures[app]) from exc
        signature = {str(r.get(self.key_of(ref))): int(r.get("_version") or 0)
                     for r in result.records}
        read = EntityRead(ref=ref, records=result.records, total=result.total,
                          truncated=result.truncated, signature=signature)
        self._cache[key] = read
        return read

    def key_of(self, ref: str) -> str:
        entity = self.graph.entity(ref)
        return entity.primary_key if entity else "id"

    @property
    def signatures(self) -> dict[str, dict[str, int]]:
        merged: dict[str, dict[str, int]] = {}
        for read in self._cache.values():
            merged.setdefault(read.ref, {}).update(read.signature)
        return merged


def resolve(schema: UiSchema, graph: CapabilityGraph, *,
            context: Optional[dict[str, Any]] = None,
            previous_signatures: Optional[dict[str, dict[str, int]]] = None) -> Resolution:
    context = context or {}
    selection: dict[str, str] = {k: str(v) for k, v in (context.get("selection") or {}).items()}
    pinned: dict[str, list[dict[str, Any]]] = {
        str(k): list(v) for k, v in (context.get("pinned_filters") or {}).items()
        if isinstance(v, list)}
    reader = _Reader(graph)
    out = Resolution(fetched_at=now())

    for block in schema.blocks:
        try:
            out.blocks[block.id] = _resolve_block(block, graph, reader, selection, pinned)
        except MorphError as exc:
            out.blocks[block.id] = {"error": clean_text(exc.message, 200), "code": exc.code}
        except Exception as exc:  # noqa: BLE001 - one bad block never takes the workspace down
            out.blocks[block.id] = {"error": f"{type(exc).__name__}", "code": "block_failed"}

    out.signatures = reader.signatures
    out.failures = [{"source": app, "error": clean_text(message, 200)}
                    for app, message in sorted(reader.failures.items())]
    out.degraded = bool(out.failures) or any("error" in b for b in out.blocks.values())
    if previous_signatures:
        out.changes = diff_signatures(previous_signatures, out.signatures)
    return out


def _resolve_block(block: Block, graph: CapabilityGraph, reader: _Reader,
                   selection: dict[str, str],
                   pinned: Optional[dict[str, list[dict[str, Any]]]] = None) -> dict[str, Any]:
    if block.kind == "workflow":
        return {"steps": [dict(s) for s in block.steps]}

    entity = graph.entity(block.entity)
    if entity is None:
        raise MorphError(f"{block.entity} is no longer connected")
    predicates = _predicates(block, pinned)
    applied_pins = list((pinned or {}).get(block.entity, []))

    if block.kind == "metric":
        read = reader.read(block.entity, predicates, entity.max_page_size)
        value = _aggregate(read.records, block.aggregate, block.field)
        return {"value": value, "aggregate": block.aggregate, "unit": block.unit,
                "sample_size": len(read.records), "total": read.total,
                "partial": read.truncated}

    if block.kind == "table":
        read = reader.read(block.entity, predicates, block.limit)
        records = read.records
        if block.sort:
            name, direction = block.sort["field"], block.sort["direction"]
            records = sorted(records, key=lambda r: (r.get(name) is None, _key(r.get(name))),
                             reverse=direction == "desc")
        return {"columns": list(block.columns), "primary_key": entity.primary_key,
                "rows": [_project(r, block.columns) for r in records],
                "total": read.total, "truncated": read.truncated,
                "row_actions": _operation_specs(entity, block.row_actions),
                "pinned_filters": applied_pins,
                "selected": selection.get(block.entity, "")}

    if block.kind == "chart":
        read = reader.read(block.entity, predicates, entity.max_page_size)
        buckets: dict[str, list[dict[str, Any]]] = {}
        for record in read.records:
            label = record.get(block.group_by)
            key = "—" if label is None else clean_text(label, 32)
            buckets.setdefault(key, []).append(record)
        points = [{"label": key, "value": _aggregate(rows, block.aggregate, block.field) or 0,
                   "count": len(rows)} for key, rows in buckets.items()]
        points.sort(key=lambda p: (-p["value"], p["label"]))
        return {"chart_type": block.chart_type, "group_by": block.group_by,
                "aggregate": block.aggregate, "unit": block.unit,
                "points": points[:block.limit], "sample_size": len(read.records)}

    if block.kind == "board":
        read = reader.read(block.entity, predicates, block.limit)
        descriptor = entity.field(block.group_by)
        order: list[str] = []
        if descriptor is not None and descriptor.values:
            order = list(descriptor.values)
        elif descriptor is not None and descriptor.type == "bool":
            order = ["true", "false"]
        lanes: dict[str, list[dict[str, Any]]] = {key: [] for key in order}
        for record in read.records:
            raw = record.get(block.group_by)
            key = ("true" if raw else "false") if isinstance(raw, bool) else (
                "—" if raw is None else clean_text(raw, 40))
            lanes.setdefault(key, []).append(record)
        return {"group_by": block.group_by, "columns": list(block.columns),
                "primary_key": entity.primary_key, "title_field": entity.title_field,
                "lanes": [{"key": key, "label": key.replace("_", " ").title(),
                           "count": len(rows),
                           "cards": [{"id": str(r.get(entity.primary_key)),
                                      "title": clean_text(r.get(entity.title_field)
                                                          or r.get(entity.primary_key), 60),
                                      "fields": _project(r, block.columns)}
                                     for r in rows[:12]]}
                          for key, rows in lanes.items()],
                "total": read.total, "truncated": read.truncated,
                "row_actions": _operation_specs(entity, block.row_actions),
                "selected": selection.get(block.entity, "")}

    if block.kind == "timeline":
        read = reader.read(block.entity, predicates, entity.max_page_size)
        dated = [r for r in read.records if r.get(block.time_field)]
        dated.sort(key=lambda r: str(r.get(block.time_field)), reverse=True)
        items = []
        for record in dated[:block.limit]:
            items.append({
                "id": str(record.get(entity.primary_key)),
                "at": record.get(block.time_field),
                "label": clean_text(record.get(block.label_field)
                                    or record.get(entity.primary_key), 80),
                "status": record.get(block.status_field) if block.status_field else None,
            })
        return {"time_field": block.time_field, "label_field": block.label_field,
                "status_field": block.status_field, "items": items,
                "total": read.total, "undated": len(read.records) - len(dated),
                "row_actions": _operation_specs(entity, block.row_actions),
                "selected": selection.get(block.entity, "")}

    if block.kind == "detail":
        record = _selected_record(block, entity, reader, selection, predicates)
        if record is None:
            return {"record": None, "fields": _field_specs(entity, block.fields),
                    "hint": "select a row to see it here"}
        return {"record": _project(record, block.fields),
                "record_id": str(record.get(entity.primary_key)),
                "version": record.get("_version"),
                "fields": _field_specs(entity, block.fields)}

    if block.kind == "relation":
        link = next((l for l in graph.links_from(block.entity) if l.name == block.link), None)
        if link is None:
            raise MorphError(f"relationship {block.link!r} is no longer available")
        related = graph.entity(link.target_entity)
        if related is None:
            raise MorphError(f"{link.target_entity} is no longer connected")
        left = reader.read(block.entity, predicates, block.limit)
        right = reader.read(link.target_entity, [], min(MAX_JOIN_RECORDS, related.max_page_size))
        index: dict[str, list[dict[str, Any]]] = {}
        for record in right.records:
            index.setdefault(str(record.get(link.target_field)), []).append(record)
        rows = []
        for record in left.records:
            matches = index.get(str(record.get(link.source_field)), [])
            if link.cardinality == "one":
                matches = matches[:1]
            rows.append({"row": _project(record, block.columns),
                         "related": [_project(m, block.related_columns) for m in matches[:5]],
                         "related_count": len(matches)})
        return {"columns": list(block.columns), "related_columns": list(block.related_columns),
                "related_label": related.label, "primary_key": entity.primary_key,
                "link": {"name": link.name, "from": f"{link.source_entity}.{link.source_field}",
                         "to": f"{link.target_entity}.{link.target_field}",
                         "cross_application": link.cross_application},
                "rows": rows, "total": left.total, "truncated": left.truncated,
                "row_actions": _operation_specs(entity, block.row_actions),
                "selected": selection.get(block.entity, "")}

    if block.kind == "form":
        operation = entity.operation(block.operation)
        if operation is None:
            raise MorphError(f"operation {block.operation!r} is no longer offered")
        record = _selected_record(block, entity, reader, selection, predicates)
        return {"operation": operation.public(),
                "params": [p.public() for p in operation.params if p.name in block.fields],
                "submit_label": block.submit_label,
                "target": _target(record, entity) if record else None,
                "hint": "" if record else "select a row to target this action"}

    if block.kind == "actions":
        record = _selected_record(block, entity, reader, selection, predicates)
        return {"operations": _operation_specs(entity, block.operations),
                "target": _target(record, entity) if record else None,
                "hint": "" if record else "select a row to target these actions"}

    raise MorphError(f"cannot resolve a {block.kind!r} block")


def _target(record: dict[str, Any], entity: Any) -> dict[str, Any]:
    return {"record_id": str(record.get(entity.primary_key)),
            "label": clean_text(record.get(entity.title_field)
                                or record.get(entity.primary_key), 70),
            "version": record.get("_version")}


def _selected_record(block: Block, entity: Any, reader: _Reader, selection: dict[str, str],
                     predicates: list[Predicate]) -> Optional[dict[str, Any]]:
    """The record a detail/form/actions block is about.

    The operator's selection wins.  With nothing selected the block shows the first
    record its own filters match, so a fresh workspace is never empty — but a write
    always re-reads the record by id before it is proposed, so this convenience can
    never decide what gets written.
    """
    chosen = selection.get(block.entity)
    if chosen:
        app, name = block.entity.split(".", 1)
        try:
            record = registry.get(app).get(name, chosen)
        except MorphError:
            record = None
        if record is not None:
            return record
    read = reader.read(block.entity, predicates, 1)
    return read.records[0] if read.records else None


def _operation_specs(entity: Any, ids: Iterable[str]) -> list[dict[str, Any]]:
    out = []
    for op_id in ids:
        operation = entity.operation(op_id)
        if operation is not None:
            out.append(operation.public())
    return out


def _field_specs(entity: Any, names: Iterable[str]) -> list[dict[str, Any]]:
    out = []
    for name in names:
        descriptor = entity.field(name)
        if descriptor is not None:
            out.append(descriptor.public())
    return out


def _key(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    return str(value)


def diff_signatures(before: dict[str, dict[str, int]],
                    after: dict[str, dict[str, int]]) -> list[dict[str, Any]]:
    """What changed in the source applications between two resolve passes."""
    changes = []
    for ref in sorted(set(before) | set(after)):
        old, new = before.get(ref) or {}, after.get(ref) or {}
        if not old or not new:
            continue
        added = sorted(set(new) - set(old))
        removed = sorted(set(old) - set(new))
        updated = sorted(rid for rid in set(old) & set(new) if old[rid] != new[rid])
        if added or removed or updated:
            changes.append({"entity": ref, "added": added[:20], "removed": removed[:20],
                            "updated": updated[:20],
                            "counts": {"added": len(added), "removed": len(removed),
                                       "updated": len(updated)}})
    return changes
