"""Change synchronisation: what moved in the connected applications, cheaply.

MORPH has two ways to answer "what changed?", and they are not interchangeable:

* **Cursor sync** (this module) asks each application's own change feed for
  everything after a sequence number. One small request per application, regardless
  of how many records exist, and it reports changes MORPH never read — including a
  change a person made directly in the source system.
* **Signature diffing** (``morph.resolve.diff_signatures``) compares the record ids
  and versions two resolve passes saw. It needs no cooperation from the source, but
  it only sees records the current workspace happens to read.

Cursor sync is used where a source supports it and signature diffing is the floor,
so a source that publishes no feed still works — just less cheaply. The result says
which mechanism answered, because "nothing changed" from a feed and "nothing changed"
in the slice we looked at are different claims.
"""
from __future__ import annotations

from typing import Any, Optional

from morph import registry, store
from morph.errors import MorphError
from morph.util import clean_text, now


def cursors(workspace_id: str) -> dict[str, int]:
    """The per-application cursor last recorded for this workspace."""
    stored = store.load_signatures(f"{workspace_id}::cursors")
    return {source: int(value.get("cursor", 0)) for source, value in stored.items()
            if isinstance(value, dict)}


def save_cursors(workspace_id: str, values: dict[str, int]) -> None:
    if not values:
        return
    store.save_signatures(f"{workspace_id}::cursors",
                          {source: {"cursor": int(cursor)} for source, cursor in values.items()})


def poll(workspace_id: str, *, sources: Optional[list[str]] = None) -> dict[str, Any]:
    """Ask every connected application what it changed since we last looked."""
    connectors = registry.connectors()
    wanted = [s for s in (sources or sorted(connectors)) if s in connectors]
    known = cursors(workspace_id)
    entries: list[dict[str, Any]] = []
    advanced: dict[str, int] = {}
    unsupported: list[str] = []
    failures: list[dict[str, str]] = []

    for source in wanted:
        # "Have we ever polled this source?" is not the same question as "is the
        # cursor zero?" — a source with no history yet still has a cursor of zero.
        seen_before = source in known
        since = known.get(source, 0)
        try:
            answer = connectors[source].changes(since=since)
        except MorphError as exc:
            failures.append({"source": source, "error": clean_text(exc.message, 160)})
            continue
        except Exception as exc:  # noqa: BLE001 - a feed must never break a refresh
            failures.append({"source": source, "error": f"{type(exc).__name__}"})
            continue
        if not answer.get("supported"):
            unsupported.append(source)
            continue
        cursor = int(answer.get("cursor") or since)
        advanced[source] = cursor
        first_look = not seen_before
        for change in answer.get("changes") or []:
            entity = change.get("entity") or ""
            entries.append({
                "source": source, "entity_ref": f"{source}.{entity}" if entity else source,
                "record_id": change.get("record_id", ""),
                "operation": change.get("operation", ""),
                "actor": change.get("actor", ""), "at": change.get("at"),
                "seq": change.get("seq"),
                # On the first poll the whole feed is history, not news. Saying so
                # stops a fresh workspace from announcing every change ever made.
                "backfill": first_look,
            })

    save_cursors(workspace_id, advanced)
    fresh = [e for e in entries if not e["backfill"]]
    return {
        "mechanism": "cursor" if advanced else "none",
        "changes": fresh,
        "backfilled": len(entries) - len(fresh),
        "entities_touched": sorted({e["entity_ref"] for e in fresh}),
        "sources_without_a_feed": unsupported,
        "failures": failures,
        "cursors": advanced,
        "polled_at": round(now(), 3),
    }
