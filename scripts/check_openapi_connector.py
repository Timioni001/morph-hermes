#!/usr/bin/env python3
"""Point MORPH at any OpenAPI service and report what it makes of it.

    python scripts/check_openapi_connector.py --base https://petstore3.swagger.io/api/v3

Read-only. It fetches the document, derives the entities, fields and operations,
lists a few live records and reads one back. It never writes, and the operations
it prints are the ones it *would* offer — nothing is proposed or applied.

With no ``--base`` it uses the Swagger Petstore, which is public, needs no
account and is nobody's idea of a tidy REST API — which is the point. It has no
``GET /pet``, so the listing has to be found by schema, and its update is a PUT
on the collection rather than the record, so MORPH correctly offers no
operations on it at all.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from morph import preflight  # noqa: E402

preflight.require()

from morph.errors import MorphError  # noqa: E402

PETSTORE = "https://petstore3.swagger.io/api/v3"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default=PETSTORE, help="the service's base URL")
    parser.add_argument("--spec-path", default="/openapi.json",
                        help="where the OpenAPI document lives, relative to --base")
    parser.add_argument("--token-env", default="",
                        help="environment variable holding a bearer token, if the API needs one")
    parser.add_argument("--entity", default="", help="read records from this entity only")
    parser.add_argument("--limit", type=int, default=5, help="how many records to show")
    args = parser.parse_args(argv)

    host = (urlsplit(args.base).hostname or "").lower()
    if not host:
        print(f"--base must be an absolute URL, not {args.base!r}", file=sys.stderr)
        return 2

    os.environ["MORPH_CONFIG"] = json.dumps({
        "allowed_hosts": [host],
        "allow_loopback": host in ("127.0.0.1", "localhost", "::1"),
        "connectors": [{"id": "probe", "kind": "openapi", "label": host,
                        "base_url": args.base.rstrip("/"), "token_env": args.token_env,
                        "read_only": True,
                        "options": {"spec_path": args.spec_path}}],
        "links": [], "default_role": "operator",
    })

    from morph import registry
    from morph.config import reload_settings
    reload_settings()

    print(f"MORPH · reading {args.base}{args.spec_path}\n")
    try:
        connector = registry.get("probe")
        described = connector.describe(refresh=True)
    except MorphError as exc:
        print(f"  refused: {exc.message}")
        print("\n  That is the adapter declining to invent something it cannot verify,")
        print("  not a crash. --help explains what it looks for.")
        return 1

    print(f"  {described.label}" + (f" — {described.vendor}" if described.vendor else ""))
    if described.description:
        print(f"  {described.description[:160]}")
    print(f"\n  {len(described.entities)} entity(ies) derived from the document alone:")

    for entity in described.entities:
        listing, item, method, _param = connector._routes[entity.name]
        print(f"\n  ── {entity.name}  (key {entity.primary_key}, titled by {entity.title_field})")
        print(f"     list {listing}   record {item}   write {method.upper() or 'none'}")
        for field in entity.fields:
            mark = "W" if field.writable else " "
            values = f"  {list(field.values)}" if field.values else ""
            print(f"       {mark} {field.name:<20}{field.type:<10}{values}")
        if entity.operations:
            print("     operations MORPH would offer, each with an exact effect:")
            for operation in entity.operations:
                unless = operation.precondition[0]["message"] if operation.precondition else "—"
                print(f"       · {operation.id:<26}{operation.effect}   unless: {unless}")
        else:
            print("     operations: none. Nothing here can be changed in a way MORPH")
            print("     could verify by re-reading the record afterwards.")

    targets = [e for e in described.entities if not args.entity or e.name == args.entity]
    for entity in targets[:3]:
        print(f"\n  ── live read: {entity.name}")
        try:
            result = connector.fetch(entity.name, limit=args.limit)
        except MorphError as exc:
            print(f"     could not list: {exc.message}")
            continue
        print(f"     {len(result.records)} of {result.total} record(s)")
        shown = [f.name for f in entity.fields][:4]
        for record in result.records:
            print("       " + json.dumps({k: record.get(k) for k in shown}, default=str))
        if result.records:
            key = str(result.records[0].get(entity.primary_key))
            one = connector.get(entity.name, key)
            print(f"     read back {entity.primary_key}={key}: "
                  + ("found" if one else "not found"))

    print("\n  Nothing was written. The connector was configured read-only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
