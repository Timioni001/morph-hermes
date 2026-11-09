# Architecture

MORPH is one pipeline with eight stages. Each stage has a single module, a single
responsibility, and a narrow interface to the next — which is what lets a new
application be a new adapter rather than a change to the engine.

```
goal ─▶ interpretation ─▶ discovery ─▶ capability graph ─▶ planning ─▶ validation
                                                                          │
                            authorised execution ◀─ resolution ◀──────────┘
                                      │                  ▲
                                      │                  └── change feed (cursor sync)
                                      └─▶ read-back verification
```

Everything — the Hermes tools, the dashboard API, the CLI, the demo — goes through
`morph/engine.py`, so each operation has one implementation and authority is decided in
one place.

---

## 0. Interpretation (`morph/intent.py`)

Before anything is planned, the goal is *read*. The output is an :class:`Intent`:

* a **mode** — `review`, `analyse`, `monitor`, `act` or `explore` — which decides the
  shape of the workspace. A review goal gets a board and a sortable queue; an analysis
  goal gets charts; a monitoring goal gets a timeline; an action goal leads with the
  form and the record it targets.
* a ranked **focus**: which entities the goal is about, whether the operator named them,
  and the filters their words imply (`"delayed orders"` → `status = delayed`).
* **what it could not cover.** This is the half that earns the stage. If the goal asks
  for a refund and no connected entity exposes one, MORPH says so. If it exposes one but
  the caller's role is denied it, MORPH says *that* instead — a different answer, and the
  useful one.

Two rules keep it honest. A word that named an *operation* does not also become a filter
("flag the delayed orders" must not filter to orders already flagged — those are exactly
the ones that do not need flagging). And a small, declared synonym table bridges the
operator's vocabulary to the schema's ("complaint" → case, "stock" → inventory) at a
fraction of a direct match's weight, so it can pull an entity into focus but never take
the goal over. It applies to the goal only, so it can never invent a field.

It is deterministic and inspectable — the Context view renders it — which is what makes
it a sound floor for a stage the model also participates in.

## 1. Discovery (`morph/registry.py`, `morph/connectors/`)

A **connector** is the only part of MORPH that knows how to talk to a particular
application. The contract is five methods:

```python
describe(refresh=False) -> SourceDescriptor   # introspect the schema
health()                -> dict               # cheap probe; never raises
fetch(entity, filters, limit, sort)           # read records
get(entity, record_id)                        # authoritative single read
apply(entity, record_id, operation, params,   # one named write
      expected_version, idempotency_key, actor)
```

Two adapters ship: `http-rest`, which talks to anything publishing a `morph.source.v1`
schema (see `docs/CONNECTORS.md`), and `memory`, an in-process fixture used by tests and
by a first run with nothing connected yet. It reports itself as `kind: memory` with
`vendor: "in-process fixture"` everywhere, so nothing can mistake it for an integration.

Descriptors are **parsed, not trusted**. A remote application's payload goes through
`SourceDescriptor.parse`, which requires identifiers to be plain `lower_snake_case`,
flattens and caps every label and description, caps collection sizes, rejects an
operation that claims to write a field the entity does not publish, and refuses to let a
source waive MORPH's approval requirement.

Discovery is parallel and partial-failure tolerant: an unreachable application is
returned in `unavailable` with its reason, and composition continues from the rest.

## 2. The capability graph (`morph/capability.py`)

The graph holds **schema only**: entity and field names, types, enum domains, declared
operations and the relationships between entities. No record value ever enters it.

That is the structural reason MORPH can hand the graph to a language model without
handing over customer data, and the structural reason "the model may not invent a field"
is a guarantee rather than a prompt instruction: the validator checks against this graph.

Relationships come from two places. An application declares its own
(`orders.customer_email → customers.email`). An operator declares the ones that cross
applications (`helpdesk.cases.order_ref → storefront.orders.id`), because no application
can know about another. Cross-application links are made traversable in both directions.

## 3. Planning (`morph/planner.py`)

```
score(goal, graph) ──▶ model attempt ──▶ validate ──▶ ✓
                            │               │
                            │               ✗ errors
                            ▼               │
                       repair attempt ◀─────┘
                            │
                            ✗ ──▶ deterministic composer ──▶ validate ──▶ ✓
```

The **model path** sends the goal, the compact graph and the preserved context through
`ctx.llm.complete_structured` with a JSON schema. It is Hermes's model, Hermes's
credentials and Hermes's accounting; MORPH chooses no provider and holds no key.

The **repair pass** is what makes the model path reliable: the validator's own error list
("`crm.invented` is not a connected entity; available: …") goes back with the second
request. One attempt, then the floor.

The **deterministic composer** is not a stored layout. It scores every entity, field,
enum value and operation against the words of the goal — entity and field name matches,
enum value matches (which become filters: "delayed orders" → `status = delayed`),
operation name matches (which pick the action form), relationship count as a tie-break —
and assembles metrics, a chart over the top status-like field, a table of the primary
entity, a cross-application relation, a detail pane, a form for the best-matching
operation, contextual actions and a workflow. Run it against a different set of
applications and it composes a different workspace, because it has read a different
graph.

## 4. The UI schema (`morph/uischema.py`)

`morph.ui.v1` is a declarative document: sections of blocks, where a block is one of
`metric`, `table`, `chart`, `detail`, `relation`, `form`, `actions`, `workflow`.

