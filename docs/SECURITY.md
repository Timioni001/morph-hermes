# Security notes

MORPH reads from and writes to systems of record on behalf of an agent. This is what it
does about that, and what it does not claim.

## The threat model

| Adversary | Concern |
|---|---|
| A language model | Fabricating a workspace, inventing fields or operations, trying to act without a human |
| A connected application | Returning hostile schema text or record values that read as instructions |
| A compromised or misconfigured connector target | Pulling MORPH at an internal address (SSRF) |
| Another page in the operator's browser | Driving the dashboard cross-site |
| A second plugin | Introducing a tool MORPH's model would trust |
| Concurrency and retries | Double-applying a write, or applying one against a record that moved |

Not in scope: Hermes's own authentication (MORPH trusts the dashboard gate), the
operator's machine, or an operator who deliberately approves something harmful — MORPH's
job there is to show them exactly what they are approving.

## The approval boundary

Four independent mechanisms, because one is a single point of failure:

1. **Authority on the principal.** `approve()` refuses an `AGENT` principal before
   anything else. Authority is never a request parameter.
2. **Configuration cannot grant it.** `morph/config.py` forces `can_approve: false` on
   the agent role even when a configuration file sets it true.
3. **The token is withheld.** An approval needs a per-proposal confirmation token,
   compared in constant time. It is issued only to operator-authority callers, so an
   agent that learns a proposal id still cannot approve it.
4. **The namespace is closed.** A `pre_tool_call` hook blocks any tool named `morph_*`
   that MORPH did not register, so another plugin cannot introduce `morph_approve`.

The interactive CLI is the only other approval path. It refuses without a TTY and
requires the word `approve` to be typed, so an agent with shell access cannot use it as a
side door.

`tests/security/test_authority.py` asserts each of these, and greps the tree to confirm
approval has exactly three call sites.

## Untrusted model output

A plan is **data naming things that already exist**. The validator checks every entity,
field, enum value, relationship and operation against the capability graph, coerces
filter values to declared types, and rejects the whole plan with a list of reasons
otherwise. Nothing in `morph.ui.v1` can hold markup, a URL, a handler or an expression —
`tests/unit/test_uischema.py` scans the schema's property names to keep it that way.

The renderer has no `innerHTML`, no `dangerouslySetInnerHTML`, no data-derived `href` and
no `eval`; `tests/unit/test_packaging.py` scans the built bundle for all of them.

## Untrusted application content

- **Schema text is flattened and capped** when the descriptor is parsed: control
  characters become spaces, zero-width and bidi-control characters are deleted (they
  exist to make text render differently from how it is stored), and labels and
  descriptions are length-capped.
- **Record values never reach a prompt.** The planner receives the capability graph,
  which is schema only. A hostile value in a support ticket cannot reach the model
  through MORPH's planning path at all.
- **A source cannot waive approval.** `requires_approval` is forced true regardless of
  what the application publishes.
- **A source cannot rename itself.** The connector id comes from configuration.
- **An operation cannot write a field the entity does not publish**, and an effect
  template cannot contain anything but literals and `{param}` placeholders.

## Network

- HTTPS only; a loopback address needs `allow_loopback: true`, which exists for the
  bundled demo applications and is off by default.
- Optional `allowed_hosts`.
- Private, link-local, reserved, multicast and cloud-metadata targets are refused, and
  `tools.url_safety.is_safe_url` (Hermes's own published policy) is consulted as well
  when MORPH runs inside an agent.
- Credentials in a URL, a query or a fragment are refused.
- Redirects are never followed — a redirect is how an allowed host hands a client to one
  that was never allowed.
- Responses are capped at 2 MB and record counts at the connector's `max_records`.
- **Connect-time validation.** Validating a URL before a request leaves a DNS-rebinding
  window. `morph/connectors/netguard.py` resolves the hostname, checks every answer
  against the policy, and dials a validated address while keeping the original hostname
  for `Host`, SNI and certificate verification. If httpx's internals ever move, the guard
  reports that it could not attach and the connector logs a warning, falling back to
  per-request URL validation.

Hermes has its own guarded client, but it is not part of the published plugin API
(catalog rule 9) and it blocks loopback by design, so MORPH carries an equivalent of its
own. The narrowing is per connector and never relaxes anything for the rest of Hermes.

## Credentials

A connector names an environment variable; the value is read at call time through
`agent.secret_scope.get_secret`, which honours the active Hermes profile. MORPH never
reads `.env` files, never reads Hermes's `auth.json`, never stores a token, never returns
one through an API or a tool result, and never logs one. The variable name must be
`UPPER_SNAKE_CASE`; a connector that names one with no value set reports itself
unreachable with a message saying which variable to set.

## The dashboard API

Hermes authenticates these routes and refuses them when the plugin is disabled. On top of
that:

- Every mutating route requires `X-MORPH-Request: 1`, a header a cross-origin form or
  image cannot set without a CORS preflight the backend never answers.
- A request the browser marks `Sec-Fetch-Site: cross-site` is refused.
- Request bodies are bounded by pydantic models; operation parameters are validated
  server-side against the source's declared schema regardless of what the UI sent.
- Per-principal token buckets limit proposals and approvals.
- Handlers run off the event loop, so a slow source cannot stall the dashboard.

## Writes

- **Optimistic concurrency.** A proposal records the record's version; if it moved by
  approval time the proposal becomes `stale` and nothing is written.
- **Idempotency.** Each proposal owns one key, passed to the source. A retried approval
  replays rather than reapplies, and a repeated proposal with the same key returns the
  existing proposal.
- **Read-back verification.** After the write MORPH re-reads the record and compares it
  field by field with the operation's declared effect. A mismatch is recorded as
  `unverified` and raised; it is never reported as success.
- **No cross-application atomicity.** A batch is a sequence of independent writes.
  `atomic: false` is in the response, and partial results are reported per item.

## Audit

Every proposal, approval, rejection, failure, expiry, workspace composition and agent
tool call is appended to a hash-chained log: each entry hashes its body together with the
previous entry's hash. `verify_audit_chain()` recomputes the chain and names the first
entry that does not follow, so a deleted or edited row is detectable. The dashboard shows
the verdict; `hermes morph audit` prints it.

The log is tamper-*evident*, not tamper-proof: anyone who can write the SQLite file can
rewrite the whole chain. Treat it as detection for accidents and single-row edits, not as
a defence against an attacker with the file.

## Reporting

Open an issue, or email the maintainer listed in `catalog-entry.yaml`. Please do not file
a public issue for anything that would let an agent approve its own action.
