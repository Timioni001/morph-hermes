"""The capability graph: what the connected applications can collectively do.

The graph is the single input the planner reasons over and the single authority the
UI-schema validator checks against.  It contains **schema only** — entity and field
names, types, enum domains, declared operations and the relationships between
entities.  No record values ever enter it, which is what lets MORPH hand it to a
language model without handing over customer data, and what makes "the model may
not invent a field" a structural guarantee rather than a prompt instruction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from morph.config import LinkConfig
from morph.connectors import EntityDescriptor, FieldDescriptor, OperationDescriptor, SourceDescriptor
from morph.util import clean_text


@dataclass(frozen=True)
class Link:
    """A traversable relationship between two entities, possibly in different apps."""

    name: str
    source_entity: str   # "<app>.<entity>"
    source_field: str
    target_entity: str   # "<app>.<entity>"
    target_field: str
    cardinality: str
    cross_application: bool

    def public(self) -> dict[str, Any]:
        return {"name": self.name, "from": f"{self.source_entity}.{self.source_field}",
                "to": f"{self.target_entity}.{self.target_field}",
                "cardinality": self.cardinality, "cross_application": self.cross_application}


@dataclass
class CapabilityGraph:
    sources: tuple[SourceDescriptor, ...] = ()
    entities: dict[str, EntityDescriptor] = field(default_factory=dict)
    links: tuple[Link, ...] = ()
    unavailable: tuple[dict[str, str], ...] = ()
    warnings: tuple[str, ...] = ()

    # — lookups —
    def entity(self, ref: str) -> Optional[EntityDescriptor]:
        return self.entities.get(ref)

    def field_of(self, ref: str, name: str) -> Optional[FieldDescriptor]:
        entity = self.entities.get(ref)
        return entity.field(name) if entity else None

    def operation(self, ref: str, op_id: str) -> Optional[OperationDescriptor]:
        entity = self.entities.get(ref)
        return entity.operation(op_id) if entity else None

    def links_from(self, ref: str) -> list[Link]:
        return [l for l in self.links if l.source_entity == ref]

    def links_touching(self, ref: str) -> list[Link]:
        return [l for l in self.links if ref in (l.source_entity, l.target_entity)]

    @property
    def writable_operations(self) -> list[tuple[str, OperationDescriptor]]:
        return [(f"{ref}.{op.id}", op) for ref, entity in sorted(self.entities.items())
                for op in entity.operations]

    def source_of(self, ref: str) -> str:
        return ref.split(".", 1)[0]

    # — serialisation —
    def public(self) -> dict[str, Any]:
        """Everything the dashboard needs to explain what MORPH is connected to."""
        return {
            "sources": [s.public() for s in self.sources],
            "links": [l.public() for l in self.links],
            "unavailable": [dict(u) for u in self.unavailable],
            "warnings": list(self.warnings),
            "entity_count": len(self.entities),
            "operation_count": len(self.writable_operations),
        }

    def compact(self) -> dict[str, Any]:
        """The schema-only view handed to the planner.

        Deliberately lossy: labels and enum domains are kept because they carry the
        vocabulary a goal is phrased in; descriptions are truncated; nothing here is
        a record value.
        """
        entities = []
        for ref, entity in sorted(self.entities.items()):
            entities.append({
                "ref": ref,
                "label": entity.label,
                "about": entity.description,
                "key": entity.primary_key,
                "title_field": entity.title_field,
                "fields": [
                    {k: v for k, v in (
                        ("name", f.name), ("type", f.type), ("label", f.label),
                        ("role", f.role), ("unit", f.unit),
                        ("values", list(f.values) if f.values else None),
                    ) if v} for f in entity.fields
                ],
                "filter_ops": list(entity.filter_ops),
                "operations": [
                    {"id": op.id, "label": op.label, "about": op.description, "risk": op.risk,
                     "params": [{"name": p.name, "type": p.type, "required": p.required,
                                 **({"values": list(p.values)} if p.values else {})}
                                for p in op.params]}
                    for op in entity.operations
                ],
            })
        return {
            "applications": [{"id": s.id, "label": s.label, "vendor": s.vendor,
                              "about": s.description, "kind": s.kind} for s in self.sources],
            "entities": entities,
            "links": [l.public() for l in self.links],
        }

    def summary(self) -> dict[str, Any]:
        return {
            "applications": [{"id": s.id, "label": s.label, "entities": [e.name for e in s.entities]}
                             for s in self.sources],
            "entities": sorted(self.entities),
            "operations": [ref for ref, _ in self.writable_operations],
            "cross_application_links": [l.public() for l in self.links if l.cross_application],
            "unavailable": [dict(u) for u in self.unavailable],
        }


def build(descriptors: Iterable[SourceDescriptor], links: Iterable[LinkConfig], *,
          unavailable: Iterable[dict[str, str]] = ()) -> CapabilityGraph:
    sources = tuple(descriptors)
    entities: dict[str, EntityDescriptor] = {}
    for source in sources:
        for entity in source.entities:
            entities[f"{source.id}.{entity.name}"] = entity

    resolved: list[Link] = []
    warnings: list[str] = []

    # Relationships an application declares about its own entities.
    for source in sources:
        for entity in source.entities:
            for rel in entity.relationships:
                target_ref = f"{source.id}.{rel.get('target_entity')}"
                target = entities.get(target_ref)
                if target is None or target.field(str(rel.get("target_field"))) is None:
                    warnings.append(
                        f"{source.id}.{entity.name}: relationship {rel.get('name')!r} points at "
                        f"{rel.get('target_entity')}.{rel.get('target_field')}, which is not published")
                    continue
                resolved.append(Link(
                    name=clean_text(rel.get("name") or "related", 40),
                    source_entity=f"{source.id}.{entity.name}", source_field=str(rel.get("local_field")),
                    target_entity=target_ref, target_field=str(rel.get("target_field")),
                    cardinality=str(rel.get("cardinality") or "many"), cross_application=False))

    # Relationships the operator declares between applications.
    for link in links:
        src_app, src_entity, src_field = link.source.split(".")
        tgt_app, tgt_entity, tgt_field = link.target.split(".")
        src_ref, tgt_ref = f"{src_app}.{src_entity}", f"{tgt_app}.{tgt_entity}"
        if src_ref not in entities or tgt_ref not in entities:
            warnings.append(f"link {link.name!r} references an entity no reachable application "
                            f"publishes ({link.source} → {link.target})")
            continue
        if entities[src_ref].field(src_field) is None or entities[tgt_ref].field(tgt_field) is None:
            warnings.append(f"link {link.name!r} references a field that is not published "
                            f"({link.source} → {link.target})")
            continue
        resolved.append(Link(name=link.name, source_entity=src_ref, source_field=src_field,
                             target_entity=tgt_ref, target_field=tgt_field,
                             cardinality=link.cardinality, cross_application=src_app != tgt_app))
        # Cross-application links are traversable in both directions.
        if src_app != tgt_app:
            resolved.append(Link(name=f"{link.name}_reverse", source_entity=tgt_ref,
                                 source_field=tgt_field, target_entity=src_ref,
                                 target_field=src_field,
                                 cardinality="many" if link.cardinality == "one" else "one",
                                 cross_application=True))

    return CapabilityGraph(sources=sources, entities=entities, links=tuple(resolved),
                           unavailable=tuple(unavailable), warnings=tuple(warnings))
