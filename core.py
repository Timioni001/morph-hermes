"""MORPH persistent data engine and declarative UI validator."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

_LOCK = threading.RLock()

FIELDS = {
    "orders": [
        "id", "customer", "total", "status",
        "channel", "hours_open", "review_status", "follow_up"
    ],
    "support": [
        "id", "customer", "order_id",
        "priority", "status", "summary"
    ],
    "inventory": [
        "id", "name", "available", "reserved",
        "reorder_level", "restock_requested"
    ],
}

LABELS = {
    "orders": "Storefront / Orders",
    "support": "Support / Cases",
    "inventory": "Warehouse / Stock"
}

OPERATIONS = {
    "orders": {
        "flag_for_review": {
            "field": "review_status",
            "value": "flagged"
        },
        "mark_followed_up": {
            "field": "follow_up",
            "value": "done"
        }
    },
    "support": {
        "resolve_case": {
            "field": "status",
            "value": "resolved"
        },
        "escalate_case": {
            "field": "priority",
            "value": "urgent"
        }
    },
    "inventory": {
        "request_restock": {
            "field": "restock_requested",
            "value": True
        }
    },
}

SEED = {
    "orders": [
        {
            "id": "ORD-1042",
            "customer": "Amara Johnson",
            "total": 129.99,
            "status": "delayed",
            "channel": "Online",
            "hours_open": 62,
            "review_status": "new",
            "follow_up": "pending"
        },
        {
            "id": "ORD-1043",
            "customer": "David Okoro",
            "total": 84.50,
            "status": "processing",
            "channel": "Marketplace",
            "hours_open": 8,
            "review_status": "new",
            "follow_up": "pending"
        },
        {
            "id": "ORD-1044",
            "customer": "Ada Williams",
            "total": 260.00,
            "status": "delayed",
            "channel": "Online",
            "hours_open": 48,
            "review_status": "new",
            "follow_up": "pending"
        },
        {
            "id": "ORD-1045",
            "customer": "Tobi Bello",
            "total": 34.00,
            "status": "fulfilled",
            "channel": "Online",
            "hours_open": 3,
            "review_status": "new",
            "follow_up": "done"
        },
        {
            "id": "ORD-1046",
            "customer": "Ife Nwosu",
            "total": 173.20,
            "status": "delayed",
            "channel": "Marketplace",
            "hours_open": 75,
            "review_status": "new",
            "follow_up": "pending"
        },
    ],
    "support": [
        {
            "id": "CS-201",
            "customer": "Amara Johnson",
            "order_id": "ORD-1042",
            "priority": "high",
            "status": "open",
            "summary": "Delivery has not arrived"
        },
        {
            "id": "CS-202",
            "customer": "Ada Williams",
            "order_id": "ORD-1044",
            "priority": "medium",
            "status": "open",
            "summary": "Requesting an ETA"
        },
        {
            "id": "CS-203",
            "customer": "Ife Nwosu",
            "order_id": "ORD-1046",
            "priority": "high",
            "status": "open",
            "summary": "Order missing tracking"
        },
    ],
    "inventory": [
        {
            "id": "SKU-01",
            "name": "Classic Backpack",
            "available": 4,
            "reserved": 3,
            "reorder_level": 10,
            "restock_requested": False
        },
        {
            "id": "SKU-02",
            "name": "Wireless Mouse",
            "available": 24,
            "reserved": 5,
            "reorder_level": 8,
            "restock_requested": False
        },
        {
            "id": "SKU-03",
            "name": "USB-C Hub",
            "available": 2,
            "reserved": 7,
            "reorder_level": 12,
            "restock_requested": False
        },
    ],
}


def db_path() -> Path:
    override = os.getenv("MORPH_DATA_DIR")

    if override:
        directory = Path(override).expanduser().resolve()
    else:
        try:
            from plugins.plugin_storage import plugin_data_dir
            directory = Path(plugin_data_dir("morph"))
        except ImportError:
            directory = Path(
                os.getenv(
                    "HERMES_HOME",
                    str(Path.home() / ".hermes")
                )
            ) / "plugin-data" / "morph"

    directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    return directory / "morph.db"


def _db():
    connection = sqlite3.connect(
        str(db_path()),
        timeout=12,
        isolation_level=None
    )
    connection.row_factory = sqlite3.Row

    connection.execute("PRAGMA busy_timeout=12000")
    connection.execute("PRAGMA journal_mode=WAL")

    return connection


def initialize():
    with _LOCK, _db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS records (
                source TEXT NOT NULL,
                id TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                payload TEXT NOT NULL,
                PRIMARY KEY(source, id)
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS workspaces (
                id TEXT PRIMARY KEY,
                goal TEXT NOT NULL,
                revision INTEGER NOT NULL,
                spec TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS actions (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                record_id TEXT NOT NULL,
                operation TEXT NOT NULL,
                expected_version INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                result TEXT
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                action_id TEXT NOT NULL,
                action TEXT NOT NULL,
                detail TEXT NOT NULL,
                happened_at REAL NOT NULL
            )
        """)

        if db.execute(
            "SELECT COUNT(*) FROM records"
        ).fetchone()[0] == 0:

            for source, rows in SEED.items():
                for row in rows:
                    db.execute(
                        "INSERT INTO records (source,id,payload) "
                        "VALUES (?,?,?)",
                        (source, row["id"], json.dumps(row))
                    )


