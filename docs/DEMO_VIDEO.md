# Demo video brief — under 90 seconds

The rubric is **usefulness, reliability, polish and creativity**, and judges favour a
plugin that does more than a prompt or a skill could. So the video has one job:

> prove that a goal became a working interface over several real applications, and
> that an approved action actually changed one of them.

Everything else is decoration. The single most persuasive frame in the whole video is
the **Storefront's own page showing the record changed** — not MORPH's own UI
claiming it did.

---

## Setup before you hit record

```bash
cd ~/morph-hermes
pkill -f apps.serve; pkill -f harness.serve
# A clean slate without deleting anything: point both data directories at fresh
# temporary ones for this run only.
MORPH_DEMO_DATA_DIR="$(mktemp -d)" MORPH_DATA_DIR="$(mktemp -d)" \
  python -m demo.run_demo --serve
```

Two browser windows, side by side or tabbed:

1. **http://localhost:8820** — MORPH
2. **http://localhost:8811** — the Storefront's own page, scrolled to `ORD-2045`

Set the browser to 1440×900 or larger. Hide bookmarks. Light mode.

Or just run `scripts/make_video.sh`, which drives all of this with Playwright and
burns in captions — then re-record by hand only if you want your own voice on it.

---

## Shot list

| # | Time | On screen | Say |
|---|---|---|---|
| 1 | 0:00–0:08 | Storefront page, `ORD-2045`, `review_status = none`, `v1` | "Three separate applications. This is the Storefront's own record list — order 2045, no review, version 1." |
| 2 | 0:08–0:14 | MORPH, Connections view | "MORPH is connected to all three. It has no layouts of its own — it reads their schemas." |
| 3 | 0:14–0:26 | Workspace view; type the goal; hit Transform | "I give it a goal in plain language: work through delayed orders and the complaints about them." |
| 4 | 0:26–0:38 | Scroll: metrics → board → queue → the joined table | "It composed this. Live counts, a triage board, and orders joined to their support cases — across two different applications." |
| 5 | 0:38–0:46 | Click row `ORD-2045`; detail and form retarget | "Picking a record drives the detail pane and targets the action." |
| 6 | 0:46–0:56 | Click **Flag for review**, type a reason, **Propose for approval** | "The agent can propose. It cannot approve, and it is never given the token an approval needs." |
| 7 | 0:56–1:06 | Approvals view; the before/after card; **Approve & apply** | "A person approves — and sees exactly what the source application promised to change." |
| 8 | 1:06–1:14 | Toast: *Applied and verified* | "MORPH writes it, then re-reads the record and checks it against what was promised." |
| 9 | 1:14–1:24 | **Switch to the Storefront page and reload** | "And there it is, in the Storefront's own system. Flagged, with the reason, version 2." |
| 10 | 1:24–1:30 | Back to MORPH; type the second goal; the workspace transforms | "Change the goal and the workspace changes with it — and your selection carries over." |

If you are tight on time, cut shot 2 and shorten shot 4. **Never cut 1, 7, 8 or 9** —
they are the proof.

---

## Lines worth saying out loud

- *"The agent cannot approve. There is no tool that approves."*
- *"Verified means MORPH re-read the record from the application that owns it."*
- *"That is the Storefront's own page, not MORPH's."*

## Things to avoid

- Don't narrate the architecture. Show the change landing.
- Don't say "AI-powered". Everyone says it; the differentiator is the approval and
  the verification.
- Don't show the terminal for more than a second or two.
- Don't speed up the approval step. It is the point.

---

## Caption for the X post

> MORPH — software that adapts to you.
>
> A Hermes plugin that turns several independent applications into one workspace
> shaped around your goal, then lets you act on them: the agent proposes, a human
> approves, and MORPH verifies the change by reading it back from the source system.
>
> Three real services, real HTTP APIs, real writes. 161 tests + 39 browser checks.
>
> @NousResearch
