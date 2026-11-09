# MORPH

**Software that adapts to you.**

MORPH is a native [Hermes Agent](https://hermes-agent.nousresearch.com) plugin that turns
several independent applications into one workspace shaped around what you are trying to
get done — and lets you act on them from that workspace, with a human approving every
write and the result verified in the application that owns the record.

Tell it *"work through delayed orders and the complaints about them"*. MORPH reads the
schemas of the applications it is connected to, works out what that goal is actually
about, composes an interface from them, fills it with data read live from each
application, joins records across application boundaries, and offers only the actions
your role is permitted to take. Change the goal and the workspace changes with it,
carrying your selection and your filters over.

There is no layout in this repository. Every workspace is composed at request time from
whatever the connected applications happen to publish.

## Why this is a plugin, and not a prompt or a skill

A prompt can describe a dashboard. A skill can teach an agent a workflow. Neither can:

- **hold a capability graph** introspected from live third-party APIs, and refuse any
  plan naming a field or operation that is not in it;
- **enforce an approval boundary the model cannot cross** — the agent is never handed
  the confirmation token an approval requires, and a `pre_tool_call` hook closes the
  `morph_` namespace so no other plugin can hand it one;
- **write to a system of record** with optimistic concurrency and an idempotency key,
  then **re-read it and compare against what the operation promised** before anyone is
  told it worked;
- **keep a hash-chained audit log** that detects its own tampering;
- **see a change someone else made** in the source application and reconcile it.

All of that is server-side state and authority. It is the part of the problem a prompt
cannot reach.

> If you are looking at this next to a generative-UI plugin: those draw the answer.
> MORPH operates the systems. The demo is not a rendered chart — it is an order that is
> actually flagged in the Storefront, with the Storefront's own changelog showing who
> approved it.

Three demo applications ship with MORPH so the whole thing is reproducible with no
account and no key. A **GitHub connector** ships alongside them to show the adapter
contract is not shaped around them: GitHub publishes no MORPH schema, no version field,
no idempotency header and no change feed in the shape MORPH asks for, and it still slots
in as one more application in the graph — with optimistic concurrency derived from
`updated_at`, idempotency enforced on MORPH's side, and incremental reads as its change
feed. See `docs/CONNECTORS.md`.

![MORPH workspace](docs/assets/catalog-card.png)

<p align="center">
  <img src="docs/assets/approvals.png" width="49%" alt="Approvals: the before and after the source application itself promised">
  <img src="docs/assets/context.png" width="49%" alt="Context: how MORPH read the goal, and what carried over">
</p>

---

## What it does, concretely

| | |
|---|---|
| **Discovers applications** | Each connector introspects a live schema: entities, field types, enum domains, declared operations with their parameters, effects, preconditions and risk. |
| **Builds a capability graph** | Schema only — no record values. Intra-application relationships come from the applications; cross-application links are declared by the operator, and MORPH traverses both. |
| **Interprets the goal** | A dedicated stage reads *what kind of work* is being asked for (review, analyse, monitor, act), which entities it is about and why, which filters the words imply — and says out loud what it could **not** cover, including an operation that exists but your role may not run. |
| **Plans an interface** | The goal, the graph and that reading go to the host model through `ctx.llm` with a JSON schema. The result is validated; validation errors are fed back for one repair attempt; a relevance-scoring composer is the floor, and runs unchanged with no model at all. |
| **Validates the plan** | `morph.ui.v1` can only name entities, fields, enum values, relationships and operations that exist. No field anywhere in the schema can hold markup, a URL or an expression. |
| **Renders something usable** | Metrics, sortable and searchable tables, charts, a kanban board grouped by a dimension the application lets you *change*, timelines, detail panes, forms, contextual actions and cross-application relation views. |
| **Resolves it live** | One pass, deduplicated reads, in-process aggregation, cross-application joins, per-block error isolation and a `degraded` flag when something is missing. |
| **Stays in sync both ways** | Each application publishes a change feed; MORPH keeps a cursor, so a refresh is one small call per application and it catches edits made **directly in the source system**, attributed to whoever made them. Signature diffing is the floor for sources with no feed. |
| **Executes, carefully** | Propose → human approves → write with `If-Match` and an idempotency key → re-read the record from the source → compare with what the operation promised. Anything less is reported as `unverified`, never as success. |
| **Keeps a tamper-evident log** | Every proposal, approval, rejection, failure and composition is hash-chained; the Activity view verifies the chain and shows the evidence behind each action. |

## Five surfaces

MORPH is one dashboard tab with five views, because they answer five different questions:

| View | Answers |
|---|---|
| **Workspace** | What am I working on? The composed interface, live. |
| **Connections** | What am I connected to, what can it do, and what may *I* do there? |
| **Approvals** | What needs a person? Each with the before/after the source itself promised. |
| **Activity** | What happened, with the evidence, and is the log intact? |
| **Context** | What did MORPH think I asked for, and what carried across the last transformation? |

---

## Quick start

```bash
git clone https://github.com/timioni001/morph-hermes
cd morph-hermes
```

MORPH has no Python dependencies of its own — it uses what Hermes already ships. To run
it outside Hermes (tests, the demo, the dashboard harness):

```bash
make install          # creates .venv and installs the dev extra
```

Every `make` target runs through `.venv/bin/python`, so it behaves the same whether or
not the virtual environment is activated in your current shell.

Then run the whole showcase in one command:

```bash
make demo
```

It starts three independent applications, composes a workspace from a goal, has the agent
propose an action, shows the agent refused when it tries to approve, has an operator
approve it, and then reads the change back out of the source application's own API. It
exits non-zero if any of that does not hold, so it is an acceptance test as much as a
demonstration. `make serve` leaves everything running and opens the dashboard.

### A recorded run

```bash
scripts/make_video.sh      # → .media/morph-demo.mp4  (~43s, needs ffmpeg + playwright)
```

Playwright drives a real browser through the whole sequence — compose, select,
propose, approve, then reload the Storefront's own page to show the change and the
version bump. The only thing added afterwards is the caption text, from timings the
recorder wrote while it ran. The file is not committed; the script regenerates it.

---

## Installing it into Hermes

```bash
# 1. put the plugin where Hermes looks for one
ln -s "$PWD" ~/.hermes/plugins/morph       # or: cp -r . ~/.hermes/plugins/morph

# 2. check it against the real runtime contracts
hermes plugins doctor ~/.hermes/plugins/morph --ci
hermes plugins validate ~/.hermes/plugins/morph

# 3. plugins are opt-in
hermes plugins enable morph

# 4. the dashboard tab is mounted when the dashboard starts
hermes dashboard
```

MORPH then appears as a **MORPH** tab in the Hermes web dashboard, and registers seven
tools in the `morph` toolset, three lifecycle hooks, a `/morph` slash command and a
`hermes morph` CLI subcommand.

Connect it to something. Out of the box it is wired to the three bundled demo
applications on loopback; `docs/CONNECTORS.md` covers pointing it at your own services,
and `config/connectors.default.json` is the file to copy.

### The tools Hermes gets

| Tool | What it does |
|---|---|
| `morph_discover` | Applications, entities, fields, operations, cross-application links, health, and this caller's permissions. |
| `morph_plan_workspace` | A goal becomes a validated workspace; an existing one is transformed, carrying context. |
| `morph_workspace_state` | The workspace with live values, pending approvals, failures and the audit tail. |
| `morph_refresh` | Re-read every source and report what moved. |
| `morph_inspect_record` | One record straight from its own application, with the operations that could be proposed against it. |
| `morph_propose_action` | Queue one write for a human. Applies nothing. |
| `morph_verify_action` | Re-read the record and say whether the change actually landed. |

There is deliberately no eighth tool. The agent cannot approve its own proposal, is never
handed the confirmation token an approval requires, and a `pre_tool_call` hook blocks any
tool in the `morph_` namespace that MORPH did not register — so the guarantee does not
depend on nobody ever adding one.

---

## From the terminal

```bash
hermes morph status                      # applications, workspace, pending approvals
hermes morph discover                    # the full capability graph and your permissions
hermes morph goal "delayed orders and the complaints about them"
hermes morph state                       # the workspace with live values
hermes morph proposals                   # what is waiting for a human
hermes morph approve <proposal-id>       # interactive only: refuses without a terminal
hermes morph verify <proposal-id>        # re-read the source and check
hermes morph refresh                     # what changed since the last read
hermes morph audit                       # the hash-chained log, verified
hermes morph doctor                      # configuration, reachability, storage, credentials
```

---

## How it is put together

```
morph/
  config.py       configuration, secret access, network policy
  registry.py     connector instances and health
  connectors/     the adapter contract, the HTTP adapter, an in-process fixture,
                  and the connect-time SSRF guard
  capability.py   the capability graph (schema only)
  planner.py      model planning, repair, and the deterministic composer
  uischema.py     morph.ui.v1 and its validator
  resolve.py      live resolution, aggregation, joins, change detection
  actions.py      principals, permissions, proposals, approval, verification
  store.py        MORPH's own SQLite: workspaces, revisions, proposals, audit chain
  workspace.py    revisions and context preservation
  engine.py       the façade every surface goes through
  tools.py        the Hermes tool handlers
  cli.py          hermes morph
dashboard/        manifest, FastAPI routes, and the plugin bundle (src/ → dist/)
apps/             three independent demo applications
demo/             the reproducible showcase
docs/             architecture, connectors, security, demo
```

`docs/ARCHITECTURE.md` explains the pipeline and the decisions behind it.

---

## Tests

```bash
make test        # 176 tests: unit, integration against the real services, security
make e2e         # 39 browser checks, Chromium, the real bundle and the real API
make bundle      # the committed bundle matches its sources
make validate    # the Hermes catalog admission checks
```

The integration tests start the three demo applications as real subprocesses on their own
ports and assert against their HTTP APIs, including: an approved action landing in the
source system, optimistic concurrency voiding a stale proposal, one application going
down without taking the workspace with it, an edit made **outside** MORPH being detected
and attributed, and change detection naming the record that moved. The security tests
cover the network policy, read-only connectors, retry and rate-limit behaviour, the
authority boundary, hostile schema text, and the refusal of anything the capability graph
does not contain.

---

## Security posture

The long version is `docs/SECURITY.md`. The short version:

- **An agent can propose; only a person approves.** Authority is carried on the principal,
  not in a request body, and the agent role cannot be granted approval by configuration.
- **Nothing from a model is executed.** A plan is data naming things that already exist.
- **Nothing from an application is an instruction.** Schema text is flattened and capped
  before it is stored, rendered or put in a prompt; record values never reach a prompt at
  all.
- **Writes are optimistic, idempotent and verified.** Version mismatch voids a proposal; a
  repeated key replays rather than reapplies; success means the record was re-read and
  matched.
- **Egress is narrow.** HTTPS only (loopback needs an explicit opt-in), an optional host
  allowlist, no redirects, response caps, and connect-time validation of the address
  actually dialled. A connector can be marked `read_only`, which no role overrides.
- **Retries are safe ones.** Reads retry with backoff; a write retries only because it
  always carries an idempotency key. `429` waits for `Retry-After`.
- **Credentials are Hermes's.** A connector names an environment variable; the value is
  read through `agent.secret_scope` and never logged, stored or returned.
- **Nothing is atomic across applications**, and MORPH never claims otherwise: a batch
  reports each item separately.

## Running it on WSL

`docs/WSL.md` is the exact command list, including the one check the cloud build could
not run: `hermes dashboard` with the MORPH tab inside the real Hermes web app.

## Limitations

- Per-user roles come from the dashboard session identity Hermes provides; with the
  dashboard on loopback and no auth gate engaged, every local caller is the configured
  default operator. Map identities to roles under `user_roles` when running with auth.
- The browser suite drives MORPH's real bundle and real API through a harness that
  implements the documented Hermes dashboard SDK. It is not the Hermes dashboard itself.
- The bundled applications are demonstration systems. They are real services with real
  databases and real HTTP APIs, which is what makes the integration demonstrable — they
  are not a storefront. The GitHub connector talks to the real GitHub API; it is driven
  by a fake transport in CI and by `scripts/check_github_connector.py` against a real
  repository.
- The GitHub connector offers only operations whose effect can be verified by re-reading
  the issue. Posting a comment is deliberately absent: its effect is an increment, so
  MORPH could not prove the change it promised.
- MORPH plans with whatever model Hermes is configured to use. Quality varies with it; the
  deterministic composer is the floor, not the ceiling.

## Licence

MIT. See `LICENSE`.