def _remote_config():
    """Load trusted, operator-configured HTTPS connectors."""
    try:
        config = json.loads(
            os.getenv("MORPH_REMOTE_JSON", "{}")
        )
    except json.JSONDecodeError:
        return {}

    if not isinstance(config, dict):
        return {}

    allowed = {}

    for name, item in config.items():
        if (
            not isinstance(item, dict)
            or name in FIELDS
            or not name.isidentifier()
        ):
            continue

        url = item.get("url", "")

        parts = urlsplit(url) if isinstance(url, str) else urlsplit("")
        fields = item.get("fields", [])

        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username
            or parts.password
            or not isinstance(fields, list)
            or not (1 <= len(fields) <= 24)
        ):
            continue

        if not all(
            isinstance(f, str) and f.isidentifier()
            for f in fields
        ):
            continue

        allowed[name] = {
            "url": url,
            "token_env": item.get("token_env", ""),
            "fields": fields,
            "label": item.get("label", name)
        }

    return allowed


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _fetch_remote(source: str) -> list[dict]:
    config = _remote_config()[source]

    headers = {
        "Accept": "application/json"
    }

    key = (
        os.getenv(config["token_env"], "")
        if config.get("token_env")
        else ""
    )

    if config.get("token_env") and not key:
        raise ValueError(
            "Connector credentials are not configured"
        )

    if key:
        headers["Authorization"] = "Bearer " + key

    request = Request(
        config["url"],
        headers=headers
    )

    with build_opener(_NoRedirect()).open(
        request,
        timeout=8
    ) as resp:

        if resp.status != 200:
            raise ValueError(
                "Remote connector unavailable"
            )

        body = resp.read(256_001)

        if len(body) > 256_000:
            raise ValueError(
                "Remote response too large"
            )

    data = json.loads(body)

    if not isinstance(data, list):
        raise ValueError(
            "Remote connector must return a JSON array"
        )

    result = []

    for record in data[:100]:
        if isinstance(record, dict):
            result.append({
                f: record.get(f)
                for f in config["fields"]
            })

    return result


def catalog():
    initialize()

    sources = [
        {
            "id": src,
            "label": LABELS[src],
            "fields": fields,
            "mode": "writable-demo",
            "actions": list(OPERATIONS[src])
        }
        for src, fields in FIELDS.items()
    ]

    for name, cfg in _remote_config().items():
        sources.append({
            "id": name,
            "label": cfg["label"],
            "fields": cfg["fields"],
            "mode": "read-only-remote",
            "actions": []
        })

    return {
        "sources": sources,
        "widget_types": ["metric", "table"],
        "note": (
            "Remote sources are configured by the "
            "operator and read only."
        )
    }


def list_records(source: str):
    initialize()

    if source in _remote_config():
        return _fetch_remote(source)

    if source not in FIELDS:
        raise ValueError("Unknown data source")

    with _db() as db:
        return [
            json.loads(r["payload"])
            for r in db.execute(
                "SELECT payload FROM records "
                "WHERE source=? ORDER BY id",
                (source,)
            )
        ]


