# Demo video brief — under 90 seconds

One sentence to land:

> My agent can act on my real GitHub repository, but it cannot act alone: it proposes,
> I approve, and MORPH proves the change by reading it back.

The most persuasive frame is **github.com itself showing the issue changed**, not
MORPH's own UI claiming it did.

---

## Setup before you hit record

A scratch repository with four or five open issues — two obvious duplicates, one bug
with no assignee. A `GITHUB_TOKEN` for it in your Hermes environment.

```bash
hermes morph connect github your-name/scratch-repo
```

```bash
hermes dashboard
```

Two browser tabs: the Hermes dashboard on **MORPH**, and the repository's Issues page
on github.com. A terminal with `hermes` chat open. 1440×900 or larger, bookmarks hidden.

---

## Shot list

| # | Time | On screen | Say |
|---|---|---|---|
| 1 | 0:00–0:08 | github.com, the open issues | "This is a real repository. I want my agent to triage it — but I don't want to hand it the keys." |
| 2 | 0:08–0:16 | Terminal: `hermes morph connect github …` → `[connected]` and the operation list | "One command connects it. These are the only changes that exist, and each one states exactly what it will set." |
| 3 | 0:16–0:30 | Hermes chat: *"close the duplicates of #1 as not planned and assign the open bug to me"* → the agent's reply | "The agent reads the issues and proposes. It says so: nothing is applied yet." |
| 4 | 0:30–0:36 | github.com, reload — nothing changed | "And nothing has changed." |
| 5 | 0:36–0:52 | MORPH → **Approvals**: the cards with before/after | "Here is each change, before and after. There is no tool the agent could call to approve these." |
| 6 | 0:52–1:02 | **Approve & apply** on each; toast *Applied and verified* | "I approve. MORPH writes it, then reads the issue back from GitHub and compares." |
| 7 | 1:02–1:12 | **github.com, reload** — issues closed as not planned, bug assigned | "There it is, on GitHub." |
| 8 | 1:12–1:22 | MORPH → **Activity**: the chain, *intact* | "And a tamper-evident record of who approved what." |
| 9 | 1:22–1:30 | Terminal: `hermes morph connect openapi https://…` | "It is not GitHub-specific — anything with an OpenAPI document connects the same way." |

If you are tight on time, cut 8 or 9. **Never cut 4, 5, 6 or 7** — they are the proof.

Shot 3 needs a model configured in Hermes (`hermes setup --portal`). Without one,
propose from the dashboard instead: select an issue in the Workspace view and use its
action — the approval and verification shots are identical.

No GitHub token to hand? `hermes morph connect sandbox && hermes morph up` gives the
same loop against three offline practice services, with the service's own page at
<http://localhost:8811> standing in for github.com.

---

## A ready-made cut

`scripts/make_video.sh` records a real browser session against the offline sandbox
and writes `.media/morph-demo.mp4`: about a minute, captioned, and narrated if a Piper
voice model is present (free, runs locally). It shows the sandbox, not GitHub — use it
as is, or as the fallback if you cannot record the GitHub cut above.

---

## Lines worth saying out loud

- *"The agent cannot approve. There is no tool that approves."*
- *"Verified means MORPH re-read the issue from GitHub."*
- *"That is github.com, not MORPH."*

## Things to avoid

- Don't narrate the architecture. Show the change landing.
- Don't say "AI-powered". The differentiator is the approval and the verification.
- Don't speed up the approval step. It is the point.

---

## Caption for the X post

> MORPH — let your Hermes agent act on your APIs without letting it act alone.
>
> Connect a GitHub repo (or anything with an OpenAPI doc). The agent can only propose
> changes. You approve each one, seeing the before and after. MORPH then reads the
> record back to prove it landed.
>
> hermes morph connect github owner/name
>
> @NousResearch
