# Running the offline sandbox

This exercises MORPH against the three bundled practice services. For the real thing,
`hermes morph connect github owner/name` — see the README.

```bash
python -m demo.run_demo            # ~15 seconds, prints every check
python -m demo.run_demo --serve    # ...then leaves everything up so you can look
```

No external service, no API key, nothing to sign up for. Everything runs on loopback in a
temporary directory that is removed afterwards.

## What it proves, step by step

| Step | What happens | Why it matters |
|---|---|---|
| 1 | Three applications start as separate processes with separate databases and separate bearer tokens | The integration is real, not a mock in one process |
| 2 | One order is read **directly from the Storefront's API** | The starting state, from the system of record |
| 3 | MORPH introspects all three schemas and builds the capability graph | Discovery, not configuration-by-hand |
| 4 | A goal in plain language becomes a workspace, resolved live | Metrics, a chart, tables, and a join across two applications |
| 5 | The **agent** proposes an action, then tries to approve it | The proposal changes nothing; the approval is refused |
| 6 | The **operator** approves; MORPH re-reads and verifies | Success means verified, not "the call returned 200" |
| 7 | The Storefront's own API and changelog are read again | The proof, from outside MORPH |
| 8 | The goal changes; the workspace transforms | Context carried over, earlier revision kept |
| 9 | A second action against a different application | The pattern is not special-cased to one source |
| 10 | Every source re-read; the audit chain verified | Change detection names the record that moved |

The script exits non-zero if any expectation fails, so it doubles as an acceptance test.

## Looking at it

With `--serve`, three source applications and the dashboard stay running:

- **http://127.0.0.1:8820/** — the MORPH workspace, served through the documented Hermes
  dashboard plugin contract
- **http://127.0.0.1:8811/** — the Storefront's own plain record viewer
- **http://127.0.0.1:8812/** — the Helpdesk's
- **http://127.0.0.1:8813/** — the Warehouse's

Flag an order in MORPH, approve it, then reload the Storefront's page. The change and the
version bump are there, in the application that owns the record. That is the whole claim.

## Driving it by hand

```bash
python -m apps.serve &                               # the three applications
eval "$(set -a; python -m apps.serve --print-tokens)" # their tokens into the environment

hermes morph goal "delayed orders and the complaints about them"
hermes morph state
hermes morph propose storefront.orders.flag_for_review ORD-2043 \
    --param reason="48h late, customer chasing"
hermes morph proposals
hermes morph approve <proposal-id>     # interactive; type the word 'approve'
hermes morph verify <proposal-id>
hermes morph refresh
hermes morph audit
```

Or inside a Hermes session, once the plugin is enabled:

> *"Use MORPH to build me a workspace for delayed orders and the complaints about them,
> then propose flagging the worst one for review."*

The agent will call `morph_discover`, `morph_plan_workspace` and `morph_propose_action`,
and then tell you that something is waiting for your approval — because that is the only
thing it can do.

## The browser suite

```bash
scripts/e2e.sh
```

Starts a clean stack, builds the bundle, and runs 24 Chromium checks against the real
dashboard bundle and the real API: that the plugin registers through the SDK contract,
that a goal composes a workspace whose numbers match what the Storefront reports, that
the cross-application join renders, that selecting a row retargets the detail pane and
the action form, that proposing changes nothing, that approving applies and verifies,
that the Storefront's own record and changelog show it, that a transformation preserves
the selection, that a denied operation is shown as denied, that a mutation without the
intent header is refused, and that phone and tablet layouts do not overflow.

Screenshots land in `.e2e-artifacts/`.

## The video

```bash
scripts/make_video.sh          # → .media/morph-demo.mp4
```

Needs `ffmpeg` and a Node `playwright` with Chromium. It brings up a clean stack,
records a real browser session at 1600×900, and burns in captions from the timings the
recorder logged as it went.

What is authentic: every frame is a real interaction with the real plugin bundle, the
real dashboard API and the three real applications. The approval really writes to the
Storefront; the Storefront's own page is then reloaded in the same browser, and the
review status and version you see change are that application's own response.

What is added: the caption strip, and a single `scrollIntoView` plus a background tint on
the row being pointed at, so a viewer can follow where to look. No frame is re-staged,
no result is simulated, and nothing is cut in a way that changes the order of events.

The MP4 is not committed — it is a 2–3 MB build artefact. Regenerate it with the command
above.