def validate_spec(spec):
    if (
        not isinstance(spec, dict)
        or not isinstance(spec.get("widgets"), list)
    ):
        raise ValueError(
            "Layout must contain widgets"
        )

    widgets = spec["widgets"]

    if not 1 <= len(widgets) <= 10:
        raise ValueError(
            "Workspace requires 1–10 widgets"
        )

    sources = {
        s["id"]: s
        for s in catalog()["sources"]
    }

    clean = []

    for i, widget in enumerate(widgets):
        if not isinstance(widget, dict):
            raise ValueError("Invalid widget")

        source = widget.get("source")
        kind = widget.get("kind")

        if (
            source not in sources
            or kind not in ("metric", "table")
        ):
            raise ValueError(
                "Unknown source or widget type"
            )

        valid_fields = sources[source]["fields"]
        title = widget.get("title", source)

        if (
            not isinstance(title, str)
            or len(title) > 70
        ):
            raise ValueError(
                "Invalid widget title"
            )

        if kind == "metric":
            aggregation = widget.get(
                "aggregation",
                "count"
            )

            if aggregation not in ("count", "sum"):
                raise ValueError(
                    "Unsupported aggregation"
                )

            field = widget.get("field")

            if (
                aggregation == "sum"
                and field not in valid_fields
            ):
                raise ValueError(
                    "Invalid metric field"
                )

            fields = []

        else:
            aggregation, field = None, None

            fields = widget.get(
                "fields",
                valid_fields[:5]
            )

            if (
                not isinstance(fields, list)
                or not 1 <= len(fields) <= 7
                or any(
                    f not in valid_fields
                    for f in fields
                )
            ):
                raise ValueError(
                    "Invalid table columns"
                )

        where = widget.get("where", {})

        if (
            not isinstance(where, dict)
            or len(where) > 3
            or any(
                f not in valid_fields
                or not isinstance(
                    v,
                    (str, int, float, bool)
                )
                for f, v in where.items()
            )
        ):
            raise ValueError(
                "Invalid filter"
            )

        clean.append({
            "id": f"w-{i}",
            "kind": kind,
            "source": source,
            "title": title,
            "fields": fields,
            "aggregation": aggregation,
            "field": field,
            "where": where
        })

    return {
        "widgets": clean
    }


def preset(goal: str):
    """Non-AI fallback for Quick Transform."""
    goal_l = goal.lower()

    if any(
        w in goal_l
        for w in (
            "delay",
            "support",
            "customer",
            "refund",
            "complaint"
        )
    ):
        return {
            "widgets": [
                {
                    "kind": "metric",
                    "source": "orders",
                    "title": "Delayed orders",
                    "where": {
                        "status": "delayed"
                    }
                },
                {
                    "kind": "metric",
                    "source": "support",
                    "title": "Open cases",
                    "where": {
                        "status": "open"
                    }
                },
                {
                    "kind": "table",
                    "source": "orders",
                    "title": "Orders needing attention",
                    "fields": [
                        "id", "customer", "status",
                        "hours_open", "follow_up"
                    ],
                    "where": {
                        "status": "delayed"
                    }
                },
                {
                    "kind": "table",
                    "source": "support",
                    "title": "Customer conversations",
                    "fields": [
                        "id", "customer", "order_id",
                        "priority", "status", "summary"
                    ]
                }
            ]
        }

    if any(
        w in goal_l
        for w in (
            "stock",
            "inventory",
            "warehouse",
            "reorder"
        )
    ):
        return {
            "widgets": [
                {
                    "kind": "metric",
                    "source": "inventory",
                    "title": "Products tracked"
                },
                {
                    "kind": "table",
                    "source": "inventory",
                    "title": "Inventory health",
                    "fields": [
                        "id", "name", "available",
                        "reserved", "reorder_level",
                        "restock_requested"
                    ]
                },
                {
                    "kind": "table",
                    "source": "orders",
                    "title": "Incoming order demand",
                    "fields": [
                        "id", "customer",
                        "status", "total"
                    ]
                }
            ]
        }

    return {
        "widgets": [
            {
                "kind": "metric",
                "source": "orders",
                "title": "Total orders"
            },
            {
                "kind": "metric",
                "source": "support",
                "title": "Customer cases"
            },
            {
                "kind": "metric",
                "source": "inventory",
                "title": "Products"
            },
            {
                "kind": "table",
                "source": "orders",
                "title": "Recent orders",
                "fields": [
                    "id", "customer", "total",
                    "status", "channel"
                ]
            },
            {
                "kind": "table",
                "source": "support",
                "title": "Customer conversations",
                "fields": [
                    "id", "customer", "order_id",
                    "priority", "status"
                ]
            },
            {
                "kind": "table",
                "source": "inventory",
                "title": "Inventory",
                "fields": [
                    "id", "name", "available",
                    "reorder_level"
                ]
            }
        ]
    }


