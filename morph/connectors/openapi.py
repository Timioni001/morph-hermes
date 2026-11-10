"""Connect any service that publishes an OpenAPI document.

The other adapters need the service to meet MORPH halfway: ``http-rest`` wants a
``morph.source.v1`` descriptor, and ``github`` is written against one vendor's
API by hand. This one needs nothing from the service at all. Point it at an
OpenAPI 3 document and it derives the entities, their fields and a set of
operations from the schema the service already publishes — so a service whose
authors have never heard of MORPH can be driven by it.

What it looks for is the REST shape almost every API already has:

    GET    /things            a collection
    GET    /things/{id}       one of them
    PATCH  /things/{id}       (or PUT) change one of them

Entities come from the first, records from the second, and everything writable
from the third's request body.

**Operations are derived, not invented.** MORPH's contract is that an operation
states what it will change so the change can be verified by re-reading the
record afterwards, and an operation whose effect cannot be predicted is worse
than no operation at all. So only fields with a closed set of outcomes become
operations on their own:

* an ``enum`` field becomes one operation per value — *Mark as shipped*,
  *Mark as cancelled* — each with an exact effect and a precondition that it is
  not already in that state;
* a ``boolean`` field becomes a matched pair, set and clear;
* every other writable field becomes one operation that takes the new value as a
  parameter, so the effect is still known before it is applied.

A field the document marks ``readOnly`` never becomes an operation, and neither
does an identifier or a version field. The result is a vocabulary a person can
read, rather than a single "update this record" verb that could do anything.
"""
from __future__ import annotations

import re
import time
from typing import Any, Optional, Sequence

from morph.connectors.base import (
    MAX_ENTITIES, MAX_ENUM_VALUES, MAX_FIELDS, MAX_OPERATIONS, ApplyResult,
    EntityDescriptor, FetchResult, FieldDescriptor, OperationDescriptor, Predicate,
    SourceDescriptor,
)
from morph.connectors.base import record_segment
from morph.connectors.http_rest import HttpRestConnector, _local_match
from morph.errors import ConnectorError, NotFound, PermissionDenied, StaleRecord
from morph.util import clean_text

NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
#: `/things/{id}` with exactly one template segment, at the end.
ITEM_PATH = re.compile(r"^/(?P<collection>[A-Za-z0-9_\-/]+)/\{(?P<param>[A-Za-z0-9_]+)\}$")

#: Fields that identify or version a record are never operation targets.
NEVER_WRITABLE = frozenset({"id", "uuid", "version", "etag", "created_at", "updated_at",
                            "created", "updated", "_id"})

_TYPES = {
    ("string", ""): "string",
    ("string", "date-time"): "datetime",
    ("string", "date"): "datetime",
    ("string", "email"): "email",
    ("string", "uuid"): "id",
    ("integer", ""): "int",
    ("number", ""): "float",
    ("boolean", ""): "bool",
}


def _slug(text: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "_", str(text).strip().lower()).strip("_")
    return out[:48]


def _title(text: str) -> str:
    return re.sub(r"[_\-]+", " ", str(text)).strip().title() or str(text)


