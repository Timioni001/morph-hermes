---
name: verified-change
description: The discipline MORPH enforces around changing someone else's system — propose, let a human approve, then verify from the source before claiming anything happened.
---

# Saying a change happened, and being right

A tool call returning 200 is not the same as the outcome the operator wanted. MORPH
is built around that gap. This skill is the reasoning that goes with it.

## The three states, and the words for them

| State | What is true | What to say |
|---|---|---|
| **Proposed** | A validated action is stored, nothing has been written | "Waiting for your approval." |
| **Applied** | The source accepted the write and the read-back matched the promised effect | "Done — and I checked the record in <application>." |
| **Unverified** | The source accepted the write, the read-back did not match | "The write was accepted but the record does not show it. Here is the mismatch." |

Never collapse the third into the second. An unverified action is a fact about the
world that someone needs to know.

## Why you cannot approve

You can call `morph_propose_action`. You cannot approve it, you are not handed the
confirmation token, and a `pre_tool_call` hook blocks anything in the `morph_`
namespace that MORPH did not register. This is deliberate and it is not an obstacle
to route around: the value of the proposal is that a person looked at it.

If you find yourself reaching for a shell command, another plugin, or a second
account to get an action applied, stop — that is the failure mode the design exists
to prevent, and you should say that approval is pending instead.

## Concurrency, in plain terms

A proposal records the record's version. If the record moves before approval, the
proposal becomes `stale` and nothing is written. That is correct behaviour, not an
error to retry blindly: something changed, and the operator should see what.

Re-read with `morph_inspect_record`, then propose again if it still makes sense.

## Retries

Every proposal owns one idempotency key, passed to the source. A retried approval
replays rather than reapplying, and proposing twice with the same key returns the
existing proposal. So: if you are unsure whether a proposal went through, call
`morph_propose_action` again with the same `idempotency_key` rather than creating a
second one.

## Across applications

There is no transaction. A batch of actions is a sequence of independent writes, and
the result reports each one. If three of five applied, say three of five applied and
name the two that did not — do not describe it as partially successful and move on.
