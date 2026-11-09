---
name: triage
description: Work through a backlog in a service connected to MORPH — a repository's issues, or any queue behind an API — by reading the records that matter and leaving proposed changes for a human to approve.
---

# Triaging a backlog through MORPH

Use this when someone asks you to get on top of a queue — stale issues, unassigned
bugs, open tickets — in a service MORPH is connected to. You read freely. You change
nothing yourself: every change is a proposal a person approves.

## 1. Find out what you are connected to

Call `morph_discover` first, every time. It returns the real entity refs
(`<application>.<entity>`), the fields and their types, the enum values, the
relationships that cross application boundaries, and — importantly — which
operations *this* caller is allowed to propose, with the reason when not.

If nothing is connected, say so and give the operator the command:
`hermes morph connect github owner/name`. If a connection lists no operations, it is
read-only (for GitHub: no token) — tell them rather than proposing something that
will be refused.

Never guess an entity ref or an operation id. If `morph_discover` does not list it,
it does not exist, and a plan that names it will be rejected.

## 2. Compose the workspace, do not describe it

Call `morph_plan_workspace` with the operator's goal in their own words. MORPH
interprets it, plans an interface against the capability graph, validates the plan
and renders it in the dashboard. Tell the operator what appeared; do not paste the
schema back at them.

If a workspace already exists, this is a *transformation*: their selected record and
pinned filters carry over where the new workspace still shows them. The result says
what was carried and what was dropped — pass that on, it is the part people worry
about.

## 3. Read before you reason

`morph_workspace_state` gives you the resolved values. Two habits:

- Numbers in the result came from a read performed during that call. Do not restate
  an older number from earlier in the conversation as if it were current.
- `degraded: true` means an application did not answer. Say which one. Do not
  average over a hole and present the result as complete.

## 4. Narrow to the record that matters

Use `morph_inspect_record` for the authoritative state of one record, straight from
the application that owns it, with the operations that could be proposed against it.
Do this before proposing anything: the table you read may be seconds old, and the
proposal is built against the record's current version.

## 5. Propose, then stop

`morph_propose_action` queues one write. It does not apply it. You will not be given
the confirmation token, and there is no tool that approves — a person approves in the
MORPH dashboard.

So the right shape for your final message is: what you propose, to which record, what
it will change, why, and that it is waiting for them. Then stop. Do not say the work
is done.

## 6. Only claim it landed when it landed

After a human approves, `morph_verify_action` re-reads the record from the source
application and compares it with what the operation promised. `verified: true` is the
only basis for telling someone a change was made. If it comes back `unverified`, say
exactly that and show the mismatch.

## Several records

One proposal changes one record. For "close all the stale ones", propose each
separately, then give the operator one summary: how many, which, and that they are
waiting under Approvals. Do not propose changes to records you have not inspected.

## Worked example

> "Close the issues that are duplicates of #41 and assign the open bugs to me."

1. `morph_discover` → `widgets.issues` with `close_as_not_planned`, `set_labels`,
   `assign_to_me` available to this caller.
2. `morph_workspace_state` / `morph_inspect_record` → #44 and #52 describe the same
   crash as #41; #47 and #49 are labelled `bug` and unassigned.
3. `morph_propose_action(widgets, issues, "44", close_as_not_planned,
   reason="duplicate of #41")`, and the same for #52.
4. `morph_propose_action(widgets, issues, "47", assign_to_me, reason="open bug,
   unassigned")`, and the same for #49.
5. Tell the operator four changes are waiting, and what each will do. Stop.
6. Once they approve: `morph_verify_action` on each → `verified: true`, then say so.

`set_labels` replaces the whole list. To add one label, read the current labels with
`morph_inspect_record` and send the full list including the new one.
