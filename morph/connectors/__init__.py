"""Connector implementations. Add a new adapter here and name it in ``KINDS``."""
from __future__ import annotations

from morph.connectors.base import (  # noqa: F401
    AGGREGATES, PREDICATE_OPS, ApplyResult, Connector, EntityDescriptor, FetchResult,
    FieldDescriptor, OperationDescriptor, Predicate, SourceDescriptor,
)
from morph.connectors.github import GitHubConnector
from morph.connectors.http_rest import HttpRestConnector
from morph.connectors.memory import MemoryConnector
from morph.connectors.openapi import OpenApiConnector

KINDS: dict[str, type[Connector]] = {
    HttpRestConnector.kind: HttpRestConnector,
    MemoryConnector.kind: MemoryConnector,
    GitHubConnector.kind: GitHubConnector,
    OpenApiConnector.kind: OpenApiConnector,
}

__all__ = ["KINDS", "Connector", "GitHubConnector", "HttpRestConnector", "MemoryConnector",
           "OpenApiConnector",
           "Predicate",
           "SourceDescriptor", "EntityDescriptor", "FieldDescriptor", "OperationDescriptor",
           "FetchResult", "ApplyResult", "PREDICATE_OPS", "AGGREGATES"]