A block names an entity, columns, typed filter predicates (`eq, neq, in, nin, gt, gte,
lt, lte, contains, exists`), a sort, a limit, an aggregate, a relationship, or an
operation. **There is no field in the schema that can hold markup, a URL, a handler or an
expression** — a property-name scan is part of the test suite — so the renderer has
nothing to execute even if a model tried.

`validate()` is strict and explains itself. Every rejection is a sentence naming the
block and the problem, which is what the repair pass feeds back. Values are coerced to
the field's declared type; enum filters must use published values; an aggregate other
than `count` needs a numeric field; a filter operator must be one the entity says it
supports; a form must include every required parameter.

A stored workspace is re-validated against the live graph on every load, so a workspace
that no longer matches reality fails loudly instead of rendering stale blocks.

## 5. Resolution (`morph/resolve.py`)

One pass performs the smallest set of reads that satisfies every block — identical
entity+filter+limit requests hit once — then aggregates in process and joins in memory,
because no application can join against another's database.

- **Partial failure is visible.** A dead application fails its own blocks and the
  resolution is marked `degraded` with the reason. A relation block whose other side is
  down fails too: half a join is not a join.
- **Nothing is cached across passes.** Every number comes from a read in that pass, and
  `fetched_at` says when.
- **Change detection is real.** Each entity read yields `{record_id: version}`; comparing
  two passes names the records that were added, removed or modified.

## 5b. Change synchronisation (`morph/sync.py`)

Resolution answers "what is true now?". Synchronisation answers "what moved?", and there
are two mechanisms because they see different things:

* **Cursor sync** asks each application's own `GET /v1/_changes?since=` feed. One small
  request per application regardless of how many records exist, and it sees changes
  MORPH never read — including an edit a person made directly in the source system,
  attributed to them.
* **Signature diffing** compares record ids and versions across two resolve passes. It
  needs no cooperation from the source, but only sees the slice the workspace reads.

Cursor sync is used where a source publishes a feed; signature diffing is the floor. The
result says which answered, because "nothing changed" from a feed and "nothing changed in
the slice we looked at" are different claims. A refresh also names the blocks that need
redrawing, so a change to one entity does not rebuild the page.

The first poll of a source is treated as history, not news: a fresh workspace must not
announce every change ever made.

## 6. Authorised execution (`morph/actions.py`)

A **principal** carries an identity, a role and an *authority*:

| | `AGENT` | `OPERATOR` |
|---|---|---|
| propose | yes, subject to role | yes, subject to role |
| receive a confirmation token | **never** | yes |
| approve | **never** | if the role allows |

`approve()` refuses an agent principal before it looks at anything else. `morph/config.py`
forces `can_approve` off for the agent role even if a configuration file sets it. A
`pre_tool_call` hook blocks any tool in the `morph_` namespace that MORPH did not
register, so a second plugin cannot introduce `morph_approve`. The interactive CLI is the
only other approval path and refuses without a terminal, so an agent with shell access
cannot use it either.

A proposal records the record's version, the operation's parameters, and the exact field
values the operation promised to set. Approval then:

1. claims the proposal inside one serialised transaction (two approvals cannot race);
2. checks the confirmation token with a constant-time compare;
3. re-checks the role, because roles change;
4. re-reads the record and voids the proposal as `stale` if the version moved;
5. writes with `If-Match` and the proposal's idempotency key;
6. **re-reads the record from the source** and compares it field by field with what was
   promised.

A mismatch is recorded as `unverified` and raised as `VerificationFailed`. MORPH never
reports a write as successful because the write call returned 200.

Batches are sequential and the result says so: `atomic: false`, with `applied` and
`failed` listed separately. There is no distributed transaction across independent
applications and MORPH does not pretend there is one.

## 7. Persistence (`morph/store.py`)

MORPH's SQLite file holds workspaces, revisions, pinned context, proposals, read
signatures and the audit log — and **no business records**. WAL mode, because the
dashboard process and the agent's tool calls both touch it; `BEGIN IMMEDIATE` on writes.

The audit log is hash-chained: each entry hashes its own body together with the previous
entry's hash. `verify_audit_chain()` recomputes the chain and names the first entry that
does not follow, so a deleted or edited entry is detectable rather than invisible.

---

## The dashboard

`dashboard/plugin_api.py` is a FastAPI router mounted by Hermes at
`/api/plugins/morph/`. Hermes authenticates it; this module decides *who* is calling
(the verified session's identity selects a role), refuses cross-site callers on every
mutating route, and runs the blocking engine off the event loop.

`dashboard/dist/index.js` is a plain IIFE built by concatenating `dashboard/src/*.js` —
no bundler, no `node_modules`, nothing to audit. React and the hooks come from
`window.__HERMES_PLUGIN_SDK__`; the page registers on `window.__HERMES_PLUGINS__`. Every
value reaching the DOM is a React text child: no `innerHTML`, no
`dangerouslySetInnerHTML`, no data-derived `href`, no `eval`.

A transformation animates deliberately — the operator needs to see that the *workspace*
changed, not that the page reloaded — and all of it collapses under
`prefers-reduced-motion`.

## Adding an application

1. Expose three endpoints (`docs/CONNECTORS.md`), or write an adapter implementing the
   five-method contract and register it in `morph/connectors/__init__.py`.
2. Add it to `connectors` in your config, with a `token_env` naming an environment
   variable Hermes resolves.
3. Declare any cross-application `links`.

Nothing else changes. The planner, the validator, the renderer and the action engine all
work from the graph.
