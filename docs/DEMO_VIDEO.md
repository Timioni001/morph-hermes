# The demo film

85 seconds, 1920×1080. Everything on screen that looks like the product is the product:
a real Hermes agent, the real MORPH tab in the Hermes dashboard, a real GitHub
repository, and github.com itself before and after. Motion graphics, captions, narration
and sound are added around that footage; nothing in it is staged or simulated.

## What was captured

| Footage | How |
|---|---|
| The agent proposing | `hermes chat` against `Timioni001/morph-demo`; the terminal card shows its real tool calls and reply, abridged |
| The MORPH tab | `video/capture/dashboard.cjs` — Playwright drives the Hermes dashboard; the approvals it clicks really write to GitHub |
| github.com | `video/capture/github.cjs` — logged-out screenshots before and after the approvals |
| OpenAPI | the adapter's real output for the public Swagger Petstore |

The dashboard take can only be recorded once per set of proposals, because approving
them changes the repository. `--rehearse` runs the same script without approving.

## Rebuilding it

```bash
node video/capture/github.cjs --tag before && node video/capture/dashboard.cjs && node video/capture/github.cjs --tag after
```

```bash
cd video/film && npm install && npx remotion render src/index.jsx Morph out/morph.mp4
```

Captures go to `video/footage/` and are copied into `video/film/public/` with the
narration (`narration.json`, synthesised locally with Piper) and the sound beds. None of
those are committed.

## Caption for the X post

> MORPH — software that adapts to you.
>
> One adaptive workspace across your applications for Hermes Agent. The agent proposes,
> you approve the exact before and after, and MORPH verifies every change by reading it
> back. Shown here on a real GitHub repository.
>
> @NousResearch
