# Connecting MORPH to your applications

MORPH reaches an application over HTTP, exactly as it would reach a third-party SaaS
product. There are two ways in: publish a `morph.source.v1` schema from three endpoints,
or write an adapter.

---

## Option 1 — speak `morph.source.v1`

Three endpoints. The bundled demo applications are the reference implementation
(`apps/_runtime.py`, ~450 lines including the HTML viewer).

### `GET /morph/schema`

```jsonc
{
  "schema_version": "morph.source.v1",
  "id": "storefront",               // informational; the connector id comes from config
  "label": "Storefront",
  "vendor": "Northwind Commerce",
  "description": "Order management for the online store.",
  "entities": [
    {
      "name": "orders",             // lower_snake_case, required
      "label": "Orders",
      "primary_key": "id",
      "title_field": "customer_name",
      "fields": [
        { "name": "id", "type": "id", "role": "key" },
        { "name": "customer_name", "type": "string", "role": "title" },
        { "name": "total_amount", "type": "money", "role": "amount", "unit": "USD" },
        { "name": "status", "type": "enum", "role": "status",
          "values": ["placed", "processing", "delayed", "shipped", "fulfilled"] },
        { "name": "hours_late", "type": "int", "role": "metric", "unit": "h" },
        { "name": "review_status", "type": "enum", "values": ["none", "flagged"],
          "writable": true }
      ],
      "relationships": [
        { "name": "customer", "target_entity": "customers",
          "local_field": "customer_email", "target_field": "email",
          "cardinality": "one" }
      ],
      "operations": [
        {
          "id": "flag_for_review",
          "label": "Flag for review",
          "description": "Mark the order as needing an operations review.",
          "risk": "low",                       // low | medium | high
          "reversible": true,
          "params": [
            { "name": "reason", "type": "text", "required": true, "max_length": 200 }
          ],
          "effect": { "review_status": "flagged", "review_reason": "{reason}" },
          "precondition": [
            { "field": "review_status", "op": "neq", "value": "flagged",
              "message": "order is already flagged for review" }
          ]
        }
      ],
      "capabilities": {
        "read": true, "write": true,
        "filter_ops": ["eq","neq","in","nin","gt","gte","lt","lte","contains","exists"],
        "sort": true, "paginate": true, "max_page_size": 200,
        "optimistic_concurrency": "version", "idempotent_writes": true
      }
    }
  ]
}
```

**Field types** — `id`, `string`, `text`, `int`, `float`, `money`, `bool`, `enum`,
`datetime`, `email`. **Roles** (optional, but they make composed workspaces much better)
— `key`, `title`, `status`, `metric`, `amount`, `timestamp`, `flag`.

**`effect` is a promise, not a program.** Each value is a literal or `{param}` naming one
of the operation's own parameters. MORPH shows the operator exactly what a write will
set, and checks the record against it afterwards. Anything else — nested structures,
unknown placeholders, strings with stray braces — is dropped when the descriptor is
parsed.

### `GET /v1/{entity}`

```jsonc
{ "items": [ { "id": "ORD-2041", "status": "delayed", "_version": 3,
               "_updated_at": 1794167495.5 } ],
  "total": 10, "limit": 100, "offset": 0, "entity": "orders" }
```

Every record carries `_version`, an integer that increases on every write. That is what
makes optimistic concurrency and change detection work.

Filters arrive as query parameters: `?status=delayed&hours_late__gte=48`
(`field` for equality, `field__op` otherwise, comma-separated for `in`/`nin`). You may
ignore them — MORPH re-applies every predicate locally, so a source's filtering is an
optimisation and never the authority for what the operator asked to see.

### `GET /v1/{entity}/{id}`

The single record, same shape. MORPH calls this before every proposal and again after
every write; it is the authority for verification.

### `POST /v1/{entity}/{id}/operations/{operation}`

```
If-Match: 3                      # refuse with 409 if the record is not at this version
Idempotency-Key: <opaque>        # the same key must not apply twice
X-Actor: morph:ops@example.com   # who approved it, for your own audit trail

{ "params": { "reason": "48h late, customer chasing" } }
```

