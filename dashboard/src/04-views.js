
  /* ── the five surfaces ─────────────────────────────────────────────────────
   *
   * Workspace, Connections, Approvals, Activity, Context. They are separate views
   * because they answer separate questions — "what am I working on", "what am I
   * connected to", "what needs me", "what happened", "what does MORPH think I asked
   * for" — and mixing them into one page is how operations tools become unreadable.
   */

  function ActionDialog(props) {
    var operation = props.request.operation;
    var values = useState({});
    var busy = useState(false);
    var error = useState("");
    var params = operation.params || [];

    function update(name, value) {
      var next = {};
      for (var key in values[0]) { next[key] = values[0][key]; }
      next[name] = value;
      values[1](next);
    }

    function submit(event) {
      event.preventDefault();
      error[1]("");
      busy[1](true);
      props.onPropose({
        source: props.request.entity.split(".")[0],
        entity: props.request.entity.split(".")[1],
        record_id: props.request.record_id,
        operation: operation.id,
        params: values[0],
        reason: "Proposed from the workspace"
      }).then(function () { props.onClose(); })
        .catch(function (err) { error[1](errorText(err)); })
        .then(function () { busy[1](false); });
    }

    return h("div", {
      style: {
        position: "fixed", inset: 0, zIndex: 70, display: "grid", placeItems: "center",
        background: "rgba(16,17,19,.46)", padding: "16px"
      },
      onClick: function (event) { if (event.target === event.currentTarget) { props.onClose(); } }
    }, h("div", {
      className: "mo-panel", role: "dialog", "aria-modal": "true",
      "aria-label": operation.label,
      style: { width: "min(470px, 100%)", boxShadow: "var(--mo-shadow-lift)" }
    }, [
      h("div", { className: "mo-panel-head", key: "head" }, [
        h("h2", { className: "mo-panel-title", key: "t" }, operation.label),
        h(Button, {
          key: "x", small: true, variant: "ghost", icon: "close", onClick: props.onClose
        }, "Close")
      ]),
      h("form", { className: "mo-panel-body", onSubmit: submit, key: "form" }, [
        h("div", { className: "mo-target", key: "t" }, [
          h(Icon, { name: "pin", size: 14, key: "i" }),
          props.request.entity + " · ",
          h("strong", { key: "s" }, props.request.record_id)
        ]),
        operation.description
          ? h("span", { key: "d", className: "mo-hint" }, operation.description) : null,
        Object.keys(operation.effect || {}).length
          ? h("div", { key: "fx", className: "mo-diff" },
            Object.keys(operation.effect).map(function (field) {
              var promised = operation.effect[field];
              var isParam = typeof promised === "string" && /^\{[a-z_]+\}$/.test(promised);
              return h("div", { key: field }, [
                h("div", { className: "mo-diff-field", key: "f" }, titleCase(field)),
                h("div", { className: "mo-field-value", key: "v" },
                  isParam ? "from the field below" : show(promised))
              ]);
            }))
          : null,
        params.map(function (param) {
          return h(ParamField, {
            key: param.name, param: param, idPrefix: "mo-dialog",
            value: values[0][param.name], onChange: update
          });
        }),
        error[0] ? h("div", { key: "e", className: "mo-error", role: "alert" }, error[0]) : null,
        h("div", { key: "a", className: "mo-approval-actions" }, [
          h(Button, {
            key: "go", type: "submit", variant: "primary", icon: "shield", busy: busy[0]
          }, "Propose for approval"),
          h(Button, { key: "no", variant: "ghost", onClick: props.onClose }, "Cancel")
        ]),
        h("span", { key: "h", className: "mo-hint" },
          "This queues the action. You approve it in the Approvals view.")
      ])
    ]));
  }

  /* ── workspace ──────────────────────────────────────────────────────────── */

  var SUGGESTIONS = [
    "Work through delayed orders and the complaints about them",
    "Show stock running low and the restocks I should request",
    "Escalated support cases and the orders behind them",
    "Compare order value by status",
    "Monitor shipments running late"
  ];

  function WorkspaceView(props) {
    var state = props.state;
    var workspace = state && state.workspace;
    var schema = (workspace && workspace.schema) || null;
    var blocks = (state && state.data && state.data.blocks) || {};
    var health = (state && state.health) || [];
    var reachable = health.filter(function (probe) { return probe.ok; }).length;
    var pending = (state && state.proposals) || [];

    return h("div", null, [
      h("div", { className: "mo-goal", key: "goal" }, [
        h("label", { className: "mo-goal-label", htmlFor: "mo-goal-input", key: "l" }, [
          "What do you need to get done?",
          h("span", { className: "mo-kbd", key: "k" }, "Ctrl K")
        ]),
        h("div", { className: "mo-goal-row", key: "r" }, [
          h("input", {
            key: "i", id: "mo-goal-input", className: "mo-goal-input", value: props.goal,
            placeholder: "e.g. work through delayed orders and the complaints about them",
            maxLength: 400, autoComplete: "off", ref: props.goalRef,
            onChange: function (event) { props.onGoalChange(event.target.value); },
            onKeyDown: function (event) { if (event.key === "Enter") { props.onCompose(); } }
          }),
          h(Button, {
            key: "b", variant: "primary", icon: "spark", busy: props.morphing,
            disabled: props.goal.trim().length < 3,
            onClick: function () { props.onCompose(); }
          }, workspace ? "Transform workspace" : "Compose workspace")
        ]),
        h("div", { className: "mo-suggestions", key: "s" }, SUGGESTIONS.map(function (text) {
          return h("button", {
            type: "button", className: "mo-chip", key: text,
            onClick: function () { props.onCompose(text); }
          }, text);
        })),
        h(SourceStrip, { key: "src", health: health })
      ]),

      !reachable
        ? h(Notice, { key: "down", tone: "bad", title: "No connected application is answering" },
          "MORPH composes from live application schemas, so it has nothing to build from. "
          + "Start the bundled demo services with `python -m apps.serve`, or point MORPH at "
          + "your own services (docs/CONNECTORS.md).")
        : null,

      (workspace && (workspace.intent || {}).missing || []).length
        ? h(Notice, { key: "miss", tone: "warn", icon: "compass",
                      title: "What MORPH could not cover" },
          (workspace.intent.missing || []).join(" · "))
        : null,

      (state && state.data && state.data.degraded)
        ? h(Notice, { key: "deg", tone: "warn", title: "Showing a partial workspace" },
          "Some blocks could not be resolved: "
          + (((state.data.failures) || []).map(function (f) {
            return f.source + " (" + f.error + ")";
          }).join("; ") || "one or more applications failed")
          + ". Nothing here is filled in from a cache.")
        : null,

      (workspace && workspace.schema_error)
        ? h(Notice, { key: "stale", tone: "warn",
                      title: "This workspace no longer matches the applications" },
          workspace.schema_error + " — set a goal again to recompose it.")
        : null,

      pending.length
        ? h("div", { key: "pend", className: "mo-notice", style: { alignItems: "center" } }, [
            h(Icon, { key: "i", name: "shield", size: 16 }),
            h("div", { key: "t", style: { flex: 1 } },
              pending.length + " action" + (pending.length === 1 ? "" : "s")
              + " waiting for your approval"),
            h(Button, {
              key: "b", small: true, icon: "arrow",
              onClick: function () { props.onNavigate("approvals"); }
            }, "Review")
          ])
        : null,

      props.morphing
        ? h("div", { key: "banner", className: "mo-morph-banner" }, [
            h(Spinner, { key: "s" }),
            "Rethinking the workspace for your new goal — your selection is carried over."
          ])
        : null,

      schema
        ? h("div", {
            key: "stage-" + workspace.revision,
            className: cx("mo-stage", props.morphing ? "morphing" : "")
          }, (schema.sections || []).map(function (section) {
            return h("section", { className: "mo-section", key: section.id }, [
              h("div", { className: "mo-section-head", key: "h" }, [
                h("h2", { className: "mo-section-title", key: "t" }, section.title),
                section.description
                  ? h("p", { className: "mo-section-note", key: "d" }, section.description)
                  : null
              ]),
              h("div", { className: "mo-grid", key: "g" },
                (section.blocks || []).map(function (block) {
                  return h(Block, {
                    key: block.id, block: block, data: blocks[block.id],
                    entityFields: props.entityFields[block.entity] || [],
                    selection: props.selection, allowed: props.allowed,
                    denyReason: props.denyReason, onSelect: props.onSelect,
                    onPropose: props.onPropose, onAct: props.onAct
                  });
                }))
            ]);
          }))
        : h(Empty, {
            key: "empty", icon: "spark", title: "No workspace yet",
            text: "Describe what you need above and MORPH will compose one from the "
              + "applications it can reach."
          })
    ]);
  }

  /* ── connections ────────────────────────────────────────────────────────── */

  function ConnectionsView(props) {
    var state = props.state || {};
    var graph = state.graph || {};
    var health = {};
    (state.health || []).forEach(function (probe) { health[probe.id] = probe; });
    var permissions = state.permissions || [];
    var links = (graph.links || []).filter(function (link) {
      return link.cross_application && link.name.indexOf("_reverse") < 0;
    });

    return h("div", { className: "mo-stack" }, [
      h("div", { className: "mo-grid", key: "apps" }, (graph.sources || []).map(function (source) {
        var probe = health[source.id] || {};
        var entities = source.entities || [];
        return h("section", { className: "mo-card mo-span-lg", key: source.id }, [
          h("div", { className: "mo-card-head", key: "h" }, [
            h("div", { key: "t", style: { minWidth: 0 } }, [
              h("h3", { className: "mo-card-title", key: "a" }, source.label || source.id),
              h("p", { className: "mo-card-sub", key: "b" },
                (source.vendor ? source.vendor + " · " : "") + (source.description || ""))
            ]),
            h(Tag, { key: "s", tone: probe.ok ? "good" : "bad" },
              probe.ok ? "connected" : "unreachable")
          ]),
          h("div", { className: "mo-card-body", key: "b" }, [
            h("div", { key: "meta", className: "mo-card-sub" }, [
              source.kind, " · ", probe.base_url || "in process",
              probe.ok ? " · " + (probe.latency_ms >= 1
                ? Math.round(probe.latency_ms) + " ms" : "<1 ms") : "",
              probe.connect_guard ? " · connect guard on" : ""
            ]),
            probe.error
              ? h("div", { key: "err", className: "mo-error", style: { marginTop: "8px" } },
                probe.error)
              : null,
            h("div", { className: "mo-conn-entities", key: "ents" },
              entities.map(function (entity) {
                return h("div", { className: "mo-conn-entity", key: entity.name }, [
                  h("b", { key: "n" }, source.id + "." + entity.name),
                  h("span", { key: "c" },
                    (entity.fields || []).length + " fields · "
                    + (entity.operations || []).length + " operations")
                ]);
              }))
          ])
        ]);
      })),

      (graph.unavailable || []).length
        ? h(Notice, { key: "miss", tone: "bad", title: "Configured but not reachable" },
          (graph.unavailable || []).map(function (entry) {
            return entry.id + ": " + entry.error;
          }).join(" · "))
        : null,

      h(Panel, {
        key: "links", title: "Cross-application relationships",
        aside: h("span", { className: "mo-card-sub" }, links.length + " declared")
      }, links.length
        ? h("div", { className: "mo-links" }, links.map(function (link) {
          return h("div", { className: "mo-link-row", key: link.name }, [
            h("span", { className: "mo-link-side", key: "a" }, link.from),
            h(Icon, { name: "arrow", size: 14, key: "i" }),
            h("span", { className: "mo-link-side", key: "b" }, link.to),
            h(Tag, { key: "c", tone: "info" }, link.cardinality)
          ]);
        }))
        : h(Empty, {
          icon: "link", title: "No cross-application links yet",
          text: "Declare them under `links` in your MORPH configuration — no application "
            + "can know about another."
        })),

      h(Panel, {
        key: "perms", title: "What you may do",
        aside: h("span", { className: "mo-card-sub" },
          permissions.filter(function (p) { return p.allowed; }).length + " of "
          + permissions.length + " allowed")
      }, permissions.map(function (entry) {
        return h("div", { className: "mo-perm", key: entry.ref }, [
          h(Tag, { key: "t", tone: entry.allowed ? "good" : "" },
            entry.allowed ? "allowed" : "denied"),
          h("span", { className: "mo-perm-ref", key: "r" }, entry.ref),
          h(Tag, {
            key: "k", tone: entry.risk === "high" ? "bad" : (entry.risk === "medium" ? "warn" : "")
          }, entry.risk + " risk"),
          entry.reason ? h("span", { className: "mo-perm-why", key: "w" }, entry.reason) : null
        ]);
      })),

      h(Panel, { key: "cfg", title: "Configuration" },
        h("div", { className: "mo-card-sub" }, [
          "Loaded from ", h("b", { key: "b" }, state.config_source || "unknown"), ". ",
          "MORPH stores no business records: only workspaces, proposals and the audit log."
        ]))
    ]);
  }

  /* ── approvals ──────────────────────────────────────────────────────────── */

  function ApprovalsView(props) {
    var state = props.state || {};
    return h("div", { className: "mo-stack" }, [
      h(ApprovalsPanel, {
        key: "p", proposals: state.proposals || [],
        onApprove: props.onApprove, onReject: props.onReject
      }),
      h(DecisionsPanel, {
        key: "d", title: "Decided", decisions: state.recent_decisions || [],
        limit: 20, onVerify: props.onVerify
      }),
      h(Panel, { key: "how", title: "Why this step exists" },
        h("div", { className: "mo-card-sub" },
          "The agent can propose an action; it is never handed the confirmation token an "
          + "approval needs, and a hook blocks any tool in the morph_ namespace it did not "
          + "register. Approving here writes to the source application and then re-reads "
          + "the record to check the change actually landed."))
    ]);
  }

  /* ── activity ───────────────────────────────────────────────────────────── */

  function ActivityView(props) {
    var state = props.state || {};
    var chain = state.audit_chain || {};
    var filter = useState("all");
    var entries = (state.audit || []).filter(function (entry) {
      if (filter[0] === "all") { return true; }
      if (filter[0] === "actions") {
        return ["proposed", "applied", "applied_unverified", "rejected",
                "approval_failed", "approval_stale", "approval_refused"]
          .indexOf(entry.kind) >= 0;
      }
      return entry.kind === "tool_call";
    });
    return h("div", { className: "mo-stack" }, [
      h(Notice, {
        key: "chain", tone: chain.ok ? "good" : "bad", icon: "shield",
        title: chain.ok ? "Audit chain intact" : "Audit chain broken"
      }, chain.ok
        ? (chain.entries || 0) + " entries, each hashed with the one before it. A removed or "
          + "edited entry would show up here."
        : "The chain does not verify from entry " + chain.broken_at
          + " onwards. Treat the log as untrustworthy from that point."),
      h(Panel, {
        key: "feed", title: "Activity",
        aside: h("div", { className: "mo-seg" }, [
          ["all", "All"], ["actions", "Actions"], ["tool_call", "Agent reads"]
        ].map(function (option) {
          return h("button", {
            key: option[0], type: "button", "aria-pressed": filter[0] === option[0],
            onClick: function () { filter[1](option[0]); }
          }, option[1]);
        }))
      }, h(AuditFeed, { entries: entries, withEvidence: true }))
    ]);
  }

  /* ── context ────────────────────────────────────────────────────────────── */

  function ContextView(props) {
    var state = props.state || {};
    var workspace = state.workspace || {};
    var intent = workspace.intent || {};
    var context = workspace.context || {};
    var selection = context.selection || {};
    var pinned = context.pinned_filters || {};

    return h("div", { className: "mo-stack" }, [
      h(Panel, {
        key: "goal", title: "Current objective",
        aside: workspace.revision
          ? h(Tag, { tone: "info" }, "revision " + workspace.revision) : null
      }, [
        h("div", { key: "g", style: { fontSize: "15px", fontWeight: 600 } },
          workspace.goal || "No goal set yet"),
        h("div", { key: "m", className: "mo-card-sub" },
          "Planned " + (workspace.mode === "deterministic"
            ? "deterministically, with no model involved"
            : "by the host model (" + workspace.mode + ")")),
        (workspace.notes || []).length
          ? h("div", { key: "n", className: "mo-evidence" },
            (workspace.notes || []).map(function (note, i) {
              return h("div", { key: i }, note);
            }))
          : null
      ]),

      intent.mode
        ? h(Panel, {
            key: "intent", title: "How MORPH read that",
            aside: h(Tag, { tone: "info" }, intent.mode)
          }, [
            h("div", { key: "f", className: "mo-links" },
              (intent.focus || []).map(function (entry) {
                return h("div", { className: "mo-link-row", key: entry.entity }, [
                  h("span", { className: "mo-link-side", key: "e" }, entry.entity),
                  h("span", { className: "mo-card-sub", key: "w" }, entry.why),
                  entry.named ? h(Tag, { key: "n", tone: "good" }, "named") : null
                ]);
              })),
            (intent.operations || []).length
              ? h("div", { key: "o", className: "mo-card-sub", style: { marginTop: "8px" } },
                "Candidate operations: " + (intent.operations || []).join(", "))
              : null,
            (intent.missing || []).length
              ? h(Notice, { key: "m", tone: "warn", icon: "compass",
                            title: "What it could not cover" },
                (intent.missing || []).join(" · "))
              : null
          ])
        : null,

      h(Panel, { key: "sel", title: "Carried context" }, [
        Object.keys(selection).length
          ? h("div", { key: "s", className: "mo-links" },
            Object.keys(selection).map(function (ref) {
              return h("div", { className: "mo-link-row", key: ref }, [
                h("span", { className: "mo-link-side", key: "a" }, ref),
                h("strong", { key: "b" }, selection[ref]),
                h(Button, {
                  key: "c", small: true, variant: "ghost", icon: "close",
                  onClick: function () { props.onClearContext(ref); }
                }, "Clear")
              ]);
            }))
          : h("div", { key: "s", className: "mo-card-sub" }, "Nothing selected."),
        Object.keys(pinned).length
          ? h("div", { key: "p", style: { marginTop: "10px" } },
            Object.keys(pinned).map(function (ref) {
              return h("div", { className: "mo-link-row", key: ref }, [
                h("span", { className: "mo-link-side", key: "a" }, ref),
                h("span", { className: "mo-card-sub", key: "b" },
                  pinned[ref].map(function (f) {
                    return f.field + " " + f.op + " " + show(
                      Array.isArray(f.value) ? f.value.join(", ") : f.value);
                  }).join(" · "))
              ]);
            }))
          : null,
        h("span", { key: "h", className: "mo-hint", style: { marginTop: "8px" } },
          "A selection and a pinned filter survive a transformation wherever the new "
          + "workspace still shows that entity.")
      ]),

      h(RevisionsPanel, {
        key: "rev", revisions: state.revisions || [],
        current: workspace.revision, onRestore: props.onRestore
      }),

      h(Panel, { key: "who", title: "You" },
        h("div", { className: "mo-card-sub" }, state.principal
          ? [
              h("b", { key: "a" }, state.principal.display), " · role ",
              h("b", { key: "b" }, state.principal.role_label || state.principal.role),
              " · may approve: ", state.principal.can_approve ? "yes" : "no",
              " · risk ceiling ", state.principal.max_risk
            ]
          : "unknown"))
    ]);
  }