def compose(goal: str, spec=None, *, mode="preset"):
    if (
        not isinstance(goal, str)
        or not 2 <= len(goal.strip()) <= 400
    ):
        raise ValueError(
            "Goal must be 2–400 characters"
        )

    clean = validate_spec(
        spec if spec is not None
        else preset(goal)
    )

    with _LOCK, _db() as db:
        old = db.execute(
            "SELECT revision FROM workspaces "
            "WHERE id='main'"
        ).fetchone()

        rev = (
            int(old["revision"]) + 1
            if old else 1
        )

        db.execute(
            "INSERT INTO workspaces "
            "(id,goal,revision,spec,updated_at) "
            "VALUES ('main',?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "goal=excluded.goal, "
            "revision=excluded.revision, "
            "spec=excluded.spec, "
            "updated_at=excluded.updated_at",
            (
                goal,
                rev,
                json.dumps(clean),
                time.time()
            )
        )

    return {
        "goal": goal,
        "revision": rev,
        "mode": mode,
        "spec": clean
    }


def workspace():
    initialize()

    with _db() as db:
        row = db.execute(
            "SELECT goal,revision,spec,updated_at "
            "FROM workspaces WHERE id='main'"
        ).fetchone()

    if not row:
        compose(
            "Manage my business operations"
        )
        return workspace()

    return {
        "goal": row["goal"],
        "revision": row["revision"],
        "spec": json.loads(row["spec"]),
        "updated_at": row["updated_at"]
    }


def snapshot():
    ws = workspace()
    widgets = []

    for widget in ws["spec"]["widgets"]:
        try:
            rows = list_records(widget["source"])

            for f, expected in widget["where"].items():
                rows = [
                    r for r in rows
                    if r.get(f) == expected
                ]

            if widget["kind"] == "metric":
                if widget["aggregation"] == "sum":
                    values = [
                        r.get(widget["field"])
                        for r in rows
                    ]

                    value = round(
                        sum(
                            v for v in values
                            if isinstance(v, (int, float))
                            and not isinstance(v, bool)
                        ),
                        2
                    )
                else:
                    value = len(rows)

                widgets.append({
                    **widget,
                    "value": value
                })

            else:
                widgets.append({
                    **widget,
                    "rows": [
                        {
                            f: r.get(f)
                            for f in widget["fields"]
                        }
                        for r in rows[:50]
                    ]
                })

        except (
            ValueError,
            OSError,
            TimeoutError
        ) as exc:
            widgets.append({
                **widget,
                "error": str(exc)[:150]
            })

    return {
        **ws,
        "widgets": widgets,
        "catalog": catalog(),
        "pending": pending_actions(),
        "events": recent_events()
    }


def pending_actions():
    initialize()

    with _db() as db:
        rows = db.execute(
            "SELECT id,source,record_id,operation,"
            "status,created_at "
            "FROM actions WHERE status='pending' "
            "ORDER BY created_at DESC LIMIT 20"
        ).fetchall()

        return [
            dict(r) for r in rows
        ]


def recent_events():
    initialize()

    with _db() as db:
        return [
            dict(r)
            for r in db.execute(
                "SELECT action,detail,happened_at "
                "FROM events "
                "ORDER BY happened_at DESC LIMIT 15"
            )
        ]


