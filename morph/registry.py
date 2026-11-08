"""Connector registry: instantiate, cache and probe the configured applications."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from morph.config import ConnectorConfig, Settings, settings
from morph.connectors import KINDS, Connector, SourceDescriptor
from morph.errors import ConfigError, MorphError, NotFound
from morph.util import clean_text

_lock = threading.RLock()
_instances: dict[str, Connector] = {}
_built_from: Optional[int] = None


def _ensure(current: Settings) -> dict[str, Connector]:
    """Instantiate connectors for *current*, reusing instances across calls.

    Keyed on the identity of the frozen settings object, so a configuration
    reload transparently rebuilds the registry (and closes the old clients).
    """
    global _built_from
    with _lock:
        if _built_from == id(current) and _instances:
            return dict(_instances)
        for connector in _instances.values():
            closer = getattr(connector, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:  # noqa: BLE001 - best-effort teardown
                    pass
        _instances.clear()
        for config in current.connectors:
            if not config.enabled:
                continue
            factory = KINDS.get(config.kind)
            if factory is None:
                raise ConfigError(f"connector {config.id!r}: unknown kind {config.kind!r}")
            _instances[config.id] = factory(config)
        _built_from = id(current)
        return dict(_instances)


def connectors() -> dict[str, Connector]:
    return _ensure(settings())


def get(connector_id: str) -> Connector:
    found = connectors().get(connector_id)
    if found is None:
        raise NotFound(f"no connector named {connector_id!r} is configured")
    return found


def reset() -> None:
    """Drop cached instances (tests, and after a configuration change)."""
    global _built_from
    with _lock:
        for connector in _instances.values():
            closer = getattr(connector, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:  # noqa: BLE001
                    pass
        _instances.clear()
        _built_from = None


def _parallel(fn, items: list[str], workers: int = 4) -> dict[str, Any]:
    if not items:
        return {}
    if len(items) == 1:
        return {items[0]: fn(items[0])}
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
        return dict(zip(items, pool.map(fn, items)))


def health() -> list[dict[str, Any]]:
    """Probe every configured application. Never raises; a dead source reports itself."""
    registry = connectors()
    probes = _parallel(lambda cid: registry[cid].health(), sorted(registry))
    out = []
    for config in settings().connectors:
        if not config.enabled:
            out.append({"id": config.id, "kind": config.kind, "ok": False,
                        "error": "disabled in configuration", "disabled": True})
            continue
        probe = dict(probes.get(config.id) or {"id": config.id, "ok": False, "error": "not probed"})
        probe.setdefault("label", config.label)
        probe["base_url"] = config.base_url
        out.append(probe)
    return out


def describe_all(*, refresh: bool = False) -> tuple[list[SourceDescriptor], list[dict[str, str]]]:
    """Introspect every reachable application.

    Returns the descriptors that were obtained plus one entry per application that
    could not be read.  A single unreachable application never stops discovery:
    MORPH composes from what is actually available and says what is missing.
    """
    registry = connectors()
    ids = sorted(registry)

    def probe(cid: str) -> Any:
        try:
            return registry[cid].describe(refresh=refresh)
        except MorphError as exc:
            return exc
        except Exception as exc:  # noqa: BLE001 - adapters must not break discovery
            return MorphError(f"{type(exc).__name__}: {exc}")

    results = _parallel(probe, ids)
    descriptors: list[SourceDescriptor] = []
    failures: list[dict[str, str]] = []
    for cid in ids:
        value = results.get(cid)
        if isinstance(value, SourceDescriptor):
            descriptors.append(value)
        else:
            code = getattr(value, "code", "connector_unavailable")
            message = getattr(value, "message", str(value))
            failures.append({"id": cid, "code": code, "error": clean_text(message, 200),
                             "label": registry[cid].config.label})
    return descriptors, failures
