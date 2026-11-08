"""In-process connector: a schema and records held in memory.

It exists for two honest reasons and no others:

* tests need a source they can drive deterministically without sockets;
* an operator evaluating MORPH with no application connected yet gets a working
  workspace instead of an empty screen.

It is reported everywhere as ``kind: "memory"`` and its descriptor carries
``vendor: "in-process fixture"``, so nothing in the UI or in a tool result can
mistake it for a real integration.  Its write path implements the same contract as
a real source: versions, optimistic concurrency, declared preconditions,
idempotent replay and authoritative read-back.
"""
from __future__ import annotations

import copy
import threading
import time
from typing import Any, Optional, Sequence

from morph.config import ConnectorConfig
from morph.connectors.base import (
    ApplyResult, Connector, FetchResult, Predicate, SourceDescriptor,
)
from morph.connectors.http_rest import _local_match
from morph.errors import ConnectorError, NotFound, SchemaError, StaleRecord

class MemoryConnector(Connector):
    kind = "memory"

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        options = config.options or {}
        schema = options.get("schema")
        if not isinstance(schema, dict):
            raise SchemaError(f"memory connector {self.id!r} needs options.schema")
        self._lock = threading.RLock()
        self._descriptor = SourceDescriptor.parse(
            {**schema, "vendor": "in-process fixture"},
            connector_id=self.id, kind=self.kind, fetched_at=time.time())
        self._rows: dict[str, dict[str, dict[str, Any]]] = {}
        self._versions: dict[tuple[str, str], int] = {}
        self._replays: dict[str, ApplyResult] = {}
        records = options.get("records") or {}
        for entity_desc in self._descriptor.entities:
            table: dict[str, dict[str, Any]] = {}
            for row in records.get(entity_desc.name, []) or []:
                if not isinstance(row, dict):
                    continue
                rid = str(row.get(entity_desc.primary_key))
                table[rid] = copy.deepcopy(row)
                self._versions[(entity_desc.name, rid)] = 1
            self._rows[entity_desc.name] = table

    def describe(self, *, refresh: bool = False) -> SourceDescriptor:
        return self._descriptor

    def health(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "ok": True, "label": self._descriptor.label,
                "entities": [e.name for e in self._descriptor.entities], "latency_ms": 0.0,
                "note": "in-process fixture, not a real integration"}

    def _materialise(self, entity: str, record_id: str) -> Optional[dict[str, Any]]:
        row = self._rows.get(entity, {}).get(record_id)
        if row is None:
            return None
        out = copy.deepcopy(row)
        out["_version"] = self._versions.get((entity, record_id), 1)
        return out

    def fetch(self, entity: str, *, filters: Sequence[Predicate] = (), limit: int = 100,
              sort: Optional[tuple[str, str]] = None) -> FetchResult:
        described = self.entity_or_raise(entity)
        with self._lock:
            records = [self._materialise(entity, rid) for rid in sorted(self._rows.get(entity, {}))]
        records = [r for r in records if r is not None]
        for predicate in filters:
            records = [r for r in records if _local_match(r, predicate)]
        if sort:
            field, direction = sort
            records.sort(key=lambda r: (r.get(field) is None, str(r.get(field))),
                         reverse=direction == "desc")
        capped = max(1, min(int(limit), described.max_page_size, self.config.max_records))
        return FetchResult(records=records[:capped], total=len(records),
                           truncated=len(records) > capped)

    def get(self, entity: str, record_id: str) -> Optional[dict[str, Any]]:
        self.entity_or_raise(entity)
        with self._lock:
            return self._materialise(entity, record_id)

    def apply(self, entity: str, record_id: str, operation: str, params: dict[str, Any], *,
              expected_version: Optional[int], idempotency_key: str, actor: str) -> ApplyResult:
        described = self.entity_or_raise(entity)
        op = described.operation(operation)
        if op is None:
            raise ConnectorError(f"{self.id}.{entity} does not expose operation {operation!r}")
        with self._lock:
            if idempotency_key and idempotency_key in self._replays:
                stored = self._replays[idempotency_key]
                return ApplyResult(record=copy.deepcopy(stored.record),
                                   changed=dict(stored.changed), replayed=True)
            row = self._rows.get(entity, {}).get(record_id)
            if row is None:
                raise NotFound(f"{entity}/{record_id} does not exist in {self.id!r}")
            version = self._versions.get((entity, record_id), 1)
            if expected_version is not None and expected_version != version:
                raise StaleRecord(f"version mismatch: record is at version {version}")
            current = self._materialise(entity, record_id) or {}
            for predicate in op.precondition:
                check = Predicate(str(predicate.get("field")), str(predicate.get("op") or "eq"),
                                  predicate.get("value"))
                if not _local_match(current, check):
                    raise StaleRecord(str(predicate.get("message") or "precondition not met"))
            # The descriptor already validated the effect template; applying it is a
            # copy of literals and resolved parameters, never an evaluation.
            updates = {k: v for k, v in op.predict(params).items()
                       if described.field(k) is not None}
            row.update(updates)
            self._versions[(entity, record_id)] = version + 1
            record = self._materialise(entity, record_id) or {}
            result = ApplyResult(record=record, changed={k: record.get(k) for k in updates},
                                 replayed=False)
            if idempotency_key:
                self._replays[idempotency_key] = ApplyResult(record=copy.deepcopy(record),
                                                             changed=dict(result.changed))
            return result