def propose_action(
    source: str,
    record_id: str,
    operation: str
):
    if (
        source not in OPERATIONS
        or operation not in OPERATIONS[source]
    ):
        raise ValueError(
            "Operation is not permitted for this source"
        )

    with _LOCK, _db() as db:
        row = db.execute(
            "SELECT version,payload "
            "FROM records "
            "WHERE source=? AND id=?",
            (source, record_id)
        ).fetchone()

        if not row:
            raise ValueError(
                "Record not found"
            )

        payload = json.loads(
            row["payload"]
        )

        rule = OPERATIONS[source][operation]

        if (
            payload.get(rule["field"])
            == rule["value"]
        ):
            raise ValueError(
                "Target is already in the desired state"
            )

        action_id = uuid.uuid4().hex

        db.execute(
            "INSERT INTO actions "
            "(id,source,record_id,operation,"
            "expected_version,status,created_at) "
            "VALUES (?,?,?,?,?,'pending',?)",
            (
                action_id,
                source,
                record_id,
                operation,
                row["version"],
                time.time()
            )
        )

    return {
        "id": action_id,
        "source": source,
        "record_id": record_id,
        "operation": operation,
        "status": "pending",
        "approval_required": True,
        "effect": {
            rule["field"]: rule["value"]
        }
    }


def approve_action(action_id: str):
    """Called by the authenticated dashboard, not agent tools."""

    with _LOCK, _db() as db:
        db.execute("BEGIN IMMEDIATE")

        action = db.execute(
            "SELECT * FROM actions WHERE id=?",
            (action_id,)
        ).fetchone()

        if (
            not action
            or action["status"] != "pending"
        ):
            db.execute("ROLLBACK")
            raise ValueError(
                "Action is missing or no longer awaiting approval"
            )

        source = action["source"]
        record_id = action["record_id"]
        op = action["operation"]

        row = db.execute(
            "SELECT payload,version "
            "FROM records "
            "WHERE source=? AND id=?",
            (source, record_id)
        ).fetchone()

        if (
            not row
            or row["version"]
            != action["expected_version"]
        ):
            db.execute(
                "UPDATE actions SET status='stale' "
                "WHERE id=?",
                (action_id,)
            )
            db.execute("COMMIT")

            raise ValueError(
                "The record changed; inspect it "
                "and propose a new action"
            )

        body = json.loads(
            row["payload"]
        )

        rule = OPERATIONS[source][op]

        body[rule["field"]] = rule["value"]

        db.execute(
            "UPDATE records "
            "SET payload=?, version=version+1 "
            "WHERE source=? AND id=?",
            (
                json.dumps(body),
                source,
                record_id
            )
        )

        verified = json.loads(
            db.execute(
                "SELECT payload "
                "FROM records "
                "WHERE source=? AND id=?",
                (source, record_id)
            ).fetchone()["payload"]
        )

        if (
            verified.get(rule["field"])
            != rule["value"]
        ):
            db.execute("ROLLBACK")
            raise RuntimeError(
                "Readback verification failed"
            )

        db.execute(
            "UPDATE actions SET status='applied', "
            "result=? WHERE id=?",
            (
                json.dumps({
                    "verified": True,
                    "field": rule["field"],
                    "value": rule["value"]
                }),
                action_id
            )
        )

        db.execute(
            "INSERT INTO events "
            "(id,action_id,action,detail,happened_at) "
            "VALUES (?,?,?,?,?)",
            (
                uuid.uuid4().hex,
                action_id,
                "approved_action",
                (
                    f"{op} on {source}/{record_id} "
                    "— verified"
                ),
                time.time()
            )
        )

        db.execute("COMMIT")

        return {
            "id": action_id,
            "status": "applied",
            "verified": True,
            "record": verified
        }


def reject_action(action_id: str):
    with _LOCK, _db() as db:
        changed = db.execute(
            "UPDATE actions SET status='rejected' "
            "WHERE id=? AND status='pending'",
            (action_id,)
        ).rowcount

    if not changed:
        raise ValueError(
            "Action is missing or "
            "no longer awaiting approval"
        )

    return {
        "id": action_id,
        "status": "rejected"
    }