```jsonc
{ "applied": true, "replayed": false, "operation": "flag_for_review",
  "entity": "orders", "record_id": "ORD-2041",
  "changed": { "review_status": "flagged", "review_reason": "48h late, customer chasing" },
  "record": { /* the full record, read back after the commit */ } }
```

Status codes MORPH understands: `200` applied, `404` gone, `409` version mismatch or
precondition unmet, `400`/`422` invalid parameters, `401`/`403` credential refused.

### Authentication

`Authorization: Bearer <token>`. MORPH reads the token through Hermes's secret scope from
the environment variable your connector names, at call time, and never logs or returns
it.

---

## The general case: `openapi`

The other adapters ask the application to meet MORPH halfway. This one asks for nothing:

```json
{ "id": "fieldwork", "kind": "openapi", "label": "Fieldwork",
  "base_url": "https://api.example.com", "token_env": "FIELDWORK_TOKEN",
  "options": { "spec_path": "/openapi.json" } }
```

It looks for the REST shape almost every API already has — `GET /things`,
`GET /things/{id}`, and `PATCH` or `PUT` on the second — and derives everything from
there. Entities from the collection paths, fields from the record schema (following
`$ref`, `allOf`, `anyOf`), writable fields from the patch body's properties.

`options` also takes `entities` (expose only these) and `exclude` (expose all but these).

### Worked example: the Swagger Petstore

```bash
python scripts/check_openapi_connector.py
```

```
  ── pet  (key id, titled by name)
     list /pet/findByStatus   record /pet/{petId}   write none
         id                  id
         name                string
         status              enum        ['available', 'pending', 'sold']
     operations: none. Nothing here can be changed in a way MORPH
     could verify by re-reading the record afterwards.
     5 of 44 record(s)
```

Three things happened there, and the third is the interesting one.

The Petstore has **no `GET /pet`**. The listing was found by looking for a GET under
`/pet` whose 200 response is an array of the same `$ref` the single-record GET returns —
`/pet/findByStatus` — and the `status` parameter was sent with the default the document
declares. (The document says that default is optional. The live server answers `400`
without it. Sending what the document declares is what any client reading it would do.)

`category`, `photoUrls` and `tags` are **absent from the fields**, because they are
nested objects and arrays, and MORPH's record model is flat.

And there are **no operations**, because the Petstore's update is `PUT /pet` with a whole
pet at the collection, not a change to one record at `/pet/{petId}`. MORPH cannot state
in advance what such a call will leave behind, so it cannot verify it afterwards, so it
does not offer it. An adapter that invented `update_pet` here would be the kind of thing
that looks good in a demo and loses someone's data in production.

### What it will not do

- **Nested values are not fields.** MORPH's record model is flat, and a nested object
  has no honest column.
- **A collection with no single-record route is not an entity.** If one record cannot be
  read on its own, no change to it can be verified, so it is read-only data at best.
- **`readOnly` fields, identifiers and version fields are never writable**, whatever the
  patch body lists.
- **No change feed is claimed.** A generic document declares none, so `changes()` reports
  `supported: false` and the caller falls back to diffing.
- **Optimistic concurrency is best-effort.** An `ETag` becomes `If-Match`; failing that,
  a `version` field does. A service with neither gets neither — and MORPH says so rather
  than implying a guarantee it cannot make. Verified read-back still applies.

## Worked example: the GitHub connector

`morph/connectors/github.py` is the adapter route done against an API nobody controls,
and it is the honest version of what that costs:

| What MORPH wants | What GitHub has | What the adapter does |
|---|---|---|
| An integer `_version` | `updated_at` | Derives the version from the timestamp. Optimistic concurrency then means "untouched since the proposal was built" — the guarantee that matters, reached another way. |
| An `Idempotency-Key` header | nothing | Enforces the key on MORPH's side, and offers only set-to-value operations, so a repeat is harmless even if the cache is lost. |
| `GET /v1/_changes?since=` | `GET /issues?since=` | Uses GitHub's own incremental read, which catches an issue closed in the GitHub UI. |
| Declared operations | a REST API | Publishes seven, each a set-to-value: close, close as not planned, reopen, set labels, set title, assign to me, unassign. Without a token it publishes none and the connection is read-only. |

