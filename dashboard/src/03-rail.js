
  /* ── side rail: approvals, revisions, sources, audit ─────────────────────
   *
   * The approvals panel is the authority boundary made visible. It shows the
   * before/after the source application itself promised, who asked, and the risk —
   * then asks a person. Nothing else in MORPH can apply a change.
   */

  function diffRows(before, predicted) {
    var keys = Object.keys(predicted || {});
    return keys.map(function (key) {
      return {
        field: key,
        from: before && key in before ? before[key] : null,
        to: predicted[key]
      };
    });
  }

  function show(value) {
    if (value === null || value === undefined || value === "") { return "—"; }
    if (typeof value === "boolean") { return value ? "Yes" : "No"; }
    var text = String(value);
    return text.length > 90 ? text.slice(0, 89) + "…" : text;
  }

  function ApprovalCard(props) {
    var proposal = props.proposal;
    var busy = useState(false);
    var error = useState("");

    function run(action) {
      error[1]("");
      busy[1](true);
      action().catch(function (err) { error[1](errorText(err)); })
        .then(function () { busy[1](false); });
    }

    return h("div", { className: cx("mo-approval", "risk-" + proposal.risk) },
      h("div", null,
        h("div", { className: "mo-approval-op" }, proposal.operation_ref.split(".").pop()
          .replace(/_/g, " ")),
        h("div", { className: "mo-approval-meta" },
          proposal.entity_ref, " · ", proposal.record_id)),
      h("div", { className: "mo-diff" }, diffRows(proposal.before, proposal.predicted)
        .map(function (row) {
          return h("div", { key: row.field },
            h("div", { className: "mo-diff-field" }, titleCase(row.field)),
            h("div", { className: "mo-diff-row" },
              h("span", { className: "mo-diff-from" }, show(row.from)),
              h(Icon, { name: "morph", size: 13 }),
              h("span", { className: "mo-diff-to" }, show(row.to))));
        })),
      h("div", { className: "mo-approval-meta" },
        "asked by ", proposal.requested_by, " (", proposal.authority, ")",
        proposal.risk === "high" ? h("span", null, " · ", h(Tag, { tone: "bad" }, "high risk")) : null),
      proposal.reason ? h("div", { className: "mo-approval-meta" }, "“", proposal.reason, "”") : null,
      error[0] ? h("div", { className: "mo-error", role: "alert" }, error[0]) : null,
      h("div", { className: "mo-approval-actions" },
        h(Button, {
          variant: "primary", small: true, icon: "check", busy: busy[0],
          onClick: function () { run(function () { return props.onApprove(proposal); }); }
        }, "Approve & apply"),
        h(Button, {
          variant: "danger", small: true, icon: "close", disabled: busy[0],
          onClick: function () { run(function () { return props.onReject(proposal); }); }
        }, "Reject")),
      h("span", { className: "mo-approval-meta" },
        "Applying writes to ", proposal.source, ", then MORPH re-reads the record to verify it."));
  }

  function ApprovalsPanel(props) {
    var pending = props.proposals || [];
    return h("div", { className: "mo-panel" },
      h("div", { className: "mo-panel-head" },
        h("h2", { className: "mo-panel-title" }, "Awaiting your approval"),
        pending.length
          ? h(Tag, { tone: pending.some(function (p) { return p.risk === "high"; }) ? "bad" : "info" },
            String(pending.length))
          : null),
      h("div", { className: "mo-panel-body" },
        pending.length
          ? pending.map(function (proposal) {
            return h(ApprovalCard, {
              key: proposal.id, proposal: proposal,
              onApprove: props.onApprove, onReject: props.onReject
            });
          })
          : h(Empty, {
            icon: "shield", title: "Nothing is waiting",
            text: "Actions the agent or you propose appear here. MORPH never applies one on its own."
          })));
  }

  function DecisionsPanel(props) {
    var decisions = (props.decisions || []).slice(0, 6);
    if (!decisions.length) { return null; }
    return h("div", { className: "mo-panel" },
      h("div", { className: "mo-panel-head" },
        h("h2", { className: "mo-panel-title" }, "Recent decisions")),
      h("div", { className: "mo-panel-body" }, decisions.map(function (item) {
        var toneName = item.status === "applied" ? "good"
          : (item.status === "rejected" ? "" : "bad");
        return h("div", { key: item.id, className: "mo-revision" },
          h(Tag, { tone: toneName }, item.status),
          h("span", { className: "mo-revision-goal", title: item.operation_ref },
            item.operation_ref.split(".").slice(1).join(".") + " · " + item.record_id),
          item.status === "applied"
            ? h(Button, {
              small: true, variant: "ghost", icon: "shield",
              title: "Re-read the record from its own application and verify",
              onClick: function () { props.onVerify(item); }
            }, "Verify")
            : null);
      })));
  }

  function RevisionsPanel(props) {
    var revisions = props.revisions || [];
    if (revisions.length < 2) { return null; }
    return h("div", { className: "mo-panel" },
      h("div", { className: "mo-panel-head" },
        h("h2", { className: "mo-panel-title" }, "Workspace history"),
        h("span", { className: "mo-card-sub" }, revisions.length + " revisions")),
      h("div", { className: "mo-panel-body" },
        h("div", { className: "mo-revisions" }, revisions.slice(0, 8).map(function (revision) {
          var isCurrent = revision.revision === props.current;
          return h("div", { className: "mo-revision", key: revision.revision },
            h("span", { className: "mo-revision-n" }, "r" + revision.revision),
            h("span", { className: "mo-revision-goal", title: revision.goal }, revision.goal),
            isCurrent
              ? h(Tag, { tone: "info" }, "current")
              : h(Button, {
                small: true, variant: "ghost", icon: "history",
                onClick: function () { props.onRestore(revision.revision); }
              }, "Restore"));
        }))));
  }

  function AuditPanel(props) {
    var open = useState(false);
    var entries = props.audit || [];
    var chain = props.chain || {};
    return h("div", { className: "mo-panel" },
      h("div", { className: "mo-panel-head" },
        h("h2", { className: "mo-panel-title" }, "Audit log"),
        h("div", { style: { display: "flex", gap: "6px", alignItems: "center" } },
          h(Tag, { tone: chain.ok ? "good" : "bad" },
            chain.ok ? "chain intact" : "chain broken"),
          h(Button, {
            small: true, variant: "ghost",
            "aria-expanded": open[0],
            onClick: function () { open[1](!open[0]); }
          }, open[0] ? "Hide" : "Show"))),
      open[0]
        ? h("div", { className: "mo-panel-body" },
          h("div", { className: "mo-audit" }, entries.map(function (entry) {
            return h("div", { className: "mo-audit-row", key: entry.seq },
              h("div", { style: { minWidth: 0 } },
                h("div", { className: "mo-audit-kind" }, titleCase(entry.kind)),
                h("div", { className: "mo-audit-sub" }, entry.subject, " · ", entry.actor)),
              h("div", { className: "mo-audit-time" }, timeAgo(entry.at)));
          })),
          h("span", { className: "mo-hint" },
            "Every entry is hashed with the one before it, so a removed or edited entry shows up "
            + "as a broken chain."))
        : null);
  }

  function SourceStrip(props) {
    var health = props.health || [];
    return h("div", { className: "mo-sources" }, health.map(function (probe) {
      return h("span", {
        className: cx("mo-source", probe.ok ? "" : "down"), key: probe.id,
        title: probe.ok
          ? (probe.base_url || "") + " · " + (probe.latency_ms || 0) + " ms"
          : probe.error
      },
        h("span", { className: "mo-dot" }),
        h("strong", null, probe.label || probe.id),
        h("span", { className: "mo-src-meta" },
          probe.ok
            ? (probe.entities || []).length + " entities"
            : "unreachable"));
    }));
  }

  function Toasts(props) {
    if (!props.toasts.length) { return null; }
    return h("div", { className: "mo-toast-stack", role: "status", "aria-live": "polite" },
      props.toasts.map(function (toast) {
        return h("div", { className: cx("mo-toast", toast.tone), key: toast.id },
          h("div", { className: "mo-toast-title" }, toast.title),
          toast.body ? h("div", { className: "mo-toast-body" }, toast.body) : null);
      }));
  }
