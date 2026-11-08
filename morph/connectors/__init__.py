"""Connector implementations. Add a new adapter here and name it in ``KINDS``."""
from __future__ import annotations

from morph.connectors.base import (  # noqa: F401
    AGGREGATES, PREDICATE_OPS, ApplyResult, Connector, EntityDescriptor, FetchResult,
    FieldDescriptor, OperationDescriptor, Predicate, SourceDescriptor,
)
from morph.connectors.http_rest import HttpRestConnector
from morph.connectors.memory import MemoryConnector

KINDS: dict[str, type[Connector]] = {
    HttpRestConnector.kind: HttpRestConnector,
    MemoryConnector.kind: MemoryConnector,
}

__all__ = ["KINDS", "Connector", "HttpRestConnector", "MemoryConnector", "Predicate",
           "SourceDescriptor", "EntityDescriptor", "FieldDescriptor", "OperationDescriptor",
           "FetchResult", "ApplyResult", "PREDICATE_OPS", "AGGREGATES"]