The missing operation is the interesting one. Posting a comment would be useful,
and it is deliberately absent: its effect is an increment, so MORPH could not re-read the
issue and prove the change it promised. **An action MORPH cannot verify is an action
MORPH should not offer** — and that rule is worth more than the feature.

Labels show the same rule from the other side. "Add a label" is an increment too, so
the operation is `set_labels`: the whole list, canonicalised before the promise is made
so the read-back can be compared exactly.

Connect your own repository:

```bash
hermes morph connect github owner/name
```

## Option 2 — write an adapter

For an application that will not publish the above (a vendor API you do not control, a
database, a queue), implement `morph.connectors.base.Connector` and register the class in
`morph/connectors/__init__.py`'s `KINDS`.

```python
class JiraConnector(Connector):
    kind = "jira"

    def describe(self, *, refresh=False) -> SourceDescriptor: ...
    def health(self) -> dict: ...                 # must never raise
    def fetch(self, entity, *, filters=(), limit=100, sort=None) -> FetchResult: ...
    def get(self, entity, record_id) -> dict | None: ...
    def apply(self, entity, record_id, operation, params, *,
              expected_version, idempotency_key, actor) -> ApplyResult: ...
```

What an adapter owes the engine:

- `describe()` returns a `SourceDescriptor`; build it with `SourceDescriptor.parse` so the
  sanitising and the structural checks apply to your source too.
- Records carry `_version` when the source has a version, ETag or `updated_at` you can map
  to one. Without it, optimistic concurrency degrades to "last write wins" — say so in
  `capabilities`.
- `apply()` raises `StaleRecord` on a version mismatch, `NotFound` on a missing record,
  and `ConnectorError` for anything else. It returns the record **as the source reports it
  after the write**.
- A repeated `idempotency_key` must not apply twice.

---

## Configuration

Precedence, highest first: `MORPH_CONFIG` (inline JSON) → `MORPH_CONFIG_FILE` → the
Hermes plugin config key `plugins.entries.morph.config` → `<plugin data
dir>/config.json`, which is the file `hermes morph connect` writes. With none of them
MORPH is connected to nothing. `config/connectors.sandbox.json` is a complete example.

```jsonc
{
  "allow_loopback": false,                    // required to reach 127.0.0.1 at all
  "allowed_hosts": ["api.example.com"],       // optional; empty means "policy only"
  "planner_enabled": true,                    // false pins planning to the composer
  "proposal_ttl_seconds": 3600,

  "connectors": [
    { "id": "storefront", "kind": "http-rest", "label": "Storefront",
      "base_url": "https://api.example.com",
      "token_env": "MORPH_STOREFRONT_TOKEN",  // a NAME; never a value
      "timeout": 8.0, "verify_tls": true, "max_records": 200,
      "options": { "schema_path": "/morph/schema", "records_path": "/v1",
                   "filter_style": "morph" } }
  ],

  "links": [
    { "name": "support_cases", "source": "helpdesk.cases.order_ref",
      "target": "storefront.orders.id", "cardinality": "many" }
  ],

  "default_role": "operator",
  "agent_role": "agent",
  "user_roles": { "ops@example.com": "operator", "lead@example.com": "approver" },
  "roles": {
    "operator": { "label": "Operations lead", "allow": ["*"],
                  "deny": ["storefront.orders.issue_refund"],
                  "max_risk": "medium", "can_approve": true }
  }
}
```

**Roles.** `allow` and `deny` are glob-lite patterns over
`<application>.<entity>.<operation>` (`*` matches one segment, `**` the rest). `deny`
wins. `max_risk` caps the risk level the role may touch. `can_approve` gates approval —
and is forced off for the agent role, whatever the file says.

**Network policy.** `base_url` must be HTTPS unless it is a loopback address and
`allow_loopback` is true. No credentials in the URL, no query, no fragment. Private,
link-local, reserved and cloud-metadata targets are refused. The URL is re-validated
before every request and the address the socket actually dials is validated at connect
time.

Check your wiring:

```bash
hermes morph doctor
hermes morph discover
```
