"""Access to the host agent's configured model.

MORPH never holds provider credentials and never chooses a model: ``register(ctx)``
hands this module the Hermes-owned ``ctx.llm`` façade, which routes to whatever the
user has configured and accounts for the usage.  Outside Hermes — tests, the CLI,
the demo — :func:`current` returns ``None`` and the planner uses its deterministic
composer, which is why MORPH works offline with no behaviour change beyond the
``mode`` it reports.
"""
from __future__ import annotations

import threading
from typing import Any, Optional

_lock = threading.RLock()
_llm: Optional[Any] = None


def bind(llm: Optional[Any]) -> None:
    global _llm
    with _lock:
        _llm = llm


def current() -> Optional[Any]:
    with _lock:
        return _llm


def available() -> bool:
    return current() is not None
