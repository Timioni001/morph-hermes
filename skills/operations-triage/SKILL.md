---
name: operations-triage
description: Work a backlog that spans several connected applications with MORPH — compose a workspace for it, find the records that matter, and leave a proposed action for a human to approve.
---

# Triaging work across connected applications

Use this when someone asks you to get on top of a queue — delayed orders, open
complaints, stock running out — and the answer lives in more than one system.

## 1. Find out what you are connected to

Call `morph_discover` first, every time. It returns the real entity refs
(`<application>.<entity>`), the fields and their types, the enum values, the
relationships that cross application boundaries, and — importantly — which
operations *this* caller is allowed to propose, with the reason when not.

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

## Worked example

> "Get on top of the delayed orders and the complaints about them."

1. `morph_discover` → `storefront.orders`, `helpdesk.cases`, a link
   `helpdesk.cases.order_ref → storefront.orders.id`, and `flag_for_review`,
   `mark_followed_up`, `escalate_case` available to this caller.
2. `morph_plan_workspace("delayed orders and the complaints about them")`.
3. `morph_workspace_state` → 5 delayed orders, 3 with an open case.
4. `morph_inspect_record("storefront", "orders", "ORD-2045")` → 75 hours late, no
   review flag, version 1.
5. `morph_propose_action(... flag_for_review, reason="75h late with an open complaint")`.
6. Tell the operator it is waiting. Stop.
7. Once they approve: `morph_verify_action` → `verified: true`, then say so.
