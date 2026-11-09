"""The Hermes surfaces MORPH reaches *out* through, bound at registration.

The engine must not import Hermes: it runs in tests, in the CLI and in the demo with
no agent anywhere. So the two outward surfaces it wants — publishing an event other
plugins can subscribe to, and putting a line in front of the operator when something
needs them — are bound here by ``register(ctx)`` and are no-ops otherwise.

Both are deliberately fire-and-forget. A notification that fails must never fail the
action it was announcing.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger("morph.hostsurface")

_lock = threading.RLock()
_emit: Optional[Callable[[str, dict], Any]] = None
_notify: Optional[Callable[[str], Any]] = None


def bind(*, emit: Optional[Callable[[str, dict], Any]] = None,
         notify: Optional[Callable[[str], Any]] = None) -> None:
    global _emit, _notify
    with _lock:
        _emit, _notify = emit, notify


def emit(event: str, payload: dict[str, Any]) -> None:
    """Publish ``morph:<event>`` for any plugin that subscribed to it."""
    with _lock:
        sink = _emit
    if sink is None:
        return
    try:
        sink(event, payload)
    except Exception as exc:  # noqa: BLE001 - observability must not break the action
        logger.debug("MORPH could not emit %s: %s", event, exc)


def notify(text: str) -> None:
    """Put one line in front of the operator in their current session."""
    with _lock:
        sink = _notify
    if sink is None:
        return
    try:
        sink(text)
    except Exception as exc:  # noqa: BLE001
        logger.debug("MORPH could not notify: %s", exc)


def available() -> bool:
    with _lock:
        return _emit is not None or _notify is not None
