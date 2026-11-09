
  /* ── panels ────────────────────────────────────────────────────────────────
   *
   * The approvals panel is the authority boundary made visible. It shows the
   * before/after the source application itself promised, who asked, and the risk —
   * then asks a person. Nothing else in MORPH can apply a change.
   */

  function diffRows(before, predicted) {
    return Object.keys(predicted || {}).map(function (key) {
      return {
        field: key,
        from: (before && key in before) ? before[key] : null,
        to: predicted[key]
      };
    });
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

    return h("div", { className: cx("mo-approval", "risk-" + proposal.risk) }, [
      h("div", { key: "op" }, [
        h("div", { className: "mo-approval-op", key: "a" },
          titleCase(proposal.operation_ref.split(".").pop())),
        h("div", { className: "mo-approval-meta", key: "b" },
          proposal.entity_ref + " · " + proposal.record_id)
      ]),
      h("div", { key: "diff", className: "mo-diff" },
        diffRows(proposal.before, proposal.predicted).map(function (row) {
          return h("div", { key: row.field }, [
            h("div", { className: "mo-diff-field", key: "f" }, titleCase(row.field)),
            h("div", { className: "mo-diff-row", key: "r" }, [
              h("span", { className: "mo-diff-from", key: "a" }, show(row.from)),
              h(Icon, { name: "arrow", size: 13, key: "i" }),
              h("span", { className: "mo-diff-to", key: "b" }, show(row.to))
            ])
          ]);
        })),
      h("div", { key: "meta", className: "mo-approval-meta" }, [
        "asked by " + proposal.requested_by + " (" + proposal.authority + ")",
        proposal.risk === "high"
          ? h("span", { key: "r" }, [" · ", h(Tag, { tone: "bad", key: "t" }, "high risk")])
          : null
      ]),
      proposal.reason
        ? h("div", { key: "why", className: "mo-approval-meta" }, "“" + proposal.reason + "”")
        : null,
      error[0] ? h("div", { key: "err", className: "mo-error", role: "alert" }, error[0]) : null,
      h("div", { key: "act", className: "mo-approval-actions" }, [
        h(Button, {
          key: "ok", variant: "primary", small: true, icon: "check", busy: busy[0],
          onClick: function () { run(function () { return props.onApprove(proposal); }); }
        }, "Approve & apply"),
        h(Button, {
          key: "no", variant: "danger", small: true, icon: "close", disabled: busy[0],
          onClick: function () { run(function () { return props.onReject(proposal); }); }
        }, "Reject")
      ]),
      h("span", { key: "note", className: "mo-approval-meta" },
        "Applying writes to " + proposal.source
          + ", then MORPH re-reads the record to verify it.")
    ]);
  }

  function ApprovalsPanel(props) {
    var pending = props.proposals || [];
    return h(Panel, {
      title: props.title || "Awaiting your approval",
      aside: pending.length
        ? h(Tag, {
            tone: pending.some(function (p) { return p.risk === "high"; }) ? "bad" : "info"
          }, String(pending.length))
        : null
    }, pending.length
      ? pending.map(function (proposal) {
        return h(ApprovalCard, {
          key: proposal.id, proposal: proposal,
          onApprove: props.onApprove, onReject: props.onReject
        });
      })
      : h(Empty, {
        icon: "shield", title: "Nothing is waiting",
        text: "Actions the agent or you propose appear here. MORPH never applies one on "
          + "its own."
      }));
  }

  function DecisionsPanel(props) {
    var decisions = props.decisions || [];
    if (!decisions.length) { return null; }
    return h(Panel, { title: props.title || "Recent decisions" },
      decisions.slice(0, props.limit || 6).map(function (item) {
        var toneName = item.status === "applied" ? "good"
          : (item.status === "rejected" ? "" : "bad");
        return h("div", { key: item.id, className: "mo-revision" }, [
          h(Tag, { key: "t", tone: toneName }, item.status),
          h("span", {
            key: "g", className: "mo-revision-goal", title: item.operation_ref
          }, item.operation_ref.split(".").slice(1).join(".") + " · " + item.record_id),
          item.status === "applied"
            ? h(Button, {
                key: "v", small: true, variant: "ghost", icon: "shield",
                title: "Re-read the record from its own application and verify",
                onClick: function () { props.onVerify(item); }
              }, "Verify")
            : null
        ]);
      }));
  }

  function RevisionsPanel(props) {
    var revisions = props.revisions || [];
    if (revisions.length < 2) { return null; }
    return h(Panel, {
      title: "Workspace history",
      aside: h("span", { className: "mo-card-sub" }, revisions.length + " revisions")
    }, h("div", { className: "mo-revisions" }, revisions.slice(0, 10).map(function (revision) {
      var isCurrent = revision.revision === props.current;
      return h("div", { className: "mo-revision", key: revision.revision }, [
        h("span", { key: "n", className: "mo-revision-n" }, "r" + revision.revision),
        h("span", { key: "g", className: "mo-revision-goal", title: revision.goal },
          revision.goal),
        isCurrent
          ? h(Tag, { key: "c", tone: "info" }, "current")
          : h(Button, {
              key: "r", small: true, variant: "ghost", icon: "history",
              onClick: function () { props.onRestore(revision.revision); }
            }, "Restore")
      ]);
    })));
  }

  function evidenceOf(entry) {
    var payload = entry.payload || {};
    var lines = [];
    if (payload.record) { lines.push(["record", payload.record]); }
    if (payload.params && Object.keys(payload.params).length) {
      lines.push(["parameters", JSON.stringify(payload.params)]);
    }
    if (payload.changed && Object.keys(payload.changed).length) {
      lines.push(["changed", Object.keys(payload.changed).map(function (k) {
        return k + " = " + show(payload.changed[k]);
      }).join(", ")]);
    }
    if ("verified" in payload) { lines.push(["verified", payload.verified ? "yes" : "NO"]); }
    if (payload.mismatches && payload.mismatches.length) {
      lines.push(["mismatch", payload.mismatches.map(function (m) {
        return m.field + ": expected " + show(m.expected) + ", found " + show(m.actual);
      }).join("; ")]);
    }
    if (payload.error) { lines.push(["error", payload.error]); }
    if (payload.reason) { lines.push(["reason", payload.reason]); }
    return lines;
  }

  function AuditFeed(props) {
    var entries = props.entries || [];
    if (!entries.length) {
      return h(Empty, { icon: "history", title: "Nothing has happened yet" });
    }
    return h("div", { className: "mo-audit" }, entries.map(function (entry) {
      var lines = props.withEvidence ? evidenceOf(entry) : [];
      return h("div", { className: "mo-audit-row", key: entry.seq }, [
        h("div", { key: "b", style: { minWidth: 0, flex: 1 } }, [
          h("div", { className: "mo-audit-kind", key: "k" }, titleCase(entry.kind)),
          h("div", { className: "mo-audit-sub", key: "s" },
            entry.subject + " · " + entry.actor + " (" + entry.authority + ")"),
          lines.length
            ? h("div", { className: "mo-evidence", key: "e" }, lines.map(function (line, i) {
              return h("div", { key: i }, [
                h("b", { key: "k" }, line[0] + ": "), String(line[1])
              ]);
            }))
            : null
        ]),
        h("div", { className: "mo-audit-time", key: "t" }, timeAgo(entry.at))
      ]);
    }));
  }

  function SourceStrip(props) {
    var health = props.health || [];
    return h("div", { className: "mo-sources" }, health.map(function (probe) {
      return h("span", {
        className: cx("mo-source", probe.ok ? "" : "down"), key: probe.id,
        title: probe.ok
          ? (probe.base_url || "") + " · " + (probe.latency_ms || 0) + " ms"
          : probe.error
      }, [
        h("span", { className: "mo-dot", key: "d" }),
        h("strong", { key: "n" }, probe.label || probe.id),
        h("span", { className: "mo-src-meta", key: "m" },
          probe.ok ? (probe.entities || []).length + " entities" : "unreachable")
      ]);
    }));
  }

  function Toasts(props) {
    if (!props.toasts.length) { return null; }
    return h("div", { className: "mo-toast-stack", role: "status", "aria-live": "polite" },
      props.toasts.map(function (toast) {
        return h("div", { className: cx("mo-toast", toast.tone), key: toast.id }, [
          h("div", { className: "mo-toast-title", key: "t" }, toast.title),
          toast.body ? h("div", { className: "mo-toast-body", key: "b" }, toast.body) : null
        ]);
      }));
  }
