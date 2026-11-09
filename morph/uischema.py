"""The declarative UI schema: ``morph.ui.v1``.

A workspace is data, never code.  A plan names entities, fields, filters and
operations that must already exist in the capability graph; there is no field
anywhere in the schema that can carry markup, a URL or an expression, so there is
nothing for a renderer to execute and nothing for a model to smuggle through.

:func:`validate` is the only way a plan becomes a workspace.  It is strict and it
explains itself: every rejection is a short sentence naming the block and what was
wrong, which is what the planner's repair pass feeds back to the model.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any, Iterable, Optional

from morph.capability import CapabilityGraph
from morph.connectors import AGGREGATES
from morph.errors import SchemaError
from morph.util import clean_text, is_ref

SCHEMA_VERSION = "morph.ui.v1"
BLOCK_KINDS = ("metric", "table", "chart", "chart_series", "board", "timeline", "detail",
               "relation", "form", "actions", "workflow")
CHART_TYPES = ("bar", "line", "donut")
SPANS = ("sm", "md", "lg", "full")
TREND_HINTS = ("higher_is_better", "lower_is_better", "neutral")

MAX_SECTIONS = 6
MAX_BLOCKS = 14
MAX_COLUMNS = 8
MAX_FILTERS = 5
MAX_IN_VALUES = 20
MAX_TABLE_LIMIT = 100
MAX_CHART_GROUPS = 12
MAX_STEPS = 6
MAX_BOARD_COLUMNS = 8
MAX_CARD_FIELDS = 4
MAX_TIMELINE_ITEMS = 40
GROUPABLE_TYPES = ("enum", "bool", "string", "id", "int", "email")
BOARDABLE_TYPES = ("enum", "bool")


@dataclass
class Block:
    id: str
    kind: str
    title: str
    entity: str = ""
    description: str = ""
    span: str = "md"
    aggregate: str = ""
    field: str = ""
    unit: str = ""
    trend_hint: str = "neutral"
    emphasis: bool = False
    columns: tuple[str, ...] = ()
    filters: tuple[dict[str, Any], ...] = ()
    sort: Optional[dict[str, str]] = None
    limit: int = 0
    row_actions: tuple[str, ...] = ()
    operations: tuple[str, ...] = ()
    selectable: bool = False
    chart_type: str = ""
    group_by: str = ""
    link: str = ""
    related_columns: tuple[str, ...] = ()
    operation: str = ""
    target: str = "selection"
    submit_label: str = ""
    fields: tuple[str, ...] = ()
    steps: tuple[dict[str, str], ...] = ()
    time_field: str = ""
    label_field: str = ""
    status_field: str = ""
    searchable: bool = False
    sortable: bool = False

    def public(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "kind": self.kind, "title": self.title,
                               "span": self.span}
        for key in ("entity", "description", "aggregate", "field", "unit", "chart_type",
                    "group_by", "link", "operation", "submit_label", "time_field",
                    "label_field", "status_field"):
            value = getattr(self, key)
            if value:
                out[key] = value
        for key in ("columns", "filters", "row_actions", "operations", "related_columns",
                    "fields", "steps"):
            value = getattr(self, key)
            if value:
                out[key] = [dict(v) if isinstance(v, dict) else v for v in value]
        if self.sort:
            out["sort"] = dict(self.sort)
        if self.limit:
            out["limit"] = self.limit
        if self.selectable:
            out["selectable"] = True
        if self.searchable:
            out["searchable"] = True
        if self.sortable:
            out["sortable"] = True
        if self.emphasis:
            out["emphasis"] = True
        if self.kind == "metric":
            out["trend_hint"] = self.trend_hint
        if self.kind in ("detail", "form", "actions"):
            out["target"] = self.target
        return out


@dataclass
class Section:
    id: str
    title: str
    description: str
    blocks: list[Block] = dc_field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "description": self.description,
                "blocks": [b.public() for b in self.blocks]}


@dataclass
class UiSchema:
    title: str
    summary: str
    sections: list[Section] = dc_field(default_factory=list)
    primary_entity: str = ""
    version: str = SCHEMA_VERSION

    @property
    def blocks(self) -> list[Block]:
        return [b for s in self.sections for b in s.blocks]

    def block(self, block_id: str) -> Optional[Block]:
        return next((b for b in self.blocks if b.id == block_id), None)

    @property
    def entities_used(self) -> list[str]:
        return sorted({b.entity for b in self.blocks if b.entity})

    @property
    def sources_used(self) -> list[str]:
        return sorted({e.split(".", 1)[0] for e in self.entities_used})

    @property
    def operations_used(self) -> list[str]:
        refs: set[str] = set()
        for block in self.blocks:
            for op in (*block.row_actions, *block.operations):
                refs.add(f"{block.entity}.{op}")
            if block.operation:
                refs.add(f"{block.entity}.{block.operation}")
        return sorted(refs)

    def public(self) -> dict[str, Any]:
        return {"version": self.version, "title": self.title, "summary": self.summary,
                "primary_entity": self.primary_entity,
                "sections": [s.public() for s in self.sections],
                "entities_used": self.entities_used, "sources_used": self.sources_used,
                "operations_used": self.operations_used}


# ───────────────────────────── value coercion ──────────────────────────────────

def _coerce_value(field_type: str, value: Any) -> Any:
    if value is None:
        return None
    if field_type in ("int",):
        if isinstance(value, bool):
            raise ValueError("expected a whole number")
        return int(value)
    if field_type in ("float", "money"):
        if isinstance(value, bool):
            raise ValueError("expected a number")
        return float(value)
    if field_type == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        raise ValueError("expected true or false")
    if isinstance(value, (dict, list)):
        raise ValueError("expected a scalar value")
    return clean_text(value, 120)


class _Errors(list):
    def add(self, where: str, message: str) -> None:
        self.append(f"{where}: {message}")


# ───────────────────────────────── validation ──────────────────────────────────

def validate(plan: Any, graph: CapabilityGraph) -> UiSchema:
    """Turn an untrusted plan into a workspace, or raise :class:`SchemaError`.

    The raised error's ``detail`` is the full list of problems, so one round trip
    tells the planner everything it has to fix.
    """
    errors = _Errors()
    if not isinstance(plan, dict):
        raise SchemaError("a workspace plan must be a JSON object")
    if not graph.entities:
        raise SchemaError("no connected application is reachable, so no workspace can be composed")

    title = clean_text(plan.get("title") or "Workspace", 80)
    summary = clean_text(plan.get("summary"), 220)
    sections_raw = plan.get("sections")
    if not isinstance(sections_raw, list) or not sections_raw:
        raise SchemaError("a workspace plan needs at least one section", detail=["sections: missing"])
    if len(sections_raw) > MAX_SECTIONS:
        errors.add("sections", f"at most {MAX_SECTIONS} sections are allowed")
        sections_raw = sections_raw[:MAX_SECTIONS]

    schema = UiSchema(title=title, summary=summary)
    counter = 0
    for s_index, raw_section in enumerate(sections_raw):
        where = f"sections[{s_index}]"
        if not isinstance(raw_section, dict):
            errors.add(where, "must be an object")
            continue
        blocks_raw = raw_section.get("blocks")
        if not isinstance(blocks_raw, list) or not blocks_raw:
            errors.add(where, "needs a non-empty blocks list")
            continue
        section = Section(id=f"s{s_index + 1}",
                          title=clean_text(raw_section.get("title") or f"Section {s_index + 1}", 70),
                          description=clean_text(raw_section.get("description"), 180))
        for b_index, raw_block in enumerate(blocks_raw):
            if counter >= MAX_BLOCKS:
                errors.add("blocks", f"at most {MAX_BLOCKS} blocks are allowed in a workspace")
                break
            counter += 1
            block = _validate_block(raw_block, graph, errors, f"{where}.blocks[{b_index}]",
                                    block_id=f"b{counter}")
            if block is not None:
                section.blocks.append(block)
        if section.blocks:
            schema.sections.append(section)

    # Workflow steps may only point at blocks that survived validation.
    known = {b.id for b in schema.blocks}
    for block in schema.blocks:
        if block.kind != "workflow":
            continue
        block.steps = tuple(
            {**step, "block": step["block"]} if step.get("block") in known
            else {k: v for k, v in step.items() if k != "block"}
            for step in block.steps)

    if not schema.sections:
        raise SchemaError("no block in the plan survived validation",
                          detail=list(errors) or ["blocks: all invalid"])
    if errors:
        raise SchemaError(f"{len(errors)} problem(s) in the workspace plan", detail=list(errors))

    primary = clean_text(plan.get("primary_entity"), 80)
    if primary not in schema.entities_used:
        counts: dict[str, int] = {}
        for block in schema.blocks:
            if block.entity:
                counts[block.entity] = counts.get(block.entity, 0) + 1
        primary = max(counts, key=lambda k: counts[k]) if counts else ""
    schema.primary_entity = primary
    return schema


def _validate_filters(raw: Any, entity: Any, errors: _Errors, where: str) -> tuple[dict[str, Any], ...]:
    if raw in (None, []):
        return ()
    if not isinstance(raw, list):
        errors.add(where, "filters must be a list")
        return ()
    out: list[dict[str, Any]] = []
    for index, item in enumerate(raw[:MAX_FILTERS]):
        spot = f"{where}.filters[{index}]"
        if not isinstance(item, dict):
            errors.add(spot, "must be an object")
            continue
        name = str(item.get("field") or "")
        descriptor = entity.field(name)
        if descriptor is None:
            errors.add(spot, f"{name!r} is not a field of {entity.name!r}")
            continue
        op = str(item.get("op") or "eq")
        if op not in entity.filter_ops:
            errors.add(spot, f"{entity.name!r} does not support the {op!r} filter")
            continue
        value = item.get("value")
        try:
            if op == "exists":
                value = bool(value)
            elif op in ("in", "nin"):
                if not isinstance(value, list) or not value:
                    errors.add(spot, f"the {op!r} filter needs a non-empty list of values")
                    continue
                value = [_coerce_value(descriptor.type, v) for v in value[:MAX_IN_VALUES]]
            else:
                if value is None:
                    errors.add(spot, f"the {op!r} filter needs a value")
                    continue
                value = _coerce_value(descriptor.type, value)
        except (TypeError, ValueError) as exc:
            errors.add(spot, f"value for {name!r} ({descriptor.type}) is wrong: {exc}")
            continue
        if descriptor.values and op in ("eq", "neq"):
            if value not in descriptor.values:
                errors.add(spot, f"{value!r} is not one of {name!r}'s values: "
                                 f"{', '.join(descriptor.values)}")
                continue
        if descriptor.values and op in ("in", "nin"):
            bad = [v for v in value if v not in descriptor.values]
            if bad:
                errors.add(spot, f"{', '.join(map(str, bad))} are not values of {name!r}")
                continue
        if descriptor.numeric and op == "contains":
            errors.add(spot, f"{name!r} is numeric, so 'contains' does not apply")
            continue
        out.append({"field": name, "op": op, "value": value})
    return tuple(out)


def _columns(raw: Any, entity: Any, errors: _Errors, where: str, key: str = "columns",
             cap: int = MAX_COLUMNS) -> tuple[str, ...]:
    names = [f.name for f in entity.fields]
    if raw in (None, []):
        preferred = [entity.primary_key, entity.title_field]
        preferred += [f.name for f in entity.fields if f.role in ("status", "metric", "amount")]
        preferred += names
        chosen: list[str] = []
        for name in preferred:
            if name in names and name not in chosen:
                chosen.append(name)
            if len(chosen) >= min(5, cap):
                break
        return tuple(chosen)
    if not isinstance(raw, list):
        errors.add(where, f"{key} must be a list of field names")
        return ()
    out: list[str] = []
    for item in raw[:cap]:
        name = str(item)
        if name not in names:
            errors.add(where, f"{name!r} is not a field of {entity.name!r}")
            continue
        if name not in out:
            out.append(name)
    if not out:
        errors.add(where, f"{key} named no usable field of {entity.name!r}")
    return tuple(out)


def _validate_block(raw: Any, graph: CapabilityGraph, errors: _Errors, where: str,
                    *, block_id: str) -> Optional[Block]:
    if not isinstance(raw, dict):
        errors.add(where, "must be an object")
        return None
    kind = str(raw.get("kind") or "")
    if kind not in BLOCK_KINDS:
        errors.add(where, f"{kind!r} is not a block kind; use one of: {', '.join(BLOCK_KINDS)}")
        return None
    if kind == "chart_series":  # alias accepted from planners, normalised to a line chart
        kind, raw = "chart", {**raw, "chart_type": raw.get("chart_type") or "line"}

    span = str(raw.get("span") or "")
    block = Block(id=block_id, kind=kind,
                  title=clean_text(raw.get("title") or kind.title(), 70),
                  description=clean_text(raw.get("description"), 180),
                  span=span if span in SPANS else ("full" if kind in ("table", "relation") else "md"))

    if kind == "workflow":
        steps_raw = raw.get("steps")
        if not isinstance(steps_raw, list) or not steps_raw:
            errors.add(where, "a workflow block needs a steps list")
            return None
        steps = []
        for index, step in enumerate(steps_raw[:MAX_STEPS]):
            if not isinstance(step, dict):
                errors.add(f"{where}.steps[{index}]", "must be an object")
                continue
            entry = {"title": clean_text(step.get("title") or f"Step {index + 1}", 70),
                     "detail": clean_text(step.get("detail"), 200)}
            target = str(step.get("block") or "")
            if target:
                entry["block"] = target
            steps.append(entry)
        if not steps:
            errors.add(where, "no usable workflow step")
            return None
        block.steps = tuple(steps)
        return block

    entity_ref = str(raw.get("entity") or "")
    if not is_ref(entity_ref, 2):
        errors.add(where, f"entity must be '<application>.<entity>', got {entity_ref!r}")
        return None
    entity = graph.entity(entity_ref)
    if entity is None:
        errors.add(where, f"{entity_ref!r} is not a connected entity; available: "
                          f"{', '.join(sorted(graph.entities))}")
        return None
    block.entity = entity_ref
    block.filters = _validate_filters(raw.get("filters"), entity, errors, where)

    if kind == "metric":
        aggregate = str(raw.get("aggregate") or "count")
        if aggregate not in AGGREGATES:
            errors.add(where, f"{aggregate!r} is not an aggregate; use one of: {', '.join(AGGREGATES)}")
            return None
        block.aggregate = aggregate
        if aggregate != "count":
            name = str(raw.get("field") or "")
            descriptor = entity.field(name)
            if descriptor is None:
                errors.add(where, f"{aggregate!r} needs a field of {entity.name!r}, got {name!r}")
                return None
            if not descriptor.numeric:
                errors.add(where, f"{name!r} is {descriptor.type}, so {aggregate!r} does not apply")
                return None
            block.field, block.unit = name, descriptor.unit
        hint = str(raw.get("trend_hint") or "neutral")
        block.trend_hint = hint if hint in TREND_HINTS else "neutral"
        block.emphasis = bool(raw.get("emphasis"))
        block.span = span if span in SPANS else "sm"
        return block

    if kind == "table":
        block.columns = _columns(raw.get("columns"), entity, errors, where)
        if not block.columns:
            return None
        block.limit = max(1, min(int(raw.get("limit") or 25), MAX_TABLE_LIMIT))
        block.selectable = bool(raw.get("selectable", True))
        # Search and sort are client-side over the rows the block already resolved.
        # They are on by default because a queue you cannot sort is not a queue.
        block.searchable = bool(raw.get("searchable", True))
        block.sortable = bool(raw.get("sortable", True))
        block.row_actions = _operations(raw.get("row_actions"), entity, errors, where, "row_actions")
        sort_raw = raw.get("sort")
        if isinstance(sort_raw, dict):
            name = str(sort_raw.get("field") or "")
            if entity.field(name) is None:
                errors.add(where, f"sort field {name!r} is not a field of {entity.name!r}")
            else:
                direction = "desc" if str(sort_raw.get("direction")).lower() == "desc" else "asc"
                block.sort = {"field": name, "direction": direction}
        return block

    if kind == "chart":
        chart_type = str(raw.get("chart_type") or "bar")
        if chart_type not in CHART_TYPES:
            errors.add(where, f"{chart_type!r} is not a chart type; use one of: {', '.join(CHART_TYPES)}")
            return None
        group_by = str(raw.get("group_by") or "")
        descriptor = entity.field(group_by)
        if descriptor is None:
            errors.add(where, f"group_by {group_by!r} is not a field of {entity.name!r}")
            return None
        if descriptor.type not in GROUPABLE_TYPES:
            errors.add(where, f"{group_by!r} is {descriptor.type}, which cannot be grouped")
            return None
        aggregate = str(raw.get("aggregate") or "count")
        if aggregate not in AGGREGATES:
            errors.add(where, f"{aggregate!r} is not an aggregate")
            return None
        if aggregate != "count":
            name = str(raw.get("field") or "")
            value_field = entity.field(name)
            if value_field is None or not value_field.numeric:
                errors.add(where, f"{aggregate!r} needs a numeric field of {entity.name!r}")
                return None
            block.field, block.unit = name, value_field.unit
        block.chart_type, block.group_by, block.aggregate = chart_type, group_by, aggregate
        block.limit = max(2, min(int(raw.get("limit") or MAX_CHART_GROUPS), MAX_CHART_GROUPS))
        block.span = span if span in SPANS else "lg"
        return block

    if kind == "board":
        group_by = str(raw.get("group_by") or "")
        descriptor = entity.field(group_by)
        if descriptor is None:
            errors.add(where, f"group_by {group_by!r} is not a field of {entity.name!r}")
            return None
        if descriptor.type not in BOARDABLE_TYPES:
            errors.add(where, f"a board groups an enum or bool field; {group_by!r} is "
                              f"{descriptor.type}")
            return None
        if descriptor.type == "enum" and len(descriptor.values) > MAX_BOARD_COLUMNS:
            errors.add(where, f"{group_by!r} has {len(descriptor.values)} values; a board takes at "
                              f"most {MAX_BOARD_COLUMNS}")
            return None
        block.group_by = group_by
        block.columns = _columns(raw.get("columns"), entity, errors, where, cap=MAX_CARD_FIELDS)
        if not block.columns:
            return None
        block.limit = max(1, min(int(raw.get("limit") or 40), MAX_TABLE_LIMIT))
        block.selectable = bool(raw.get("selectable", True))
        block.row_actions = _operations(raw.get("row_actions"), entity, errors, where, "row_actions")
        block.span = "full"
        return block

    if kind == "timeline":
        name = str(raw.get("time_field") or "")
        descriptor = entity.field(name)
        if descriptor is None:
            errors.add(where, f"time_field {name!r} is not a field of {entity.name!r}")
            return None
        if descriptor.type != "datetime":
            errors.add(where, f"a timeline needs a datetime field; {name!r} is {descriptor.type}")
            return None
        block.time_field = name
        label = str(raw.get("label_field") or entity.title_field)
        if entity.field(label) is None:
            label = entity.title_field
        block.label_field = label
        status = str(raw.get("status_field") or "")
        if status and entity.field(status) is None:
            errors.add(where, f"status_field {status!r} is not a field of {entity.name!r}")
            return None
        if not status:
            candidate = next((f.name for f in entity.fields
                              if f.role == "status" or f.type == "enum"), "")
            status = candidate
        block.status_field = status
        block.limit = max(1, min(int(raw.get("limit") or 20), MAX_TIMELINE_ITEMS))
        block.selectable = bool(raw.get("selectable", True))
        block.row_actions = _operations(raw.get("row_actions"), entity, errors, where, "row_actions")
        block.span = span if span in SPANS else "lg"
        return block

    if kind == "detail":
        block.fields = _columns(raw.get("fields"), entity, errors, where, key="fields", cap=12)
        if not block.fields:
            return None
        block.target = "selection"
        block.span = span if span in SPANS else "lg"
        return block

    if kind == "relation":
        link = next((l for l in graph.links_from(entity_ref) if l.name == str(raw.get("link") or "")),
                    None)
        if link is None:
            available = [l.name for l in graph.links_from(entity_ref)]
            errors.add(where, f"{str(raw.get('link'))!r} is not a relationship from {entity_ref!r}"
                              + (f"; available: {', '.join(available)}" if available
                                 else "; this entity has no relationships"))
            return None
        related = graph.entity(link.target_entity)
        if related is None:
            errors.add(where, f"relationship {link.name!r} targets an unreachable entity")
            return None
        block.link = link.name
        block.columns = _columns(raw.get("columns"), entity, errors, where)
        block.related_columns = _columns(raw.get("related_columns"), related, errors, where,
                                         key="related_columns", cap=5)
        if not block.columns or not block.related_columns:
            return None
        block.limit = max(1, min(int(raw.get("limit") or 20), MAX_TABLE_LIMIT))
        block.searchable = bool(raw.get("searchable", True))
        block.row_actions = _operations(raw.get("row_actions"), entity, errors, where, "row_actions")
        block.span = "full"
        return block

    if kind == "form":
        op_id = str(raw.get("operation") or "")
        operation = entity.operation(op_id)
        if operation is None:
            available = [o.id for o in entity.operations]
            errors.add(where, f"{op_id!r} is not an operation of {entity_ref!r}"
                              + (f"; available: {', '.join(available)}" if available
                                 else "; this entity exposes none"))
            return None
        names = [p.name for p in operation.params]
        chosen = raw.get("fields")
        if isinstance(chosen, list) and chosen:
            picked = [str(f) for f in chosen if str(f) in names]
            missing = [p.name for p in operation.params if p.required and p.name not in picked]
            if missing:
                errors.add(where, f"form omits required parameter(s): {', '.join(missing)}")
                return None
        else:
            picked = names
        block.operation, block.fields = op_id, tuple(picked)
        block.target = "selection"
        block.submit_label = clean_text(raw.get("submit_label") or operation.label, 40)
        block.span = span if span in SPANS else "md"
        return block

    if kind == "actions":
        block.operations = _operations(raw.get("operations"), entity, errors, where, "operations")
        if not block.operations:
            errors.add(where, f"an actions block for {entity_ref!r} named no valid operation")
            return None
        block.target = "selection"
        block.span = span if span in SPANS else "md"
        return block

    errors.add(where, f"{kind!r} is not handled")
    return None


def _operations(raw: Any, entity: Any, errors: _Errors, where: str, key: str) -> tuple[str, ...]:
    if raw in (None, []):
        return ()
    if not isinstance(raw, list):
        errors.add(where, f"{key} must be a list of operation ids")
        return ()
    known = {o.id for o in entity.operations}
    out: list[str] = []
    for item in raw[:8]:
        op_id = str(item)
        if op_id not in known:
            errors.add(where, f"{op_id!r} is not an operation of {entity.name!r}"
                              + (f"; available: {', '.join(sorted(known))}" if known
                                 else "; this entity exposes none"))
            continue
        if op_id not in out:
            out.append(op_id)
    return tuple(out)


# ───────────────── JSON schema handed to the planning model ────────────────────

def planner_json_schema() -> dict[str, Any]:
    """Structured-output schema for the planner.

    Intentionally permissive about per-kind requirements — providers differ in how
    well they handle discriminated unions — because :func:`validate` is the real
    gate and its errors drive the repair pass.
    """
    scalar = {"type": ["string", "number", "boolean", "null"]}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["title", "summary", "sections"],
        "properties": {
            "title": {"type": "string", "maxLength": 80},
            "summary": {"type": "string", "maxLength": 220},
            "primary_entity": {"type": "string", "maxLength": 80},
            "sections": {
                "type": "array", "minItems": 1, "maxItems": MAX_SECTIONS,
                "items": {
                    "type": "object", "additionalProperties": False, "required": ["title", "blocks"],
                    "properties": {
                        "title": {"type": "string", "maxLength": 70},
                        "description": {"type": "string", "maxLength": 180},
                        "blocks": {
                            "type": "array", "minItems": 1, "maxItems": MAX_BLOCKS,
                            "items": {
                                "type": "object", "additionalProperties": False,
                                "required": ["kind", "title"],
                                "properties": {
                                    "kind": {"type": "string", "enum": list(BLOCK_KINDS)},
                                    "title": {"type": "string", "maxLength": 70},
                                    "description": {"type": "string", "maxLength": 180},
                                    "entity": {"type": "string", "maxLength": 80},
                                    "span": {"type": "string", "enum": list(SPANS)},
                                    "aggregate": {"type": "string", "enum": list(AGGREGATES)},
                                    "field": {"type": "string", "maxLength": 60},
                                    "trend_hint": {"type": "string", "enum": list(TREND_HINTS)},
                                    "emphasis": {"type": "boolean"},
                                    "columns": {"type": "array", "maxItems": MAX_COLUMNS,
                                                "items": {"type": "string", "maxLength": 60}},
                                    "related_columns": {"type": "array", "maxItems": 5,
                                                        "items": {"type": "string", "maxLength": 60}},
                                    "fields": {"type": "array", "maxItems": 12,
                                               "items": {"type": "string", "maxLength": 60}},
                                    "filters": {
                                        "type": "array", "maxItems": MAX_FILTERS,
                                        "items": {"type": "object", "additionalProperties": False,
                                                  "required": ["field", "op"],
                                                  "properties": {
                                                      "field": {"type": "string", "maxLength": 60},
                                                      "op": {"type": "string",
                                                             "enum": ["eq", "neq", "in", "nin", "gt",
                                                                      "gte", "lt", "lte", "contains",
                                                                      "exists"]},
                                                      "value": {"anyOf": [scalar,
                                                                          {"type": "array",
                                                                           "maxItems": MAX_IN_VALUES,
                                                                           "items": scalar}]}}}},
                                    "sort": {"type": "object", "additionalProperties": False,
                                             "required": ["field"],
                                             "properties": {"field": {"type": "string", "maxLength": 60},
                                                            "direction": {"type": "string",
                                                                          "enum": ["asc", "desc"]}}},
                                    "limit": {"type": "integer", "minimum": 1, "maximum": MAX_TABLE_LIMIT},
                                    "selectable": {"type": "boolean"},
                                    "searchable": {"type": "boolean"},
                                    "sortable": {"type": "boolean"},
                                    "time_field": {"type": "string", "maxLength": 60},
                                    "label_field": {"type": "string", "maxLength": 60},
                                    "status_field": {"type": "string", "maxLength": 60},
                                    "row_actions": {"type": "array", "maxItems": 8,
                                                    "items": {"type": "string", "maxLength": 60}},
                                    "operations": {"type": "array", "maxItems": 8,
                                                   "items": {"type": "string", "maxLength": 60}},
                                    "chart_type": {"type": "string", "enum": list(CHART_TYPES)},
                                    "group_by": {"type": "string", "maxLength": 60},
                                    "link": {"type": "string", "maxLength": 60},
                                    "operation": {"type": "string", "maxLength": 60},
                                    "submit_label": {"type": "string", "maxLength": 40},
                                    "steps": {
                                        "type": "array", "maxItems": MAX_STEPS,
                                        "items": {"type": "object", "additionalProperties": False,
                                                  "required": ["title"],
                                                  "properties": {
                                                      "title": {"type": "string", "maxLength": 70},
                                                      "detail": {"type": "string", "maxLength": 200},
                                                      "block": {"type": "string", "maxLength": 10}}}},
                                },
                            },
                        },
                    },
                },
            },
        },
    }
