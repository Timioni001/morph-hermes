---
name: connect-an-application
description: Connect a new application to MORPH — the three endpoints a service must publish, or the adapter to write when it cannot, plus the configuration and the network rules that apply.
---

# Connecting an application to MORPH

MORPH reaches an application over HTTP, the same way it would reach a third-party
SaaS product. There are two routes in.

## Route 1: publish `morph.source.v1`

Three endpoints. `apps/_runtime.py` in the MORPH repository is a complete reference
implementation in about 450 lines.

| Endpoint | Purpose |
|---|---|
| `GET /morph/schema` | Entities, field types, enum domains, relationships, and operations with their parameters, declared effect, preconditions and risk |
| `GET /v1/{entity}` | `{"items": [...], "total": n}`, each record carrying an integer `_version` |
| `GET /v1/{entity}/{id}` | One record — the authority for pre-checks and verification |
| `POST /v1/{entity}/{id}/operations/{op}` | One named write, honouring `If-Match` and `Idempotency-Key` |
| `GET /v1/_changes?since=` | Optional. A change feed, so MORPH can ask what moved instead of re-reading everything |

Two things people get wrong:

- **`effect` is a promise, not a program.** Each value is a literal or `{param}`
  naming one of that operation's own parameters. MORPH shows the operator exactly
  what a write will set and checks the record against it afterwards. Anything else
  is dropped when the descriptor is parsed.
- **`_version` must actually move on every write.** Optimistic concurrency and
  change detection both rest on it. Without it, a stale proposal cannot be caught.

## Route 2: write an adapter

For an API you do not control, implement `morph.connectors.base.Connector` —
`describe`, `health`, `fetch`, `get`, `apply`, and optionally `changes` — and
register the class in `morph/connectors/__init__.py`'s `KINDS`. Build the descriptor
with `SourceDescriptor.parse` so the sanitising and structural checks apply to your
source too.

`apply()` raises `StaleRecord` on a version mismatch, `NotFound` on a missing
record, `ConnectorError` otherwise, and returns the record **as the source reports it
after the write**.

## Configuration

```jsonc
{
  "allow_loopback": false,
  "connectors": [
    { "id": "storefront", "kind": "http-rest", "base_url": "https://api.example.com",
      "token_env": "MORPH_STOREFRONT_TOKEN",   // a NAME, never a value
      "read_only": false, "timeout": 8.0, "max_retries": 2 }
  ],
  "links": [
    { "name": "support_cases", "source": "helpdesk.cases.order_ref",
      "target": "storefront.orders.id", "cardinality": "many" }
  ]
}
```

`links` are the cross-application joins, declared by the operator because no
application can know about another. They are what turn several systems into one
workspace.

## What will be refused

- A `base_url` that is not HTTPS, unless it is loopback *and* `allow_loopback` is on.
- Private, link-local, reserved or cloud-metadata targets — checked again at connect
  time, against the address the socket actually dials.
- Credentials in a URL. A connector names an environment variable; Hermes resolves it
  for the active profile.
- A redirect. MORPH never follows one.

Check the result with `hermes morph doctor`, which reports configuration,
reachability, credentials and storage in one pass.
