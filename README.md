# MORPH

**Software that adapts to you.**

MORPH is a plugin for [Hermes Agent](https://hermes-agent.nousresearch.com). It turns the
applications you use into one adaptive workspace, and lets the agent act in them without
acting alone:

1. **The agent proposes.** It can read everything, but a change is only ever a proposal.
2. **You approve.** Each proposal shows the exact before and after.
3. **MORPH verifies.** It writes the change, reads the record back from the API, and
   reports it verified only if the two match.

![MORPH inside the Hermes dashboard](docs/assets/morph-in-hermes.png)

<sub>Verified on Hermes Agent v0.23.0 · 243 tests · 39 browser checks · MIT</sub>

## Install

```bash
git clone https://github.com/Timioni001/morph-hermes ~/.hermes/plugins/morph
```

```bash
hermes plugins enable morph
```

## Connect something

A GitHub repository:

```bash
hermes morph connect github owner/name
```

Any service that publishes an OpenAPI document:

```bash
hermes morph connect openapi https://api.example.com --token-env EXAMPLE_TOKEN
```

Three offline practice services, no account needed:

```bash
hermes morph connect sandbox && hermes morph up
```

`connect` checks that the service answers before saving anything. MORPH stores the *name*
of the environment variable that holds a token, never the token. A public GitHub
repository connects read-only when no `GITHUB_TOKEN` is set.

## Use it

Open the dashboard and pick **MORPH** under *Plugins*:

```bash
hermes dashboard
```

Then ask the agent for something, for example:

> Triage the open issues. Close the duplicates as not planned and assign the bugs to me.

The agent reads the issues and queues its changes under **Approvals**. Nothing is applied
until you approve it there, or in a terminal with `hermes morph approve <id>`.

| View | What it shows |
|---|---|
| **Workspace** | An interface composed for your goal from live data: metrics, tables, boards, charts |
| **Connections** | What is connected, and what you may change there |
| **Approvals** | Proposed changes waiting for a person, each with its before and after |
| **Activity** | Everything that happened, in a tamper-evident log |
| **Context** | How MORPH read your goal, and what carried over when it changed |

Workspaces are planned by the model Hermes is configured with. With no model, or when a
model's plan does not validate, a built-in composer is used instead and the workspace says
so.

### Commands

```bash
hermes morph status              # connections, workspace, pending approvals
hermes morph connections         # what is connected and what may be proposed
hermes morph goal "…"            # compose a workspace for a goal
hermes morph proposals           # what is waiting for a person
hermes morph approve <id>        # interactive terminal only
hermes morph reject <id>
hermes morph audit               # the log, with its chain verified
hermes morph doctor              # configuration, reachability, credentials
hermes morph disconnect <id>
```

### What the agent gets

Seven tools: `morph_discover`, `morph_plan_workspace`, `morph_workspace_state`,
`morph_refresh`, `morph_inspect_record`, `morph_propose_action`, `morph_verify_action`.
There is no tool that approves.

## What can be changed

MORPH only offers an operation when it can check the result afterwards.

| Connection | Operations |
|---|---|
| **GitHub issues** | close, close as not planned, reopen, set labels, set title, assign to me, unassign |
| **OpenAPI** | derived from the schema: one per enum value, a set/clear pair per boolean, one per other writable field |

Posting a comment is deliberately missing: its effect is an addition, so there is nothing
exact to verify. Labels are set as a whole list for the same reason.

## Safety

- **Only a person can approve.** The agent is never given the confirmation token, and a
  hook blocks any `morph_` tool MORPH did not register.
- **Every write is verified** by re-reading the record. Anything else is reported as
  `unverified`, never as success.
- **Credentials stay in Hermes.** Tokens are read through Hermes's secret scope at call
  time and are never logged, stored or returned. MORPH never reads `.env` or `auth.json`.
- **Network.** MORPH calls only the services you connect (including `api.github.com` for
  a repository). Hosts are allowlisted; private, loopback and cloud-metadata addresses are
  refused unless you opt in; redirects are never followed.
- **Model.** Planning sends the model schema only, never record values.
- **Background processes.** None, unless you connect the sandbox and run
  `hermes morph up`, which starts its three services on loopback.
- **Storage.** Connections, workspaces, proposals and the audit log live in the plugin's
  own data directory. No telemetry.

## Verified

- **Live on GitHub** (9 November 2026, [`Timioni001/morph-demo`](https://github.com/Timioni001/morph-demo/issues)):
  a Hermes agent proposed four changes, a person approved them in the dashboard, and MORPH
  applied and verified each one. The result was confirmed separately through the GitHub API
  and on github.com.
- **Tests:** 243 Python tests (unit, integration, security) and 39 browser checks.
- **Hermes:** `hermes plugins doctor` and `hermes plugins validate --install-deps` pass in
  CI against Hermes v0.23.0.
- **Not run live:** `set_labels` and `unassign` on GitHub, and writes through the OpenAPI
  adapter. These are covered by tests only.

## Limitations

- Plan quality follows the model. Small models produce simple workspaces.
- The GitHub connection covers issues, with pull requests appearing as issues.
- The OpenAPI adapter has no change feed, and its conflict detection depends on the
  service providing an `ETag` or a version field.
- With the dashboard on loopback and no sign-in, every local user is the default operator.

## Development

```bash
make install    # create .venv with the dev tools
make test       # Python tests
make e2e        # browser checks
make demo       # the whole loop against the offline sandbox
make validate   # Hermes plugin checks
```

More detail: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md),
[`docs/CONNECTORS.md`](docs/CONNECTORS.md), [`docs/SECURITY.md`](docs/SECURITY.md),
[`docs/WSL.md`](docs/WSL.md).

## Licence

MIT. See [`LICENSE`](LICENSE).
