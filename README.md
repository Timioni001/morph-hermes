# MORPH

**Software that adapts to you.**

MORPH is a native [Hermes Agent](https://hermes-agent.nousresearch.com) plugin that turns
several independent applications into one workspace shaped around what you are trying to
get done — and lets you act on them from that workspace, with a human approving every
write and the result verified in the application that owns the record.

Tell it *"work through delayed orders and the complaints about them"*. MORPH reads the
schemas of the applications it is connected to, works out which entities, fields and
operations that goal is about, composes an interface from them, fills it with data read
live from each application, joins records across application boundaries, and offers only
the actions your role is actually permitted to take. Change the goal and the workspace
changes with it, carrying your selection and your filters over.

It is not a dashboard builder, a chat wrapper or a set of preset layouts. There is no
layout in this repository. Every workspace is composed at request time from whatever the
connected applications happen to publish.

---

## What it does, concretely

| | |
|---|---|
| **Discovers applications** | Each connector introspects a live schema: entities, field types, enum domains, declared operations with their parameters, effects, preconditions and risk. |
| **Builds a capability graph** | Schema only — no record values. Intra-application relationships come from the applications; cross-application links are declared by the operator, and MORPH traverses both. |
| **Plans an interface** | The goal plus the graph go to the host model through `ctx.llm` with a JSON schema. The result is validated; validation errors are fed back for one repair attempt; a relevance-scoring composer is the floor, and runs unchanged with no model at all. |
| **Validates the plan** | `morph.ui.v1` can only name entities, fields, enum values, relationships and operations that exist. There is no field anywhere in the schema that can hold markup, a URL or an expression. |
| **Resolves it live** | One pass, deduplicated reads, in-process aggregation, cross-application joins, per-block error isolation and a `degraded` flag when something is missing. |
| **Detects change** | Each read yields a signature over record ids and versions, so the next pass reports exactly what was added, removed or modified. |
| **Executes, carefully** | Propose → human approves → write with `If-Match` and an idempotency key → re-read the record from the source → compare with what the operation promised. Anything less is reported as `unverified`, never as success. |
| **Keeps a tamper-evident log** | Every proposal, approval, rejection, failure and composition is hash-chained; `hermes morph audit` verifies the chain. |

---

## Quick start

```bash
git clone https://github.com/timioni001/morph-hermes
cd morph-hermes
```

MORPH has no Python dependencies of its own — it uses what Hermes already ships. To run
it outside Hermes (tests, the demo, the dashboard harness):

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

Then run the whole showcase in one command:

```bash
python -m demo.run_demo
```

It starts three independent applications, composes a workspace from a goal, has the agent
propose an action, shows the agent refused when it tries to approve, has an operator
approve it, and then reads the change back out of the source application's own API. It
exits non-zero if any of that does not hold, so it is an acceptance test as much as a
demonstration. Add `--serve` to leave everything running and open the dashboard.

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
pytest                      # 141 tests: unit, integration against the real services, security
scripts/e2e.sh              # 24 browser checks, Chromium, the real bundle and the real API
node scripts/build_dashboard.mjs --check   # the committed bundle matches its sources
hermes plugins validate .   # the catalog admission checks
```

The integration tests start the three demo applications as real subprocesses on their own
ports and assert against their HTTP APIs, including: an approved action landing in the
source system, optimistic concurrency voiding a stale proposal, one application going
down without taking the workspace with it, and change detection naming the record that
moved. The security tests cover the network policy, the authority boundary, hostile
schema text, and the refusal of anything the capability graph does not contain.

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
  actually dialled.
- **Credentials are Hermes's.** A connector names an environment variable; the value is
  read through `agent.secret_scope` and never logged, stored or returned.
- **Nothing is atomic across applications**, and MORPH never claims otherwise: a batch
  reports each item separately.

## Limitations

- Per-user roles come from the dashboard session identity Hermes provides; with the
  dashboard on loopback and no auth gate engaged, every local caller is the configured
  default operator. Map identities to roles under `user_roles` when running with auth.
- The browser suite drives MORPH's real bundle and real API through a harness that
  implements the documented Hermes dashboard SDK. It is not the Hermes dashboard itself.
- The bundled applications are demonstration systems. They are real services with real
  databases and real HTTP APIs, which is what makes the integration demonstrable — they
  are not a storefront.
- MORPH plans with whatever model Hermes is configured to use. Quality varies with it; the
  deterministic composer is the floor, not the ceiling.

## Licence

MIT. See `LICENSE`.
