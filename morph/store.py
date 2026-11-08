"""MORPH's own persistence: workspaces, revisions, proposals and the audit log.

MORPH deliberately stores **no business records**.  Each connected application
remains the system of record; what lives here is MORPH's own state — which
workspace is current, what the operator had selected, which actions are awaiting a
human, and an append-only hash-chained log of everything that happened.

SQLite in WAL mode, because the dashboard process and the agent's tool calls both
touch this file; writes take ``BEGIN IMMEDIATE`` so a proposal cannot be approved
twice by two readers that both saw it pending.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from morph.config import data_dir
from morph.util import chain_hash, now

SCHEMA_VERSION = 3
_LOCK = threading.RLock()
_READY: set[str] = set()

DDL = """
CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    title TEXT NOT NULL,
    revision INTEGER NOT NULL,
    schema_json TEXT NOT NULL,
    context_json TEXT NOT NULL,
    mode TEXT NOT NULL,
    notes_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS revisions (
    workspace_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    goal TEXT NOT NULL,
    title TEXT NOT NULL,
    schema_json TEXT NOT NULL,
    context_json TEXT NOT NULL,
    mode TEXT NOT NULL,
    notes_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (workspace_id, revision)
);
CREATE TABLE IF NOT EXISTS signatures (
    workspace_id TEXT NOT NULL,
    entity_ref TEXT NOT NULL,
    signature_json TEXT NOT NULL,
    at REAL NOT NULL,
    PRIMARY KEY (workspace_id, entity_ref)
);
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    entity TEXT NOT NULL,
    record_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    params_json TEXT NOT NULL,
    expected_version INTEGER,
    before_json TEXT NOT NULL,
    predicted_json TEXT NOT NULL,
    risk TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    requested_role TEXT NOT NULL,
    authority TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    confirm_token TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    decided_at REAL,
    decided_by TEXT,
    result_json TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS proposals_status ON proposals (status, created_at DESC);
CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL,
    actor TEXT NOT NULL,
    authority TEXT NOT NULL,
    role TEXT NOT NULL,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def db_path() -> Path:
    return data_dir() / "morph.db"


def _connect() -> sqlite3.Connection:
    path = db_path()
    connection = sqlite3.connect(str(path), timeout=15, isolation_level=None,
                                 check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=15000")
    connection.execute("PRAGMA foreign_keys=ON")
    key = str(path)
    if key not in _READY:
        with _LOCK:
            connection.executescript(DDL)
            connection.execute("INSERT OR REPLACE INTO meta (key,value) VALUES ('schema_version',?)",
                               (str(SCHEMA_VERSION),))
            _READY.add(key)
    return connection


@contextmanager
def read() -> Iterator[sqlite3.Connection]:
    connection = _connect()
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def write() -> Iterator[sqlite3.Connection]:
    """Serialised write transaction. Commits on success, rolls back on any error."""
    with _LOCK:
        connection = _connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()


def reset_cache() -> None:
    """Forget which database files have been initialised (tests switch data dirs)."""
    with _LOCK:
        _READY.clear()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def loads(value: Any, fallback: Any = None) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


# ───────────────────────────────── audit log ───────────────────────────────────

def append_audit(connection: sqlite3.Connection, *, actor: str, authority: str, role: str,
                 kind: str, subject: str, payload: Any) -> dict[str, Any]:
    """Append one tamper-evident entry. Must run inside :func:`write`."""
    row = connection.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
    previous = row["hash"] if row else "genesis"
    at = now()
    body = {"at": at, "actor": actor, "authority": authority, "role": role, "kind": kind,
            "subject": subject, "payload": payload}
    digest = chain_hash(previous, body)
    connection.execute(
        "INSERT INTO audit (at,actor,authority,role,kind,subject,payload_json,prev_hash,hash)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (at, actor, authority, role, kind, subject, dumps(payload), previous, digest))
    return {**body, "hash": digest, "prev_hash": previous}


def audit_tail(limit: int = 30) -> list[dict[str, Any]]:
    with read() as connection:
        rows = connection.execute(
            "SELECT seq,at,actor,authority,role,kind,subject,payload_json,prev_hash,hash"
            " FROM audit ORDER BY seq DESC LIMIT ?", (max(1, min(limit, 200)),)).fetchall()
    return [{"seq": r["seq"], "at": round(r["at"], 3), "actor": r["actor"],
             "authority": r["authority"], "role": r["role"], "kind": r["kind"],
             "subject": r["subject"], "payload": loads(r["payload_json"], {}),
             "hash": r["hash"], "prev_hash": r["prev_hash"]} for r in rows]


def verify_audit_chain() -> dict[str, Any]:
    """Recompute the chain. A mismatch names the first entry that does not follow."""
    with read() as connection:
        rows = connection.execute(
            "SELECT seq,at,actor,authority,role,kind,subject,payload_json,prev_hash,hash"
            " FROM audit ORDER BY seq ASC").fetchall()
    previous = "genesis"
    for row in rows:
        body = {"at": row["at"], "actor": row["actor"], "authority": row["authority"],
                "role": row["role"], "kind": row["kind"], "subject": row["subject"],
                "payload": loads(row["payload_json"], {})}
        expected = chain_hash(previous, body)
        if row["prev_hash"] != previous or row["hash"] != expected:
            return {"ok": False, "entries": len(rows), "broken_at": row["seq"]}
        previous = row["hash"]
    return {"ok": True, "entries": len(rows), "head": previous}


# ──────────────────────────────── signatures ───────────────────────────────────

def save_signatures(workspace_id: str, signatures: dict[str, dict[str, int]]) -> None:
    if not signatures:
        return
    at = now()
    with write() as connection:
        for ref, signature in signatures.items():
            connection.execute(
                "INSERT INTO signatures (workspace_id,entity_ref,signature_json,at)"
                " VALUES (?,?,?,?) ON CONFLICT(workspace_id,entity_ref) DO UPDATE SET"
                " signature_json=excluded.signature_json, at=excluded.at",
                (workspace_id, ref, dumps(signature), at))


def load_signatures(workspace_id: str) -> dict[str, dict[str, int]]:
    with read() as connection:
        rows = connection.execute(
            "SELECT entity_ref,signature_json FROM signatures WHERE workspace_id=?",
            (workspace_id,)).fetchall()
    return {r["entity_ref"]: loads(r["signature_json"], {}) for r in rows}