class OpenApiConnector(HttpRestConnector):
    """A read-and-verify adapter over any OpenAPI 3 service."""

    kind = "openapi"

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        options = config.options or {}
        self._spec_path = str(options.get("spec_path") or "/openapi.json")
        self._only = tuple(str(x) for x in (options.get("entities") or ()))
        self._skip = {str(x) for x in (options.get("exclude") or ())}
        self._max_records = int(options.get("max_records") or 200)
        # entity name → (listing path, item path, write method, id parameter)
        self._routes: dict[str, tuple[str, str, str, str]] = {}
        # entity name → query parameters the listing declares a default for
        self._list_params: dict[str, dict[str, Any]] = {}

    # ─────────────────────────────── schema ───────────────────────────────

    def describe(self, *, refresh: bool = False) -> SourceDescriptor:
        if self._schema is not None and not refresh:
            return self._schema
        status, document = self._request("GET", self._spec_path)
        if status != 200 or not isinstance(document, dict):
            raise ConnectorError(
                f"{self.id!r} did not serve an OpenAPI document at {self._spec_path} "
                f"(HTTP {status})")
        if not str(document.get("openapi") or "").startswith("3"):
            raise ConnectorError(
                f"{self.id!r} serves OpenAPI {document.get('openapi') or 'of unknown version'}; "
                "this adapter reads version 3")
        described = self._derive(document)
        self._schema = described
        self._schema_at = time.time()
        return described

    def _derive(self, document: dict[str, Any]) -> SourceDescriptor:
        paths = document.get("paths")
        if not isinstance(paths, dict):
            raise ConnectorError(f"{self.id!r}'s OpenAPI document declares no paths")

        entities: list[EntityDescriptor] = []
        self._routes = {}
        self._list_params = {}
        for item_path, item_ops in sorted(paths.items()):
            match = ITEM_PATH.match(str(item_path))
            if not match or not isinstance(item_ops, dict) or "get" not in item_ops:
                continue      # a record you cannot read on its own cannot be verified
            collection = "/" + match.group("collection")
            found = self._find_listing(document, paths, collection, item_ops.get("get"))
            if found is None:
                continue      # records you cannot list: no table, so no entity
            list_path, listing = found

            name = _slug(match.group("collection").rsplit("/", 1)[-1])
            if not NAME.match(name) or name in self._skip:
                continue
            if self._only and name not in self._only:
                continue

            entity = self._entity(document, name, listing, item_ops, collection)
            if entity is None:
                continue
            write_method = "patch" if "patch" in item_ops else ("put" if "put" in item_ops else "")
            self._routes[name] = (list_path, str(item_path), write_method,
                                  match.group("param"))
            self._list_params[name] = self._declared_defaults(listing.get("get"))
            entities.append(entity)
            if len(entities) >= MAX_ENTITIES:
                break

        if not entities:
            raise ConnectorError(
                f"{self.id!r} publishes an OpenAPI document, but none of its paths look like a "
                "collection with readable records (GET /things and GET /things/{id})")

        info = document.get("info") if isinstance(document.get("info"), dict) else {}
        return SourceDescriptor(
            id=self.id, kind=self.kind,
            label=clean_text(self.config.label or info.get("title") or self.id, 60),
            vendor=clean_text(info.get("title") or "", 60),
            description=clean_text(info.get("description") or
                                   "Derived from the service's own OpenAPI document.", 400),
            entities=tuple(entities), fetched_at=time.time())

    def _entity(self, document: dict[str, Any], name: str, listing: dict[str, Any],
                item_ops: dict[str, Any], collection: str) -> Optional[EntityDescriptor]:
        shape = self._response_schema(document, item_ops.get("get"))
        if not shape:
            shape = self._item_of(document, self._response_schema(document, listing.get("get")))
        properties = (shape or {}).get("properties")
        if not isinstance(properties, dict) or not properties:
            return None

        writable_names = self._writable_names(document, item_ops)
        required = set(shape.get("required") or []) if isinstance(shape, dict) else set()

        fields: list[FieldDescriptor] = []
        for prop_name, raw in list(properties.items())[:MAX_FIELDS]:
            field = self._field(document, prop_name, raw, writable_names, required)
            if field is not None:
                fields.append(field)
        if not fields:
            return None

        primary = self._primary_key(fields)
        operations = self._operations(fields, primary)
        return EntityDescriptor(
            name=name, label=_title(name),
            description=clean_text((item_ops.get("get") or {}).get("summary") or "", 300),
            primary_key=primary, title_field=self._title_field(fields, primary),
            fields=tuple(fields), operations=tuple(operations),
            writable=bool(operations),
            # A generic collection endpoint advertises no filter vocabulary, so
            # every predicate is evaluated here rather than pushed down.
            filter_ops=(), sortable=True, paginated=False,
            max_page_size=self._max_records)

    def _find_listing(self, document: dict[str, Any], paths: dict[str, Any],
                      collection: str, item_get: Any) -> Optional[tuple[str, dict[str, Any]]]:
        """The GET that lists these records, matched by schema rather than by path.

        ``GET /things`` is the convention, but plenty of real APIs put the listing
        somewhere else — the Swagger Petstore lists pets at ``/pet/findByStatus``
        and has no ``GET /pet`` at all. Insisting on the convention would refuse
        half the APIs in the world over a naming choice.

        So: any GET under the collection whose 200 response is an array of the
        same schema the single-record GET returns. Identity comes from the
        document's own ``$ref``, so this cannot be fooled into pairing one
        collection's listing with another's records. A candidate whose required
        parameters have no defaults is skipped — MORPH would have nothing to send.
        """
        item_ref = self._response_ref(item_get)
        candidates = [collection] + sorted(
            key for key in paths
            if isinstance(key, str) and key.startswith(collection + "/") and "{" not in key)
        for key in candidates:
            operation = (paths.get(key) or {}).get("get") if isinstance(paths.get(key), dict) else None
            if not isinstance(operation, dict):
                continue
            if not self._parameters_are_optional(operation):
                continue
            listed = self._list_item_ref(operation)
            if item_ref and listed and listed != item_ref:
                continue
            if not item_ref or not listed:
                # One side names no schema: fall back to structural agreement.
                shape = self._item_of(document, self._response_schema(document, operation))
                item_shape = self._response_schema(document, item_get)
                if not (shape.get("properties") and item_shape.get("properties")):
                    if key != collection:
                        continue
                elif set(shape["properties"]) != set(item_shape["properties"]):
                    continue
            return key, {"get": operation}
        return None

    @staticmethod
    def _declared_defaults(operation: Any) -> dict[str, Any]:
        """Query parameters the document gives a default for.

        A default in the document is not a promise that the server applies it —
        the Swagger Petstore declares one for ``status`` on ``/pet/findByStatus``
        and still answers 400 when it is missing. Sending what the document says
        costs nothing and is what a client reading that document would do.
        """
        sent: dict[str, Any] = {}
        if not isinstance(operation, dict):
            return sent
        for parameter in operation.get("parameters") or []:
            if not isinstance(parameter, dict) or parameter.get("in") != "query":
                continue
            schema = parameter.get("schema")
            name = parameter.get("name")
            if isinstance(schema, dict) and "default" in schema and isinstance(name, str):
                sent[name] = schema["default"]
        return sent

    @staticmethod
    def _parameters_are_optional(operation: dict[str, Any]) -> bool:
        for parameter in operation.get("parameters") or []:
            if not isinstance(parameter, dict) or not parameter.get("required"):
                continue
            schema = parameter.get("schema")
            if not (isinstance(schema, dict) and "default" in schema):
                return False
        return True

    @staticmethod
    def _response_ref(operation: Any) -> str:
        """The raw ``$ref`` a 200 response names, before resolution."""
        if not isinstance(operation, dict):
            return ""
        for code in ("200", "201", 200, 201):
            entry = (operation.get("responses") or {}).get(code)
            if isinstance(entry, dict):
                body = (entry.get("content") or {}).get("application/json")
                if isinstance(body, dict) and isinstance(body.get("schema"), dict):
                    ref = body["schema"].get("$ref")
                    return str(ref) if isinstance(ref, str) else ""
        return ""

    @staticmethod
    def _list_item_ref(operation: Any) -> str:
        """The raw ``$ref`` of the items in a 200 array response."""
        if not isinstance(operation, dict):
            return ""
        for code in ("200", 200):
            entry = (operation.get("responses") or {}).get(code)
            if not isinstance(entry, dict):
                continue
            body = (entry.get("content") or {}).get("application/json")
            schema = body.get("schema") if isinstance(body, dict) else None
            if not isinstance(schema, dict):
                continue
            if schema.get("type") == "array" and isinstance(schema.get("items"), dict):
                ref = schema["items"].get("$ref")
                return str(ref) if isinstance(ref, str) else ""
            properties = schema.get("properties")
            if isinstance(properties, dict):
                for key in ("items", "records", "data", "results"):
                    nested = properties.get(key)
                    if isinstance(nested, dict) and nested.get("type") == "array" \
                            and isinstance(nested.get("items"), dict):
                        ref = nested["items"].get("$ref")
                        return str(ref) if isinstance(ref, str) else ""
        return ""

    # ───────────────────────────── schema helpers ─────────────────────────

    def _resolve(self, document: dict[str, Any], node: Any, depth: int = 0) -> dict[str, Any]:
        """Follow ``$ref`` within this document. Never fetches anything."""
        if not isinstance(node, dict) or depth > 8:
            return {}
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            target: Any = document
            for part in ref[2:].split("/"):
                if not isinstance(target, dict):
                    return {}
                target = target.get(part.replace("~1", "/").replace("~0", "~"))
            return self._resolve(document, target, depth + 1)
        for combinator in ("allOf", "anyOf", "oneOf"):
            branches = node.get(combinator)
            if isinstance(branches, list) and branches:
                merged: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
                for branch in branches:
                    part = self._resolve(document, branch, depth + 1)
                    if part.get("properties"):
                        merged["properties"].update(part["properties"])
                    merged["required"].extend(part.get("required") or [])
                    if combinator != "allOf":
                        break      # a union: the first branch is the representative one
                if merged["properties"]:
                    return merged
                return self._resolve(document, branches[0], depth + 1)
        return node

    def _response_schema(self, document: dict[str, Any], operation: Any) -> dict[str, Any]:
        if not isinstance(operation, dict):
            return {}
        responses = operation.get("responses")
        if not isinstance(responses, dict):
            return {}
        for code in ("200", "201", 200, 201):
            entry = responses.get(code)
            if isinstance(entry, dict):
                content = entry.get("content")
                if isinstance(content, dict):
                    body = content.get("application/json")
                    if isinstance(body, dict):
                        return self._resolve(document, body.get("schema"))
        return {}

    def _item_of(self, document: dict[str, Any], shape: dict[str, Any]) -> dict[str, Any]:
        """The record shape inside a list response, however the service wraps it."""
        if not isinstance(shape, dict):
            return {}
        if shape.get("type") == "array":
            return self._resolve(document, shape.get("items"))
        properties = shape.get("properties")
        if isinstance(properties, dict):
            for key in ("records", "items", "data", "results"):
                if key in properties:
                    return self._item_of(document, self._resolve(document, properties[key]))
        return shape

    def _writable_names(self, document: dict[str, Any], item_ops: dict[str, Any]) -> set[str]:
        for method in ("patch", "put"):
            operation = item_ops.get(method)
            if not isinstance(operation, dict):
                continue
            body = operation.get("requestBody")
            if not isinstance(body, dict):
                continue
            content = body.get("content")
            if not isinstance(content, dict):
                continue
            payload = content.get("application/json")
            if not isinstance(payload, dict):
                continue
            shape = self._resolve(document, payload.get("schema"))
            properties = shape.get("properties")
            if isinstance(properties, dict):
                return {str(key) for key in properties}
        return set()

    def _field(self, document: dict[str, Any], name: str, raw: Any,
               writable_names: set[str], required: set[str]) -> Optional[FieldDescriptor]:
        shape = self._resolve(document, raw)
        if not isinstance(shape, dict):
            return None
        field_name = _slug(name)
        if not NAME.match(field_name):
            return None

        declared = shape.get("type")
        if isinstance(declared, list):
            declared = next((t for t in declared if t != "null"), "string")
        fmt = str(shape.get("format") or "")
        values = [str(v) for v in (shape.get("enum") or []) if v is not None][:MAX_ENUM_VALUES]

        if values:
            kind = "enum"
        elif declared in ("object", "array"):
            return None        # MORPH's record model is flat; a nested value is not a field
        else:
            kind = _TYPES.get((str(declared or "string"), fmt)) \
                or _TYPES.get((str(declared or "string"), "")) or "string"
        if field_name in ("id", "_id", "uuid") and kind in ("string", "id", "int"):
            kind = "id"

        writable = (field_name in writable_names
                    and field_name not in NEVER_WRITABLE
                    and not bool(shape.get("readOnly"))
                    and kind != "id")
        return FieldDescriptor(
            name=field_name, type=kind, label=_title(name), values=tuple(values),
            writable=writable, required=field_name in required,
            description=clean_text(shape.get("description") or "", 200),
            max_length=shape.get("maxLength") if isinstance(shape.get("maxLength"), int) else None,
            minimum=shape.get("minimum") if isinstance(shape.get("minimum"), (int, float)) else None,
            maximum=shape.get("maximum") if isinstance(shape.get("maximum"), (int, float)) else None,
        )

    @staticmethod
    def _primary_key(fields: Sequence[FieldDescriptor]) -> str:
        for candidate in ("id", "_id", "uuid", "name", "key"):
            if any(f.name == candidate for f in fields):
                return candidate
        return fields[0].name

    @staticmethod
    def _title_field(fields: Sequence[FieldDescriptor], primary: str) -> str:
        for candidate in ("title", "name", "label", "summary", "subject"):
            if any(f.name == candidate for f in fields):
                return candidate
        text = next((f.name for f in fields if f.type in ("string", "text") and f.name != primary),
                    "")
        return text or primary

    # ──────────────────────────── derived operations ──────────────────────

    def _operations(self, fields: Sequence[FieldDescriptor],
                    primary: str) -> list[OperationDescriptor]:
        operations: list[OperationDescriptor] = []
        for field in fields:
            if not field.writable or field.name == primary:
                continue
            operations.extend(self._operations_for(field))
            if len(operations) >= MAX_OPERATIONS:
                return operations[:MAX_OPERATIONS]
        return operations

    def _operations_for(self, field: FieldDescriptor) -> list[OperationDescriptor]:
        label = field.label
        if field.type == "enum" and field.values:
            return [
                OperationDescriptor(
                    id=f"set_{field.name}_{_slug(value)}"[:48],
                    label=f"{label}: {_title(value)}",
                    description=f"Set {label.lower()} to {value!r}.",
                    effect={field.name: value}, effect_fields=(field.name,),
                    precondition=({"field": field.name, "op": "neq", "value": value,
                                   "message": f"{label.lower()} is already {value!r}"},),
                    risk="low", reversible=True, requires_approval=True)
                for value in field.values[:MAX_OPERATIONS]
            ]
        if field.type == "bool":
            return [
                OperationDescriptor(
                    id=f"set_{field.name}"[:48], label=f"Set {label.lower()}",
                    description=f"Turn {label.lower()} on.",
                    effect={field.name: True}, effect_fields=(field.name,),
                    precondition=({"field": field.name, "op": "neq", "value": True,
                                   "message": f"{label.lower()} is already set"},),
                    risk="low", reversible=True, requires_approval=True),
                OperationDescriptor(
                    id=f"clear_{field.name}"[:48], label=f"Clear {label.lower()}",
                    description=f"Turn {label.lower()} off.",
                    effect={field.name: False}, effect_fields=(field.name,),
                    precondition=({"field": field.name, "op": "neq", "value": False,
                                   "message": f"{label.lower()} is already clear"},),
                    risk="low", reversible=True, requires_approval=True),
            ]
        # An open-valued field: the effect is still exact, it just comes from the
        # operator rather than the schema.
        return [OperationDescriptor(
            id=f"set_{field.name}"[:48], label=f"Set {label.lower()}",
            description=f"Set {label.lower()} to a value you supply.",
            params=(FieldDescriptor(name="value", type=field.type, label=label,
                                    required=True, max_length=field.max_length,
                                    minimum=field.minimum, maximum=field.maximum),),
            effect={field.name: "{value}"}, effect_fields=(field.name,),
            risk="low", reversible=True, requires_approval=True)]

    # ───────────────────────────────── data ───────────────────────────────

    def _route(self, entity: str) -> tuple[str, str, str, str]:
        self.describe()
        route = self._routes.get(entity)
        if route is None:
            raise ConnectorError(f"{self.id}.{entity} is not one of this service's collections")
        return route

    def fetch(self, entity: str, *, filters: Sequence[Predicate] = (), limit: int = 100,
              offset: int = 0, order_by: str = "", descending: bool = False) -> FetchResult:
        described = self.entity_or_raise(entity)
        listing, _item, _method, _param = self._route(entity)
        status, payload = self._request("GET", listing,
                                        params=self._list_params.get(entity) or None)
        if status != 200:
            raise ConnectorError(f"{self.id!r} returned HTTP {status} listing {entity}")

        records = self._records_from(payload)
        for predicate in filters:
            records = [r for r in records if _local_match(r, predicate)]
        if order_by and any(f.name == order_by for f in described.fields):
            records.sort(key=lambda r: _sortable(r.get(order_by)), reverse=descending)
        total = len(records)
        window = records[offset:offset + max(1, min(limit, self._max_records))]
        return FetchResult(records=window, total=total)

    @staticmethod
    def _records_from(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [r for r in payload if isinstance(r, dict)]
        if isinstance(payload, dict):
            for key in ("records", "items", "data", "results"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [r for r in value if isinstance(r, dict)]
        return []

    def get(self, entity: str, record_id: str) -> Optional[dict[str, Any]]:
        _collection, item, _method, param = self._route(entity)
        status, payload = self._request("GET", item.replace("{" + param + "}", record_segment(record_id)))
        if status == 404:
            return None
        if status != 200 or not isinstance(payload, dict):
            raise ConnectorError(f"{self.id!r} returned HTTP {status} reading {entity}")
        return payload

    def changes(self, since: int = 0) -> dict[str, Any]:
        """A generic service publishes no change feed; the caller diffs instead."""
        return {"supported": False, "reason": "the OpenAPI document declares no change feed"}

    def apply(self, entity: str, record_id: str, operation: str, params: dict[str, Any], *,
              expected_version: Optional[int], idempotency_key: str, actor: str) -> ApplyResult:
        if self.config.read_only:
            raise PermissionDenied(
                f"connector {self.id!r} is configured read-only; no write reaches it")
        described = self.entity_or_raise(entity)
        operator = described.operation(operation)
        if operator is None:
            raise ConnectorError(f"{self.id}.{entity} does not expose operation {operation!r}")
        _collection, item, method, param = self._route(entity)
        if not method:
            raise ConnectorError(
                f"{self.id}.{entity} is read-only: its OpenAPI document declares no "
                "PATCH or PUT for a single record")

        before = self.get(entity, record_id)
        if before is None:
            raise NotFound(f"{entity}/{record_id} no longer exists in {self.id!r}")

        body = operator.predict(params)
        headers = {"Idempotency-Key": idempotency_key[:120], "X-Actor": clean_text(actor, 60)}
        etag = before.get("etag") or before.get("_etag")
        if etag:
            headers["If-Match"] = str(etag)
        elif expected_version is not None and "version" in before:
            headers["If-Match"] = str(expected_version)

        path = item.replace("{" + param + "}", record_segment(record_id))
        status, payload = self._request(method.upper(), path, body=body, headers=headers)
        if status == 404:
            raise NotFound(f"{entity}/{record_id} no longer exists in {self.id!r}")
        if status in (409, 412):
            raise StaleRecord(f"{entity}/{record_id} changed in {self.id!r} before the write")
        if status in (400, 422):
            raise ConnectorError(f"{self.id!r} refused the change: "
                                 f"{self._detail(payload, 'invalid parameters')}")
        if status in (401, 403):
            raise ConnectorError(f"{self.id!r} rejected MORPH's credential (HTTP {status})")
        if status not in (200, 201, 204):
            raise ConnectorError(f"{self.id!r} returned HTTP {status} applying {operation!r}")

        # 204, or a body that is not the record: re-read rather than assume.
        record = payload if isinstance(payload, dict) and payload.get(described.primary_key) \
            else self.get(entity, record_id)
        if not isinstance(record, dict):
            raise ConnectorError(f"{self.id!r} applied {operation!r} but the record "
                                 "could not be read back")
        changed = {key: {"before": before.get(key), "after": record.get(key)}
                   for key in body if before.get(key) != record.get(key)}
        return ApplyResult(record=record, changed=changed, replayed=False)


def _sortable(value: Any) -> Any:
    if isinstance(value, bool):
        return (0, int(value), "")
    if isinstance(value, (int, float)):
        return (0, value, "")
    return (1, 0, str(value if value is not None else ""))
